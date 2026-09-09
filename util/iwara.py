import hashlib
import urllib.parse

import httpx


class Iwara:
    """Resolve a public iwara.tv video URL to a direct mp4, without nicovrc."""

    # Public extractor constant. Not a login secret.
    # Without this, the file list is still 200 but only 360 and preview.
    _X_VERSION_SECRET = "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN"
    _QUALITY_RANK = {"Source": 0, "540": 1, "360": 2}

    def is_video(self, url: str) -> bool:
        return "iwara.tv/video/" in url

    def video_id(self, url: str) -> str:
        video_id = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
        if not video_id:
            raise ValueError(f"No iwara video id in {url}")
        return video_id

    def x_version(self, file_url: str) -> str:
        parsed = urllib.parse.urlparse(file_url)
        file_id = parsed.path.rstrip("/").split("/")[-1]
        expires = urllib.parse.parse_qs(parsed.query).get("expires", [""])[0]
        if not file_id or not expires:
            raise ValueError(f"iwara fileUrl missing id or expires: {file_url}")
        raw = f"{file_id}_{expires}_{self._X_VERSION_SECRET}"
        return hashlib.sha1(raw.encode()).hexdigest()

    def best_view(self, files: list) -> str:
        ranked: list[tuple[int, str]] = []
        for item in files:
            name = item.get("name")
            if name not in self._QUALITY_RANK:
                continue
            src = (item.get("src") or {}).get("view") or (item.get("src") or {}).get("download")
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            ranked.append((self._QUALITY_RANK[name], src))
        if not ranked:
            raise LookupError("No iwara view URL")
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]

    async def resolve_mp4(self, url: str) -> str:
        video_id = self.video_id(url)
        headers = {
            "Accept": "application/json",
            "Origin": "https://www.iwara.tv",
            "Referer": "https://www.iwara.tv/",
        }
        async with httpx.AsyncClient(follow_redirects=True) as client:
            meta_res = await client.get(
                f"https://api.iwara.tv/video/{video_id}",
                headers=headers,
                timeout=12.0,
            )
            meta_res.raise_for_status()
            meta = meta_res.json()
            file_url = meta.get("fileUrl") if isinstance(meta, dict) else None
            if not file_url:
                raise LookupError(f"No fileUrl for iwara {video_id}")

            file_res = await client.get(
                file_url,
                headers={**headers, "X-Version": self.x_version(file_url)},
                timeout=12.0,
            )
            file_res.raise_for_status()
            files = file_res.json()

        if not isinstance(files, list):
            raise LookupError(f"Unexpected iwara file list for {video_id}")
        return self.best_view(files)
