import re
import urllib.parse

import httpx

from util.trackinfo import TrackInfo


class Bilibili:
    """bilibili 動画のメタデータを公開 API で取得する。API キー不要。

    リスクコントロール (412) を避けるため、先にトップページを叩いて
    buvid Cookie を取得してから view API を呼ぶ。

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
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }

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
        async with httpx.AsyncClient(
            headers=self._HEADERS, follow_redirects=True
        ) as client:
            # buvid Cookie を得る (これが無いと view API は 412 を返す)
            await client.get("https://www.bilibili.com/", timeout=15.0)
            response = await client.get(
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": bvid},
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("code") != 0:
            raise LookupError(f"No view info for video: {url}")
        data = payload.get("data") or {}
        title = data.get("title") or ""
        if not title:
            raise LookupError(f"No title on video: {url}")
        artwork = (data.get("pic") or "").replace("http://", "https://", 1)
        return TrackInfo(
            url=f"https://www.bilibili.com/video/{bvid}",
            title=title,
            artist=(data.get("owner") or {}).get("name") or "",
            artwork_url=artwork or None,
        )
