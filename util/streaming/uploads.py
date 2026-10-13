"""Store uploaded images and drop the ones we no longer keep.

A new upload keeps a file only when both are true: it is among the newest
``KEEP_NEWEST`` uploads, and it was uploaded within ``MAX_AGE_SECONDS``.
Everything else is deleted, including metadata, so old URLs stop resolving.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

logger = logging.getLogger("uvicorn")

KEEP_NEWEST = 10
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
META_NAME = "index.json"
PRESERVE_NAMES = {META_NAME, ".gitkeep"}
NAME_RE_SOURCE = r"[0-9a-f]{32}\.(?:jpg|png|gif|webp)"

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
    ...         not (directory / "notes.txt").exists(),
    ...     )
    (True, True, True, True, True)
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
        ...         not (store.directory / "notes.txt").exists(),
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
