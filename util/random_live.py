import random
from datetime import datetime, timezone

import httpx

from util.youtube import YouTube


class RandomLive:
    """Pick a currently-live YouTube stream from a curated channel list.

    Channel IDs are fetched from:
    https://gist.github.com/cympfh/e8ee500adacbc1bbfc717ca7cbb2a9b4
    """

    def __init__(self):
        self.url = (
            "https://gist.githubusercontent.com/cympfh/"
            "e8ee500adacbc1bbfc717ca7cbb2a9b4/raw/random-live-users"
        )

    async def _fetch_channel_ids(self) -> list[str]:
        async with httpx.AsyncClient() as client:
            response = await client.get(self.url, timeout=1.0)
            response.raise_for_status()
            lines = response.text.splitlines()

            channel_ids = []
            for line in lines:
                line = line.strip()
                if "#" in line:
                    line = line.split("#")[0].strip()
                if line:
                    channel_ids.append(line)
            if not channel_ids:
                raise ValueError("No channel IDs found in the list.")
            return channel_ids

    async def get(self) -> str:
        """Return a live watch URL, or raise LookupError if nobody is live."""
        channel_ids = await self._fetch_channel_ids()

        now = datetime.now(timezone.utc)
        seed = now.strftime("%Y/%m/%d")
        random.Random(seed).shuffle(channel_ids)
        start_idx = now.hour % len(channel_ids)

        youtube = YouTube()
        n = len(channel_ids)
        for offset in range(n):
            channel_id = channel_ids[(start_idx + offset) % n]
            live_url = await youtube.get_live_video_url(channel_id)
            if live_url:
                return live_url

        raise LookupError("No live streams found among curated channels.")
