"""Store uploaded images and drop the ones we no longer keep.

A new upload keeps a file only when both are true: it is among the newest
``KEEP_NEWEST`` uploads, and it was uploaded within ``MAX_AGE_SECONDS``.
Other generated upload files are deleted, including their metadata, so old
URLs stop resolving. Unrelated files in the directory are left alone.
"""

import io
import json
import logging
import math
import os
import re
import secrets
import struct
import threading
import time
import warnings
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

logger = logging.getLogger("uvicorn")

KEEP_NEWEST = 10
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
# Per-frame pixel cap. Pillow raises DecompressionBombError above twice this.
MAX_INPUT_PIXELS = 50_000_000
MAX_FRAMES = 200
# 100 frames of 4000x2250 is 900MP and still allowed; larger jobs are rejected.
MAX_TOTAL_PIXELS = 1_000_000_000
META_NAME = "index.json"
PRESERVE_NAMES = {META_NAME, ".gitkeep"}
NAME_RE_SOURCE = r"[0-9a-f]{32}\.(?:jpg|png|gif|webp)"

Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)

_KIND_EXT = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}
_DECLARED_KIND = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ImageTooLarge(ValueError):
    """The image has too many pixels or too many frames to store."""


@dataclass(frozen=True, slots=True)
class UploadRecord:
    name: str
    uploaded_at: float


def default_upload_dir() -> Path:
    return Path(os.environ.get("UPLOAD_DIR", "uploads"))


