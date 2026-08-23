import asyncio
import hashlib
import logging
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
        if shutil.which("yt-dlp"):
            return ["yt-dlp"]
        return [sys.executable, "-m", "yt_dlp"]

    async def _download(self, url: str, outfile: Path) -> None:
        cmd = [
            *self._yt_dlp_bin(),
            "--no-playlist",
            "--no-progress",
            "--no-mtime",
            "--force-overwrites",
            "-f",
            "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4][height<=720]/best",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "-o",
            str(outfile),
            url,
        ]
        logger.info(f"Running yt-dlp: {' '.join(cmd)}")
        async with self._sema:
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

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace")[-1000:]
            logger.error(f"yt-dlp failed ({proc.returncode}): {err}")
            raise HTTPException(status_code=502, detail="Failed to download YouTube video")
        if stdout:
            logger.info(stdout.decode("utf-8", errors="replace")[-500:])
        if not outfile.exists() or outfile.stat().st_size <= 0:
            raise HTTPException(status_code=502, detail="yt-dlp produced no output")

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
