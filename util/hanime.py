import asyncio
import html
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass

import httpx

logger = logging.getLogger("uvicorn")

# The watch-page CDN answers non-browser clients with a short decoy playlist
# unless the request carries a Referer from a mirror that issued the signed URL.
# VRChat does not send that Referer, so the server fetches the mp4 itself.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Mirrors of hanimeone.me / hanime1.me that serve the same watch?v= id.
# The official hosts sit behind a Cloudflare challenge from this service.
_MIRROR_HOSTS = (
    "www.hanime163.net",
    "hanime163.net",
    "www.hanime1.pw",
    "hanime1.pw",
)
_OFFICIAL_HOSTS = (
    "hanimeone.me",
    "www.hanimeone.me",
    "hanime1.me",
    "www.hanime1.me",
    "hanimeone.com",
    "www.hanimeone.com",
)
_HOSTS = set(_MIRROR_HOSTS) | set(_OFFICIAL_HOSTS)

_VIDEO_RE = re.compile(
    r'<video\b[^>]*\bid=["\']player["\'][^>]*>(.*?)</video>',
    re.IGNORECASE | re.DOTALL,
)
_SOURCE_RE = re.compile(
    r"<source\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*\bsize=[\"'](\d+)[\"']",
    re.IGNORECASE,
)
_SOURCE_REV_RE = re.compile(
    r"<source\b[^>]*\bsize=[\"'](\d+)[\"'][^>]*\bsrc=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_TOKEN_TS_RE = re.compile(r",(\d{10})(?:\D|$)")


@dataclass(frozen=True)
class HanimeMedia:
    video_id: str
    media_url: str
    referer: str
    quality: str
    expires_at: float


# Shared across Hanime() instances so the redirect and the stream reuse one resolve.
_cache: dict[str, HanimeMedia] = {}
_locks: dict[str, asyncio.Lock] = {}


class Hanime:
    """Resolve a hanimeone / hanime1 watch URL to a signed mp4."""

    def is_watch(self, url: str) -> bool:
        """True for a watch page on a known hanime host.

        >>> Hanime().is_watch("https://hanimeone.me/watch?v=408050")
        True
        >>> Hanime().is_watch("https://www.hanime1.me/watch?v=408050&x=1")
        True
        >>> Hanime().is_watch("https://HanimeOne.com/watch?v=408050")
        True
        >>> Hanime().is_watch("https://www.hanime163.net/watch?v=408050")
        True
        >>> Hanime().is_watch("https://hanime1.pw/watch?v=408050")
        True
        >>> Hanime().is_watch("https://hanimeone.me/search?q=1")
        False
        >>> Hanime().is_watch("https://example.com/watch?v=408050")
        False
        >>> Hanime().is_watch("https://hanimeone.me/watch?v=abc")
        False
        """
        try:
            self.video_id(url)
        except ValueError:
            return False
        return True

    def video_id(self, url: str) -> str:
        """Numeric id from a known host's ``/watch?v=`` query.

        >>> Hanime().video_id("https://www.hanimeone.me/watch?v=408050")
        '408050'
        """
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in _HOSTS:
            raise ValueError(f"Not a hanime watch URL: {url}")
        if parsed.path.rstrip("/") != "/watch":
            raise ValueError(f"Not a hanime watch URL: {url}")
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if not video_id.isdigit():
            raise ValueError(f"No hanime video id in {url}")
        return video_id

    def upstream_headers(
        self, media: HanimeMedia, range_header: str | None = None
    ) -> dict[str, str]:
        headers = {"Referer": media.referer, "User-Agent": _UA}
        if range_header:
            headers["Range"] = range_header
        return headers

    def proxy_path(self, url: str) -> str:
        """Local path that streams the resolved mp4.

        >>> Hanime().proxy_path("https://hanimeone.me/watch?v=408050")
        '/video/hanime/408050'
        """
        return f"/video/hanime/{self.video_id(url)}"

    def cached(self, video_id: str) -> HanimeMedia | None:
        media = _cache.get(video_id)
        if media is None or media.expires_at <= time.time():
            return None
        return media

    def invalidate(self, video_id: str) -> None:
        _cache.pop(video_id, None)

    async def resolve(self, url: str) -> HanimeMedia:
        video_id = self.video_id(url)
        cached = self.cached(video_id)
        if cached is not None:
            return cached

        lock = _locks.setdefault(video_id, asyncio.Lock())
        async with lock:
            cached = self.cached(video_id)
            if cached is not None:
                return cached
            return await self._resolve_uncached(url, video_id)

    async def _resolve_uncached(self, url: str, video_id: str) -> HanimeMedia:
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(25.0, read=30.0),
            headers={"User-Agent": _UA},
        ) as client:
            for base in self._bases_for(url):
                page = f"{base}/watch?v={video_id}"
                referer = f"{base}/"
                try:
                    response = await client.get(page, headers={"Accept": "text/html"})
                except httpx.HTTPError as e:
                    last_error = e
                    logger.warning(f"Hanime watch page failed: {page}: {e}")
                    continue
                if response.status_code != 200 or self._is_challenge(response.text):
                    last_error = LookupError(
                        f"{page} returned {response.status_code}"
                    )
                    logger.warning(str(last_error))
                    continue
                sources = self.parse_sources(response.text)
                if not sources:
                    last_error = LookupError(f"No mp4 source on {page}")
                    logger.warning(str(last_error))
                    continue
                for quality, media_url in sources:
                    try:
                        await self._confirm_mp4(client, media_url, referer)
                    except (LookupError, httpx.HTTPError) as e:
                        last_error = e
                        logger.warning(
                            f"Hanime source rejected ({quality}): {e}"
                        )
                        continue
                    media = HanimeMedia(
                        video_id=video_id,
                        media_url=media_url,
                        referer=referer,
                        quality=quality,
                        expires_at=self._expires_at(media_url),
                    )
                    _cache[video_id] = media
                    return media

        raise LookupError(f"Failed to resolve hanime {video_id}: {last_error}")

    def parse_sources(self, page: str) -> list[tuple[str, str]]:
        """Highest quality first. Each item is ``(quality, url)``.

        >>> page = '''
        ... <video id="player">
        ... <source src="https://cdn.example/a-720p.mp4?secure=x,1700000000" size="720">
        ... <source src="https://cdn.example/a-1080p.mp4?secure=y,1700000000" type="video/mp4" size="1080">
        ... </video>
        ... '''
        >>> Hanime().parse_sources(page)[0][0]
        '1080'
        """
        match = _VIDEO_RE.search(page)
        block = match.group(1) if match else page
        found: dict[int, str] = {}
        for src, size in _SOURCE_RE.findall(block):
            found[int(size)] = html.unescape(src)
        for size, src in _SOURCE_REV_RE.findall(block):
            found.setdefault(int(size), html.unescape(src))
        return [(str(size), found[size]) for size in sorted(found, reverse=True)]

    def _bases_for(self, url: str) -> list[str]:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        ordered: list[str] = []

        def add(name: str) -> None:
            base = f"https://{name}"
            if base not in ordered:
                ordered.append(base)

        if host in _MIRROR_HOSTS:
            add(host)
        for name in _MIRROR_HOSTS:
            add(name)
        if host in _OFFICIAL_HOSTS:
            add(host)
        for name in _OFFICIAL_HOSTS:
            add(name)
        return ordered

    def _is_challenge(self, html: str) -> bool:
        head = html[:2500].lower()
        return (
            "just a moment" in head
            or "attention required" in head
            or "cf-challenge" in head
            or "/cdn-cgi/challenge-platform" in head
        )

    def _expires_at(self, media_url: str) -> float:
        match = _TOKEN_TS_RE.search(media_url)
        if not match:
            return time.time() + 600
        # Refresh a minute before the signed URL lapses.
        return int(match.group(1)) - 60

    async def _confirm_mp4(
        self, client: httpx.AsyncClient, media_url: str, referer: str
    ) -> None:
        headers = {
            "Referer": referer,
            "User-Agent": _UA,
            "Range": "bytes=0-31",
        }
        async with client.stream("GET", media_url, headers=headers) as response:
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            chunk = b""
            async for part in response.aiter_bytes():
                chunk += part
                if len(chunk) >= 16:
                    break
        if status not in (200, 206):
            raise LookupError(f"media status {status}")
        if "mpegurl" in content_type or chunk.startswith(b"#EXTM3U"):
            raise LookupError("media is a decoy playlist")
        if "mp4" not in content_type and b"ftyp" not in chunk:
            raise LookupError(f"media content-type {content_type or 'unknown'}")
