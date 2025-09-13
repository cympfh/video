import random
from datetime import datetime, timezone

import httpx


class Random:
    """Fetch a random video from

    https://gist.github.com/cympfh/653f299d9c748aa78b1a800f2bfa5221
    """

    def __init__(self):
        self.url = "https://gist.githubusercontent.com/cympfh/653f299d9c748aa78b1a800f2bfa5221/raw/random-videos"

    async def get(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(self.url, timeout=1.0)
            response.raise_for_status()
            video_urls = response.text.splitlines()
            if not video_urls:
                raise ValueError("No video URLs found in the list.")

            now = datetime.now(timezone.utc)
            seed = now.strftime("%Y/%m/%d")
            random.Random(seed).shuffle(video_urls)
            idx = now.hour % len(video_urls)
            return video_urls[idx]
