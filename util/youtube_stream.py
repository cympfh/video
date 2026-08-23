import asyncio
import hashlib
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger("uvicorn")

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_COOKIE_KEEP = {
    "LOGIN_INFO",
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "SIDCC",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
    "PREF",
    "VISITOR_INFO1_LIVE",
    "VISITOR_PRIVACY_METADATA",
    "CONSENT",
    "SOCS",
    "YSC",
    "__Secure-ROLLOUT_TOKEN",
}
_YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
}


def is_youtube_url(url: str) -> bool:
    """YouTube動画URLかどうか

    Examples
    --------
    >>> is_youtube_url("https://www.youtube.com/watch?v=dQw4w9wgGcQ")
    True
    >>> is_youtube_url("https://youtu.be/dQw4w9wgGcQ")
    True
    >>> is_youtube_url("https://www.youtube.com/shorts/dQw4w9wgGcQ")
    True
    >>> is_youtube_url("https://www.nicovideo.jp/watch/sm123")
    False
    """
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")


def extract_video_id(url: str) -> str | None:
    """YouTube URLから動画IDを取り出す

    Examples
    --------
    >>> extract_video_id("https://www.youtube.com/watch?v=dQw4w9wgGcQ")
    'dQw4w9wgGcQ'
    >>> extract_video_id("https://youtu.be/dQw4w9wgGcQ?t=10")
    'dQw4w9wgGcQ'
    >>> extract_video_id("https://www.youtube.com/shorts/dQw4w9wgGcQ")
    'dQw4w9wgGcQ'
    >>> extract_video_id("https://www.youtube.com/embed/dQw4w9wgGcQ")
    'dQw4w9wgGcQ'
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid if _VIDEO_ID_RE.fullmatch(vid) else None

    if host in _YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        qs = parse_qs(parsed.query)
        if "v" in qs and _VIDEO_ID_RE.fullmatch(qs["v"][0]):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
            if _VIDEO_ID_RE.fullmatch(parts[1]):
                return parts[1]
    return None


def _write_slim_cookies(src: Path, dest: Path) -> int:
    """yt-dlp が 413 にならないよう認証に必要な cookie だけ残す"""
    kept = ["# Netscape HTTP Cookie File"]
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        if parts[5] in _COOKIE_KEEP:
            kept.append(line)
    dest.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return len(kept) - 1


class YouTubeStream:
    """yt-dlp でYouTube動画を落として転送する"""

    BASE_DIR = Path("stream")
    MAX_CACHED = 8
    MAX_CONCURRENT_DOWNLOADS = 2
    DOWNLOAD_TIMEOUT = 600

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._sema = asyncio.Semaphore(self.MAX_CONCURRENT_DOWNLOADS)

    def _lock_for(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _stream_key(self, url: str) -> str:
        video_id = extract_video_id(url)
        if video_id:
            return f"yt_{video_id}"
        return f"yt_{hashlib.sha256(url.encode()).hexdigest()[:16]}"

    def _cleanup_old(self, keep: str):
        """古いYouTubeキャッシュを削除"""
        cached = [p for p in self.BASE_DIR.glob("yt_*") if p.is_dir()]
        if len(cached) < self.MAX_CACHED:
            return
        cached.sort(key=lambda p: p.stat().st_mtime)
        num_to_remove = len(cached) - self.MAX_CACHED + 1
        for path in cached[:num_to_remove]:
            if path.name == keep:
                continue
            logger.info(f"Removing old YouTube cache: {path.name}")
            shutil.rmtree(path, ignore_errors=True)

    def _yt_dlp_bin(self) -> list[str]:
        return [sys.executable, "-m", "yt_dlp"]

    def _js_runtime_args(self) -> list[str]:
        """deno (default) / node を探して --js-runtimes を付ける"""
        homes = [Path.home() / ".deno/bin/deno", Path.home() / ".local/bin/deno"]
        deno_candidates = ["deno", "/usr/local/bin/deno", "/usr/bin/deno", *map(str, homes)]
        for cand in deno_candidates:
            found = shutil.which(cand) if cand == "deno" else cand
            if found and os.path.isfile(found) and os.access(found, os.X_OK):
                logger.info("yt-dlp JS runtime: deno (%s)", found)
                return ["--js-runtimes", f"deno:{found}"]

        node = shutil.which("node")
        if node:
            logger.info("yt-dlp JS runtime: node (%s)", node)
            return ["--js-runtimes", f"node:{node}"]

        logger.warning("No JS runtime (deno/node) found. YouTube download may fail.")
        return []

    def _cookies_args(self) -> list[str]:
        path = os.getenv("YOUTUBE_COOKIES") or os.getenv("YTDLP_COOKIES")
        candidates = []
        if path:
            candidates.append(Path(path).expanduser())
        candidates.extend(
            [
                Path("/home/ubuntu/firefox/cookie.txt"),
                Path.home() / "cookie.txt",
                Path("cookie.txt"),
                Path("cookies.txt"),
            ]
        )
        seen: set[Path] = set()
        for p in candidates:
            if p in seen:
                continue
            seen.add(p)
            if p.is_file():
                dest = Path("/tmp/yt-dlp-cookies.txt")
                n = _write_slim_cookies(p, dest)
                dest.chmod(0o600)
                logger.info("yt-dlp cookies: %s (%s of %s)", dest, n, p)
                return ["--cookies", str(dest)]
        logger.warning("No YouTube cookies file found")
        return []

    async def _run_yt_dlp(self, cmd: list[str]) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="yt-dlp is not installed")
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail="yt-dlp timed out")
        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode or 0, out, err

    async def _download(self, url: str, outfile: Path) -> None:
        format_sel = (
            "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/"
            "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
            "b[ext=mp4][height<=720]/"
            "best"
        )
        base = [
            *self._yt_dlp_bin(),
            "--no-playlist",
            "--no-progress",
            "--no-mtime",
            "--force-overwrites",
            "--remote-components",
            "ejs:github",
            *self._js_runtime_args(),
            *self._cookies_args(),
            "-f",
            format_sel,
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "-o",
            str(outfile),
        ]
        # android は cookie を無視する。web_safari は cookie + PO を使う
        attempts = [
            ["--extractor-args", "youtube:player_client=web_safari"],
            ["--extractor-args", "youtube:player_client=web"],
        ]

        last_err = ""
        async with self._sema:
            for i, extra in enumerate(attempts, start=1):
                cmd = [*base, *extra, "--", url]
                logger.info("Running yt-dlp attempt %s/%s for %s", i, len(attempts), url)
                rc, stdout, stderr = await self._run_yt_dlp(cmd)
                if stdout:
                    logger.info(stdout[-500:])
                if rc == 0 and outfile.exists() and outfile.stat().st_size > 0:
                    return
                last_err = stderr[-1000:]
                logger.warning("yt-dlp attempt %s failed (%s): %s", i, rc, last_err)
                if outfile.exists():
                    outfile.unlink()

        logger.error("yt-dlp failed: %s", last_err)
        raise HTTPException(status_code=502, detail="Failed to download YouTube video")

    async def get(self, url: str) -> FileResponse:
        """YouTube動画をダウンロードして返す"""
        stream_key = self._stream_key(url)
        outdir = self.BASE_DIR / stream_key
        outfile = outdir / "video.mp4"
        donefile = outdir / ".done"

        async with self._lock_for(stream_key):
            if outfile.exists() and donefile.exists() and outfile.stat().st_size > 0:
                logger.info(f"YouTube cache hit: {stream_key}")
                donefile.touch()
                return FileResponse(path=str(outfile), media_type="video/mp4")

            logger.info(f"YouTube cache miss, downloading: {url}")
            self._cleanup_old(keep=stream_key)
            outdir.mkdir(parents=True, exist_ok=True)
            if donefile.exists():
                donefile.unlink()
            if outfile.exists():
                outfile.unlink()

            t0 = time.time()
            await self._download(url, outfile)
            donefile.touch()
            logger.info(
                "YouTube downloaded: %s (%s bytes, %.1fs)",
                stream_key,
                outfile.stat().st_size,
                time.time() - t0,
            )

        return FileResponse(path=str(outfile), media_type="video/mp4")
