import json
import re
import urllib.parse

import httpx

from util.trackinfo import TrackInfo


class Bilibili:
    """bilibili 動画のメタデータをページ HTML から取得する。API キー不要。

    公開の view API はボット対策 (412) で弾かれることがあるため、
    動画ページの og タグと __INITIAL_STATE__ を読む。

    >>> bili = Bilibili()
    >>> bili.is_video("https://www.bilibili.com/video/BV1smLczPEa5/")
    True
    >>> bili.is_video("https://bilibili.com/video/BV1y13462ESA?spm_id_from=333")
    True
    >>> bili.is_video("https://www.bilibili.com/")
    False
    >>> bili.is_video("https://example.com/video/BV1smLczPEa5")
    False
    >>> bili.bvid("https://www.bilibili.com/video/BV1smLczPEa5/?p=4")
    'BV1smLczPEa5'
    """

    _HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
    _BV_RE = re.compile(r"^/video/(BV[0-9A-Za-z]+)")
    _OG_TITLE_RE = re.compile(
        r'<meta[^>]+(?:property|name)="og:title"[^>]+content="([^"]+)"'
    )
    _OG_IMAGE_RE = re.compile(
        r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"'
    )
    _TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
    _OWNER_RE = re.compile(r'"owner":\{"mid":\d+,"name":"((?:[^"\\]|\\.)*)"')
    _TITLE_SUFFIX_RE = re.compile(r"_哔哩哔哩_bilibili\s*$")

    def bvid(self, url: str) -> str | None:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower() not in self._HOSTS:
            return None
        match = self._BV_RE.match(parsed.path)
        return match.group(1) if match else None

    def is_video(self, url: str) -> bool:
        return self.bvid(url) is not None

    async def resolve_track(self, url: str) -> TrackInfo:
        """動画 URL をメタデータ (タイトル・投稿者・カバー画像) に解決する"""
        bvid = self.bvid(url)
        if not bvid:
            raise ValueError(f"Not a bilibili video URL: {url}")
        page_url = f"https://www.bilibili.com/video/{bvid}"
        async with httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            follow_redirects=True,
        ) as client:
            response = await client.get(page_url, timeout=15.0)
            response.raise_for_status()
        html = response.text

        title = None
        match = self._OG_TITLE_RE.search(html) or self._TITLE_TAG_RE.search(html)
        if match:
            title = self._TITLE_SUFFIX_RE.sub("", match.group(1)).strip()
        if not title:
            raise LookupError(f"No title on video: {url}")

        artwork = None
        match = self._OG_IMAGE_RE.search(html)
        if match:
            artwork = match.group(1)

        artist = ""
        match = self._OWNER_RE.search(html)
        if match:
            try:
                artist = json.loads(f'"{match.group(1)}"')
            except json.JSONDecodeError:
                artist = match.group(1)

        return TrackInfo(url=page_url, title=title, artist=artist, artwork_url=artwork)
