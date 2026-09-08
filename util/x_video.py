import urllib.parse

import httpx


def is_x_status(url: str) -> bool:
    """True for X/Twitter status URLs."""
    host = urllib.parse.urlparse(url).netloc.lower()
    return host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}


def mp4_from_proxy_location(location: str) -> str | None:
    """Pull the twimg mp4 out of a nicovrc /proxy/ redirect.

    nicovrc answers 302 to its /video/ player page, whose `url` query is the
    actual mp4. That player page hangs, so callers must stop at this hop.
    """
    if not location:
        return None

    parsed = urllib.parse.urlparse(location)
    mp4 = urllib.parse.parse_qs(parsed.query).get("url", [None])[0]
    if mp4 and "video.twimg.com" in mp4:
        return mp4

    if "video.twimg.com" in location and "/ext_tw_video/" in location:
        return location
    return None


async def resolve_mp4(url: str) -> str:
    """Resolve an X status URL to a direct video.twimg.com mp4."""
    proxy = f"https://nicovrc.net/proxy/?{url}"
    async with httpx.AsyncClient(follow_redirects=False) as client:
        response = await client.get(proxy, timeout=8.0)
        location = response.headers.get("location", "")

    mp4 = mp4_from_proxy_location(location)
    if not mp4:
        raise LookupError(f"No playable mp4 for {url}")
    return mp4
