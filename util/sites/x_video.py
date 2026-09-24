import math
import re
import urllib.parse

import httpx


class X:
    """Resolve an X / Twitter status URL to a direct mp4, without nicovrc."""

    _STATUS_RE = re.compile(r"/status/(\d+)")
    _HOSTS = {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }

    def is_status(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        return host in self._HOSTS and self._STATUS_RE.search(parsed.path) is not None

    def status_id(self, url: str) -> str:
        match = self._STATUS_RE.search(urllib.parse.urlparse(url).path)
        if not match:
            raise ValueError(f"No status id in {url}")
        return match.group(1)

    def syndication_token(self, status_id: str) -> str:
        # Syndication accepts ((id / 1e15) * pi) with zeros and the decimal point stripped.
        return str((int(status_id) / 1e15) * math.pi).replace("0", "").replace(".", "")

    def best_mp4(self, payload: dict) -> str | None:
        candidates: list[tuple[int, str]] = []
        for media in payload.get("mediaDetails") or []:
            if media.get("type") != "video":
                continue
            for variant in (media.get("video_info") or {}).get("variants") or []:
                if variant.get("content_type") != "video/mp4":
                    continue
                url = variant.get("url")
                if not url:
                    continue
                candidates.append((int(variant.get("bitrate") or 0), url))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    async def resolve_mp4(self, url: str) -> str:
        status_id = self.status_id(url)
        token = self.syndication_token(status_id)
        endpoint = "https://cdn.syndication.twimg.com/tweet-result"
        params = {"id": status_id, "token": token, "lang": "en"}
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(endpoint, params=params, timeout=8.0)
            response.raise_for_status()
            payload = response.json()

        mp4 = self.best_mp4(payload)
        if not mp4:
            raise LookupError(f"No mp4 on status {status_id}")
        return mp4
