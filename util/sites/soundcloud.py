import re
import urllib.parse
from dataclasses import dataclass

import httpx

# client_id は soundcloud.com の JS アセットからスクレイプする (yt-dlp と同じ手口)。
# たまにローテーションされるので、401/403 を受けたら取り直す。
_client_id_cache: str | None = None

_CLIENT_ID_RE = re.compile(r'client_id\s*[:=]\s*"([0-9a-zA-Z]+)"')
_SCRIPT_SRC_RE = re.compile(r'src="(https://[^"]+\.js)"')


@dataclass
class SoundCloudTrack:
    """A resolved SoundCloud track's metadata."""

    url: str
    title: str
    artist: str
    artwork_url: str | None = None

    @property
    def display_title(self) -> str:
        """YamaPlayer などに表示するための1行タイトル

        >>> SoundCloudTrack("u", "Song", "Artist").display_title
        'Artist - Song'
        >>> SoundCloudTrack("u", "Song", "").display_title
        'Song'
        """
        if self.artist:
            return f"{self.artist} - {self.title}"
        return self.title

    def artwork(self, size: str = "t500x500") -> str | None:
        """アートワーク画像の URL。``-large`` (100px) を指定サイズに置き換える

        >>> t = SoundCloudTrack("u", "Song", "Artist", "https://i1.sndcdn.com/artworks-abc-large.jpg")
        >>> t.artwork()
        'https://i1.sndcdn.com/artworks-abc-t500x500.jpg'
        >>> t.artwork("original")
        'https://i1.sndcdn.com/artworks-abc-original.jpg'
        >>> SoundCloudTrack("u", "Song", "Artist").artwork() is None
        True
        """
        if not self.artwork_url:
            return None
        return re.sub(r"-large(\.\w+)$", rf"-{size}\1", self.artwork_url)


class SoundCloud:
    """Resolve a SoundCloud track URL to its stream (HLS m3u8) URL.

    公開トラックのみ。公式 API キーは不要。

    >>> sc = SoundCloud()
    >>> sc.is_track("https://soundcloud.com/forss/flickermood")
    True
    >>> sc.is_track("https://soundcloud.com/monstercat/pegboard-nerds-disconnected")
    True
    >>> sc.is_track("https://soundcloud.com/forss")  # プロフィール
    False
    >>> sc.is_track("https://soundcloud.com/forss/sets/mix")  # プレイリスト
    False
    >>> sc.is_track("https://soundcloud.com/discover")  # トラックではないページ
    False
    >>> sc.is_track("https://soundcloud.com/stream")
    False
    >>> sc.is_track("https://example.com/foo/bar")
    False
    """

    _HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
    # トラックではないページのパス(先頭セグメント)
    _RESERVED = {
        "you",
        "discover",
        "stream",
        "search",
        "settings",
        "upload",
        "notifications",
        "messages",
        "pages",
        "charts",
        "jobs",
        "imprint",
        "terms-of-use",
        "pages",
    }

    def is_track(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower() not in self._HOSTS:
            return False
        segments = [s for s in parsed.path.split("/") if s]
        if len(segments) != 2:
            return False
        return segments[0].lower() not in self._RESERVED

    async def _scrape_client_id(self) -> str:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
        ) as client:
            response = await client.get("https://soundcloud.com", timeout=15.0)
            response.raise_for_status()
            scripts = _SCRIPT_SRC_RE.findall(response.text)
            # client_id は後ろの方のバンドルにいることが多い
            for src in reversed(scripts[-6:]):
                js = await client.get(src, timeout=15.0)
                js.raise_for_status()
                match = _CLIENT_ID_RE.search(js.text)
                if match:
                    return match.group(1)
        raise LookupError("Could not scrape SoundCloud client_id")

    async def _client_id(self, force_refresh: bool = False) -> str:
        global _client_id_cache
        if force_refresh or _client_id_cache is None:
            _client_id_cache = await self._scrape_client_id()
        return _client_id_cache

    def _best_transcoding(self, track: dict) -> dict | None:
        transcodings = (track.get("media") or {}).get("transcodings") or []

        def score(t: dict) -> tuple[int, int]:
            fmt = t.get("format") or {}
            protocol = fmt.get("protocol") or ""
            mime = fmt.get("mime_type") or ""
            # HLS の AAC を最優先。次に HLS の MP3、最後にプログレッシブ
            rank = 0
            if protocol == "hls" and mime.startswith("audio/mp4"):
                rank = 3
            elif protocol == "hls":
                rank = 2
            elif protocol == "progressive":
                rank = 1
            hq = 1 if t.get("quality") == "hq" else 0
            return (rank, hq)

        if not transcodings:
            return None
        return max(transcodings, key=score)

    async def _resolve_payload(self, url: str) -> dict:
        """resolve API でトラックの JSON を取得する"""
        payload = await self._fetch_json(
            "https://api-v2.soundcloud.com/resolve", params={"url": url}
        )
        if payload.get("kind") != "track":
            raise LookupError(f"Not a track: {url}")
        return payload

    async def resolve_track(self, url: str) -> SoundCloudTrack:
        """トラック URL をメタデータ (タイトル・アーティスト・アートワーク) に解決する"""
        if not self.is_track(url):
            raise ValueError(f"Not a SoundCloud track URL: {url}")
        payload = await self._resolve_payload(url)
        artwork = payload.get("artwork_url")
        if not artwork:
            # トラックにアートワークが無いときは作者のアバターで代用する
            artwork = (payload.get("user") or {}).get("avatar_url")
        return SoundCloudTrack(
            url=payload.get("permalink_url") or url,
            title=payload.get("title") or "",
            artist=(payload.get("user") or {}).get("username") or "",
            artwork_url=artwork,
        )

    async def resolve_stream(self, url: str) -> str:
        """トラック URL をストリーム URL (HLS m3u8) に解決する"""
        if not self.is_track(url):
            raise ValueError(f"Not a SoundCloud track URL: {url}")
        payload = await self._resolve_payload(url)
        transcoding = self._best_transcoding(payload)
        if transcoding is None:
            raise LookupError(f"No transcodings on track: {url}")
        stream = await self._fetch_json(transcoding["url"])
        stream_url = stream.get("url")
        if not stream_url:
            raise LookupError(f"No stream url on track: {url}")
        return stream_url

    async def _fetch_json(self, endpoint: str, params: dict | None = None) -> dict:
        """api-v2 を client_id つきで叩く。401/403 なら client_id を取り直して一度だけ再試行"""
        for attempt in range(2):
            client_id = await self._client_id(force_refresh=attempt > 0)
            query = {"client_id": client_id, **(params or {})}
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
            ) as client:
                response = await client.get(endpoint, params=query, timeout=15.0)
                if response.status_code in (401, 403) and attempt == 0:
                    continue
                response.raise_for_status()
                return response.json()
        raise LookupError("SoundCloud API rejected the request")