def sniff_image(data: bytes) -> str | None:
    """Return the image kind from magic bytes, or None.

    Examples
    --------
    >>> sniff_image(b"\\x89PNG\\r\\n\\x1a\\nrest")
    'png'
    >>> sniff_image(b"\\xff\\xd8\\xff\\xe0rest")
    'jpeg'
    >>> sniff_image(b"GIF89arest")
    'gif'
    >>> sniff_image(b"RIFF\\x00\\x00\\x00\\x00WEBPrest")
    'webp'
    >>> sniff_image(b"GIF87a")
    'gif'
    >>> sniff_image(b"\\xff\\xd8\\xff")
    'jpeg'
    >>> sniff_image(b"\\xff\\xd8\\x00") is None
    True
    >>> sniff_image(b"\\xff\\xd8") is None
    True
    >>> sniff_image(b"not an image") is None
    True
    >>> sniff_image(b"") is None
    True
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def detect_image(data: bytes, content_type: str | None) -> str | None:
    """Accept a payload only when the bytes are an image.

    A specific ``image/*`` content type must agree with the magic bytes.
    Missing and ``application/octet-stream`` types are decided by the bytes.
    Any other declared type is rejected.

    Examples
    --------
    >>> detect_image(b"\\xff\\xd8\\xff\\xe0", "image/jpeg")
    'jpeg'
    >>> detect_image(b"\\x89PNG\\r\\n\\x1a\\n", "image/png; charset=binary")
    'png'
    >>> detect_image(b"GIF89a", None)
    'gif'
    >>> detect_image(b"RIFF\\x00\\x00\\x00\\x00WEBP", "application/octet-stream")
    'webp'
    >>> detect_image(b"\\xff\\xd8\\xff", "image/png") is None
    True
    >>> detect_image(b"\\xff\\xd8\\xff", "text/html") is None
    True
    >>> detect_image(b"hello", "image/png") is None
    True
    >>> detect_image(b"<svg></svg>", "image/svg+xml") is None
    True
    """
    kind = sniff_image(data)
    if kind is None:
        return None
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in ("", "application/octet-stream"):
        return kind
    if _DECLARED_KIND.get(declared) != kind:
        return None
    return kind


def fit_within(
    width: int,
    height: int,
    *,
    max_width: int = MAX_WIDTH,
    max_height: int = MAX_HEIGHT,
) -> tuple[int, int]:
    """Scale a size so it fits inside ``max_width`` x ``max_height``.

    Aspect ratio is preserved. Sizes that already fit are unchanged. Rounding
    matches the browser (half up) and the result never exceeds the box.

    Examples
    --------
    >>> fit_within(3840, 2160)
    (1920, 1080)
    >>> fit_within(4000, 1000)
    (1920, 480)
    >>> fit_within(1000, 4000)
    (270, 1080)
    >>> fit_within(3000, 2000)
    (1620, 1080)
    >>> fit_within(800, 600)
    (800, 600)
    >>> fit_within(1920, 1080)
    (1920, 1080)
    >>> fit_within(1920, 1081)
    (1918, 1080)
    >>> fit_within(1, 10000)
    (1, 1080)
    >>> fit_within(3840, 2001)
    (1920, 1001)
    >>> fit_within(4000, 2160)
    (1920, 1037)
    """
    if width < 1 or height < 1:
        raise ValueError("Image dimensions must be positive")
    if width <= max_width and height <= max_height:
        return width, height
    scale = min(max_width / width, max_height / height)
    fitted_width = min(max_width, max(1, math.floor(width * scale + 0.5)))
    fitted_height = min(max_height, max(1, math.floor(height * scale + 0.5)))
    return fitted_width, fitted_height


def downscale_image(data: bytes, kind: str) -> bytes:
    """Return image bytes that fit inside 1920x1080, without source metadata.

    EXIF orientation is applied, then the image is re-encoded so GPS and other
    metadata are not stored. Animations that pass the frame cap keep their
    frames. Anything larger than Full HD is resized with the same fit the
    browser uses.

    Examples
    --------
    >>> import io
    >>> from PIL import Image
    >>> from PIL.ExifTags import IFD
    >>> photo = Image.new("RGB", (800, 600), (1, 2, 3))
    >>> exif = Image.Exif()
    >>> exif.get_ifd(IFD.GPSInfo)[2] = (35.0, 41.0, 0.0)
    >>> original = io.BytesIO()
    >>> photo.save(original, format="JPEG", exif=exif, quality=90)
    >>> stored = Image.open(io.BytesIO(downscale_image(original.getvalue(), "jpeg")))
    >>> stored.size == (800, 600) and not stored.getexif().get_ifd(IFD.GPSInfo)
    True
    >>> big = io.BytesIO()
    >>> Image.new("RGB", (3840, 2160), (9, 9, 9)).save(big, format="PNG")
    >>> fitted = Image.open(io.BytesIO(downscale_image(big.getvalue(), "png")))
    >>> fitted.size
    (1920, 1080)
    >>> wide = io.BytesIO()
    >>> Image.new("RGB", (4000, 1000), (4, 4, 4)).save(wide, format="PNG")
    >>> Image.open(io.BytesIO(downscale_image(wide.getvalue(), "png"))).size
    (1920, 480)
    >>> frame_a = Image.new("RGB", (16, 16), (255, 0, 0))
    >>> frame_b = Image.new("RGB", (16, 16), (0, 0, 255))
    >>> gif = io.BytesIO()
    >>> frame_a.save(gif, format="GIF", save_all=True, append_images=[frame_b], duration=80, loop=0)
    >>> animated = Image.open(io.BytesIO(downscale_image(gif.getvalue(), "gif")))
    >>> animated.size == (16, 16) and animated.n_frames == 2
    True
    >>> downscale_image(_png_claiming(8000, 8000), "png")
    Traceback (most recent call last):
    ...
    uploads.ImageTooLarge: Image is too large
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            _reject_input(image)
            orientation = image.getexif().get(274) or 1
            if orientation not in (0, 1):
                image = ImageOps.exif_transpose(image)
                _reject_input(image)
            target = fit_within(image.width, image.height)
            encoded = _encode_fitted(image, target, kind)
    except Image.DecompressionBombError as exc:
        raise ImageTooLarge("Image is too large") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Could not read image") from exc
    _ensure_fits(encoded)
    return encoded


def _png_claiming(width: int, height: int) -> bytes:
    """PNG header that claims ``width`` x ``height`` without a real bitmap."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + chunk(b"IEND", b"")
    )


def _reject_input(image: Image.Image) -> None:
    """Reject dimensions and frame counts before pixel data is expanded.

    Examples
    --------
    >>> class _Fake:
    ...     size = (13000, 13000)
    ...     n_frames = 1
    >>> _reject_input(_Fake())
    Traceback (most recent call last):
    ...
    uploads.ImageTooLarge: Image is too large
    >>> class _Gif:
    ...     size = (4000, 2250)
    ...     n_frames = 100
    >>> _reject_input(_Gif())
    >>> class _Many:
    ...     size = (100, 100)
    ...     n_frames = 201
    >>> _reject_input(_Many())
    Traceback (most recent call last):
    ...
    uploads.ImageTooLarge: Image is too large
    """
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("Image dimensions must be positive")
    frames = int(getattr(image, "n_frames", 1) or 1)
    if (
        width * height > MAX_INPUT_PIXELS
        or frames > MAX_FRAMES
        or frames * width * height > MAX_TOTAL_PIXELS
    ):
        raise ImageTooLarge("Image is too large")


def _encode_fitted(image: Image.Image, target: tuple[int, int], kind: str) -> bytes:
    frames = getattr(image, "n_frames", 1) or 1
    if frames > 1 and kind in {"gif", "webp"}:
        try:
            return _encode_animation(image, target, kind)
        except OSError:
            logger.warning("Animated downscale failed; storing the first frame")
    return _encode_still(image, target, kind)


def _encode_still(image: Image.Image, target: tuple[int, int], kind: str) -> bytes:
    working = image
    if working.size != target:
        working = working.resize(target, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    if kind == "jpeg":
        if working.mode != "RGB":
            working = working.convert("RGB")
        working.save(buffer, format="JPEG", quality=90, optimize=True)
    elif kind == "png":
        if working.mode not in ("RGB", "RGBA", "L", "LA"):
            bands = working.getbands()
            working = working.convert("RGBA" if "A" in bands else "RGB")
        working.save(buffer, format="PNG", optimize=True)
    elif kind == "gif":
        working = working.convert("P", palette=Image.Palette.ADAPTIVE)
        working.save(buffer, format="GIF")
    elif kind == "webp":
        if working.mode not in ("RGB", "RGBA"):
            bands = working.getbands()
            working = working.convert("RGBA" if "A" in bands else "RGB")
        working.save(buffer, format="WEBP", quality=90)
    else:
        raise ValueError(f"Unsupported image kind: {kind}")
    return buffer.getvalue()


def _encode_animation(image: Image.Image, target: tuple[int, int], kind: str) -> bytes:
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(image):
        frame = frame.copy()
        if frame.size != target:
            frame = frame.resize(target, Image.Resampling.LANCZOS)
        frames.append(frame)
        durations.append(int(frame.info.get("duration", image.info.get("duration", 100))))
    if not frames:
        raise ValueError("Image has no frames")
    buffer = io.BytesIO()
    save_kwargs = {
        "save_all": True,
        "append_images": frames[1:],
        "duration": durations,
        "loop": image.info.get("loop", 0),
    }
    if kind == "gif":
        frames[0].save(buffer, format="GIF", **save_kwargs)
    else:
        frames[0].save(buffer, format="WEBP", quality=90, **save_kwargs)
    return buffer.getvalue()


def _ensure_fits(data: bytes) -> None:
    """Reject an encoded image that is outside the Full HD box.

    Examples
    --------
    >>> import io
    >>> from PIL import Image
    >>> ok = io.BytesIO()
    >>> Image.new("RGB", (1920, 1080), (1, 1, 1)).save(ok, format="PNG")
    >>> _ensure_fits(ok.getvalue())
    >>> over = io.BytesIO()
    >>> Image.new("RGB", (1921, 1080), (1, 1, 1)).save(over, format="PNG")
    >>> _ensure_fits(over.getvalue())
    Traceback (most recent call last):
    ...
    uploads.ImageTooLarge: Image is 1921x1080 after downscale
    """
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    if width < 1 or height < 1 or width > MAX_WIDTH or height > MAX_HEIGHT:
        raise ImageTooLarge(f"Image is {width}x{height} after downscale")


def content_type_for(name: str) -> str:
    return _MEDIA_TYPES[Path(name).suffix.lower()]


def public_stream_url(base: str, name: str) -> str:
    """URL that plays the upload through the existing image route.

    Examples
    --------
    >>> from urllib.parse import quote
    >>> name = "ab" * 16 + ".png"
    >>> base = "http://127.0.0.1:8080"
    >>> image = base + "/video/uploads/" + name
    >>> public_stream_url(base, name) == base + "/video?url=" + quote(image, safe="")
    True
    """
    image_url = base.rstrip("/") + "/video/uploads/" + name
    return base.rstrip("/") + "/video?url=" + quote(image_url, safe="")


def select_retained(
    records: list[UploadRecord],
    *,
    now: float,
    keep_newest: int = KEEP_NEWEST,
    max_age_seconds: float = MAX_AGE_SECONDS,
) -> list[UploadRecord]:
    """Return uploads that are both recent and among the newest ``keep_newest``.

    ``uploaded_at`` is a Unix timestamp. A record uploaded exactly
    ``max_age_seconds`` ago is kept. Equal timestamps break ties by name,
    descending, so the result is stable. The returned list is newest first.

    Examples
    --------
    Twelve uploads one second apart. Only the newest 10 are kept:

    >>> now = 1_700_000_000
    >>> recent = [UploadRecord(f"n{i:02d}.jpg", now - (11 - i)) for i in range(12)]
    >>> [r.name for r in select_retained(recent, now=now)]
    ['n11.jpg', 'n10.jpg', 'n09.jpg', 'n08.jpg', 'n07.jpg', 'n06.jpg', 'n05.jpg', 'n04.jpg', 'n03.jpg', 'n02.jpg']

    Files older than 7 days are dropped even when they sit inside the newest 10:

    >>> day = 86400
    >>> mixed = [UploadRecord(f"d{i}.jpg", now - i * day) for i in range(12)]
    >>> [r.name for r in select_retained(mixed, now=now)]
    ['d0.jpg', 'd1.jpg', 'd2.jpg', 'd3.jpg', 'd4.jpg', 'd5.jpg', 'd6.jpg', 'd7.jpg']

    The 7-day boundary is inclusive. One second older is not kept:

    >>> edge = [
    ...     UploadRecord("fresh.jpg", now),
    ...     UploadRecord("edge.jpg", now - 7 * day),
    ...     UploadRecord("stale.jpg", now - 7 * day - 1),
    ... ]
    >>> [r.name for r in select_retained(edge, now=now)]
    ['fresh.jpg', 'edge.jpg']

    Fewer than 10 recent files are all kept:

    >>> [r.name for r in select_retained([UploadRecord("only.jpg", now)], now=now)]
    ['only.jpg']
    """
    ranked = sorted(records, key=lambda record: (record.uploaded_at, record.name), reverse=True)
    cutoff = now - max_age_seconds
    kept: list[UploadRecord] = []
    for record in ranked[:keep_newest]:
        if record.uploaded_at >= cutoff:
            kept.append(record)
    return kept


def _safe_name(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    if re.fullmatch(NAME_RE_SOURCE, name) is None:
        return None
    return name


def apply_retention(
    directory: Path,
    records: list[UploadRecord],
    *,
    now: float,
    keep_newest: int = KEEP_NEWEST,
    max_age_seconds: float = MAX_AGE_SECONDS,
) -> list[UploadRecord]:
    """Delete files and metadata outside the retention window.

    Examples
    --------
    >>> import json, tempfile
    >>> from pathlib import Path
    >>> now = 1_700_000_000
    >>> day = 86400
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     directory = Path(tmp)
    ...     fresh = "a" * 32 + ".png"
    ...     stale = "b" * 32 + ".png"
    ...     _ = (directory / fresh).write_bytes(b"f")
    ...     _ = (directory / stale).write_bytes(b"s")
    ...     _ = (directory / "notes.txt").write_bytes(b"junk")
    ...     _ = (directory / ".gitkeep").write_bytes(b"")
    ...     kept = apply_retention(
    ...         directory,
    ...         [
    ...             UploadRecord(fresh, now),
    ...             UploadRecord(stale, now - 8 * day),
    ...         ],
    ...         now=now,
    ...     )
    ...     meta = json.loads((directory / "index.json").read_text())
    ...     (
    ...         [item["name"] for item in meta["files"]] == [fresh],
    ...         [record.name for record in kept] == [fresh],
    ...         (directory / fresh).is_file(),
    ...         not (directory / stale).exists(),
    ...         (directory / "notes.txt").is_file(),
    ...         (directory / ".gitkeep").is_file(),
    ...     )
    (True, True, True, True, True, True)
    """
    directory.mkdir(parents=True, exist_ok=True)
    existing = {
        record.name
        for record in records
        if _safe_name(record.name) and (directory / record.name).is_file()
    }
    present = [record for record in records if record.name in existing]
    kept = select_retained(
        present,
        now=now,
        keep_newest=keep_newest,
        max_age_seconds=max_age_seconds,
    )
    kept_names = {record.name for record in kept}
    for path in list(directory.iterdir()):
        if path.name in PRESERVE_NAMES or path.name in kept_names:
            continue
        if _safe_name(path.name) is None:
            continue
        if path.is_symlink() or path.is_file():
            path.unlink()
    _write_metadata(directory, kept)
    return kept


def _write_metadata(directory: Path, records: list[UploadRecord]) -> None:
    payload = {
        "files": [
            {"name": record.name, "uploaded_at": record.uploaded_at}
            for record in records
        ]
    }
    temporary = directory / f".{META_NAME}.{secrets.token_hex(4)}.tmp"
    temporary.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(directory / META_NAME)


def resolve_stored_upload(
    url: str,
    directory: Path,
    *,
    trusted_hosts: set[str],
) -> Path | None:
    """Map a trusted upload URL to a local file.

    Returns None when ``url`` is not one of our uploads. Raises
    FileNotFoundError when it is, but the file has been pruned.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> name = "ab" * 16 + ".png"
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     directory = Path(tmp)
    ...     _ = (directory / name).write_bytes(b"png")
    ...     url = f"https://s.cympfh.cc/video/uploads/{name}"
    ...     found = resolve_stored_upload(url, directory, trusted_hosts={"s.cympfh.cc"})
    ...     found is not None and found.name == name
    True
    >>> resolve_stored_upload(
    ...     "https://example.com/cat.png", Path("."), trusted_hosts={"s.cympfh.cc"}
    ... ) is None
    True
    >>> resolve_stored_upload(
    ...     "https://evil.test/video/uploads/" + ("ab" * 16) + ".png",
    ...     Path("."),
    ...     trusted_hosts={"s.cympfh.cc"},
    ... ) is None
    True
    >>> resolve_stored_upload(
    ...     "https://s.cympfh.cc/video/uploads/../../etc/passwd",
    ...     Path("."),
    ...     trusted_hosts={"s.cympfh.cc"},
    ... ) is None
    True
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     missing = "cd" * 16 + ".jpg"
    ...     url = f"http://127.0.0.1/video/uploads/{missing}"
    ...     try:
    ...         resolve_stored_upload(url, Path(tmp), trusted_hosts={"127.0.0.1"})
    ...     except FileNotFoundError:
    ...         missing_gone = True
    ...     else:
    ...         missing_gone = False
    ...     missing_gone
    True
    """
    parsed = urlparse(url)
    prefix = "/video/uploads/"
    if not parsed.path.startswith(prefix):
        return None
    name = _safe_name(parsed.path[len(prefix) :])
    if name is None:
        return None
    host = (parsed.hostname or "").lower()
    allowed = {item.lower() for item in trusted_hosts}
    if host and host not in allowed:
        return None
    path = (directory / name).resolve()
    if path.parent != directory.resolve():
        return None
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


class UploadStore:
    """Directory of uploaded images plus ``index.json`` metadata."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.Lock()

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def read_records(self) -> list[UploadRecord]:
        path = self.directory / META_NAME
        if not path.is_file():
            return self._records_from_mtime()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload["files"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Upload metadata unreadable; using file mtimes")
            return self._records_from_mtime()
        records: list[UploadRecord] = []
        if not isinstance(items, list):
            return self._records_from_mtime()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _safe_name(item.get("name"))
            if name is None:
                continue
            try:
                uploaded_at = float(item["uploaded_at"])
            except (KeyError, TypeError, ValueError):
                continue
            records.append(UploadRecord(name, uploaded_at))
        return records

    def _records_from_mtime(self) -> list[UploadRecord]:
        if not self.directory.is_dir():
            return []
        records: list[UploadRecord] = []
        for path in self.directory.iterdir():
            if _safe_name(path.name) and path.is_file():
                records.append(UploadRecord(path.name, path.stat().st_mtime))
        return records

    def add(self, data: bytes, kind: str, *, now: float | None = None) -> UploadRecord:
        """Save image bytes, then prune files that fail retention.

        Seeded files below were uploaded earlier. The ``add`` call is the new
        upload that deletes anything outside the newest 10 and the 7-day window,
        including metadata.

        Examples
        --------
        >>> import json, tempfile
        >>> from pathlib import Path
        >>> now = 1_700_000_000
        >>> day = 86400
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     store = UploadStore(Path(tmp))
        ...     store.ensure()
        ...     seeded = []
        ...     for i in range(11):
        ...         name = f"{i:032x}.jpg"
        ...         _ = (store.directory / name).write_bytes(b"x")
        ...         age = (8 * day) if i == 0 else i
        ...         seeded.append({"name": name, "uploaded_at": now - age})
        ...     _ = (store.directory / "index.json").write_text(json.dumps({"files": seeded}))
        ...     _ = (store.directory / "notes.txt").write_bytes(b"junk")
        ...     created = store.add(b"new-image", "png", now=now)
        ...     meta = json.loads((store.directory / "index.json").read_text())
        ...     names = sorted(item["name"] for item in meta["files"])
        ...     on_disk = sorted(
        ...         p.name for p in store.directory.iterdir() if p.suffix in {".jpg", ".png"}
        ...     )
        ...     stale = f"{0:032x}.jpg"
        ...     past_cap = f"{10:032x}.jpg"
        ...     (
        ...         created.name in names,
        ...         stale not in names,
        ...         past_cap not in names,
        ...         len(names) == 10,
        ...         names == on_disk,
        ...         not (store.directory / stale).exists(),
        ...         not (store.directory / past_cap).exists(),
        ...         (store.directory / "notes.txt").is_file(),
        ...     )
        (True, True, True, True, True, True, True, True)
        """
        if kind not in _KIND_EXT:
            raise ValueError(f"Unsupported image kind: {kind}")
        with self._lock:
            self.ensure()
            uploaded_at = time.time() if now is None else now
            name = self._allocate_name(kind)
            (self.directory / name).write_bytes(data)
            records = self.read_records()
            records.append(UploadRecord(name, uploaded_at))
            apply_retention(self.directory, records, now=uploaded_at)
            return UploadRecord(name, uploaded_at)

    def _allocate_name(self, kind: str) -> str:
        ext = _KIND_EXT[kind]
        for _ in range(5):
            name = secrets.token_hex(16) + ext
            if not (self.directory / name).exists():
                return name
        raise RuntimeError("Could not allocate an upload name")

    def get(self, name: str) -> Path | None:
        safe = _safe_name(name)
        if safe is None:
            return None
        path = (self.directory / safe).resolve()
        if path.parent != self.directory.resolve() or not path.is_file():
            return None
        return path
