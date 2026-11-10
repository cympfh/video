import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from util.trackinfo import TrackInfo


class NicoVideo:
    """ニコニコ動画のメタデータを getthumbinfo API で取得する。API キー不要。

    >>> nico = NicoVideo()
    >>> nico.is_video("https://www.nicovideo.jp/watch/sm9")
    True
    >>> nico.is_video("https://nicovideo.jp/watch/sm44886216?hoge=fuga")
    True
    >>> nico.is_video("https://www.nicovideo.life/watch?v=sm9")  # 変換後の URL も
    True
    >>> nico.is_video("https://www.nicovideo.jp/ranking")
    False
    >>> nico.is_video("https://example.com/watch/sm9")
    False
    >>> nico.video_id("https://www.nicovideo.jp/watch/sm44886216?hoge=fuga")
    'sm44886216'
    >>> nico.video_id("https://www.nicovideo.life/watch?v=sm9")
    'sm9'
    """

    _HOSTS = {"nicovideo.jp", "www.nicovideo.jp"}
    _LIFE_HOSTS = {"nicovideo.life", "www.nicovideo.life"}

    def video_id(self, url: str) -> str | None:
        """動画 URL から動画 ID (``sm9`` など) を取り出す。動画でなければ None"""
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if host in self._HOSTS:
            match = re.match(r"^/watch/([a-z]{2}\d+)", parsed.path)
            return match.group(1) if match else None
        if host in self._LIFE_HOSTS and parsed.path == "/watch":
            vid = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
            if vid and re.fullmatch(r"[a-z]{2}\d+", vid):
                return vid
        return None

    def is_video(self, url: str) -> bool:
        """動画の URL (nicovideo.jp / nicovideo.life の watch) なら True"""
        return self.video_id(url) is not None

    async def resolve_track(self, url: str) -> TrackInfo:
        """動画 URL をメタデータ (タイトル・投稿者・サムネイル) に解決する"""
        video_id = self.video_id(url)
        if not video_id:
            raise ValueError(f"Not a niconico video URL: {url}")
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
        ) as client:
            response = await client.get(
                f"https://ext.nicovideo.jp/api/getthumbinfo/{video_id}",
                timeout=15.0,
            )
            response.raise_for_status()
        root = ET.fromstring(response.text)
        if root.get("status") != "ok":
            raise LookupError(f"No thumb info for video: {url}")
        thumb = root.find("thumb")
        if thumb is None:
            raise LookupError(f"No thumb info for video: {url}")

        def text(tag: str) -> str:
            """``<thumb>`` 直下の要素のテキストを返す。無ければ空文字"""
            elem = thumb.find(tag)
            return (elem.text or "") if elem is not None else ""

        title = text("title")
        if not title:
            raise LookupError(f"No title on video: {url}")
        return TrackInfo(
            url=f"https://www.nicovideo.jp/watch/{video_id}",
            title=title,
            artist=text("user_nickname"),
            artwork_url=text("thumbnail_url") or None,
        )
