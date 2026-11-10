import urllib.parse

import httpx

from util.trackinfo import TrackInfo


class YouTubeInfo:
    """YouTube 動画のメタデータを oEmbed で取得する。API キー不要。

    >>> yt = YouTubeInfo()
    >>> yt.is_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    True
    >>> yt.is_video("https://youtu.be/dQw4w9WgXcQ")
    True
    >>> yt.is_video("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    True
    >>> yt.is_video("https://m.youtube.com/watch?v=dQw4w9WgXcQ")
    True
    >>> yt.is_video("https://www.youtube.com/@channel")  # 動画ではない
    False
    >>> yt.is_video("https://example.com/watch?v=abc")
    False
    """

    _WATCH_HOSTS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
    }

    def is_video(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if host in ("youtu.be", "www.youtu.be"):
            return len(parsed.path.strip("/")) > 0
        if host not in self._WATCH_HOSTS:
            return False
        if parsed.path == "/watch":
            return "v" in urllib.parse.parse_qs(parsed.query)
        return parsed.path.startswith("/shorts/") or parsed.path.startswith("/live/")

    async def resolve_track(self, url: str) -> TrackInfo:
        """動画 URL をメタデータ (タイトル・チャンネル名・サムネイル) に解決する"""
        if not self.is_video(url):
            raise ValueError(f"Not a YouTube video URL: {url}")
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
        ) as client:
            response = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=15.0,
            )
            if response.status_code in (400, 401, 403, 404):
                raise LookupError(f"No oEmbed info for video: {url}")
            response.raise_for_status()
            data = response.json()
        title = data.get("title") or ""
        if not title:
            raise LookupError(f"No title on video: {url}")
        return TrackInfo(
            url=url,
            title=title,
            artist=data.get("author_name") or "",
            artwork_url=data.get("thumbnail_url"),
        )
