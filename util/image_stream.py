import asyncio
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastapi.responses import RedirectResponse


class ImageStream:
    MAX_SECONDS = 12 * 60 * 60  # hours
    BASE_DIR = Path("stream")

    processes = {}  # sid -> Popen

    def __init___(self):
        os.makedirs(str(self.BASE_DIR), exist_ok=True)

    def stream(self, image_path: str, stream_key: str):
        """ffmpeg を用いて HLS ストリームを開始する

        Parameters
        ----------
        image_path : str
            入力画像のパス

        Returns
        -------
        str
            ストリームのキー（ディレクトリ名）
            ./stream/<stream_key>/index.m3u8 でアクセス可能になる
        """
        outdir = self.BASE_DIR / stream_key
        os.makedirs(str(outdir), exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-re",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            image_path,
            "-vf",
            "scale=1280:720,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-r",
            "30",
            "-g",
            "60",
            "-an",
            "-f",
            "hls",
            "-hls_time",
            "4",
            "-hls_list_size",
            "6",
            "-hls_flags",
            "delete_segments+append_list+omit_endlist",
            "-hls_segment_filename",
            os.path.join(outdir, "seg_%05d.ts"),
            str(outdir / "index.m3u8"),
        ]
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    async def get(
        self, path: str | None = None, url: str | None = None
    ) -> RedirectResponse:
        if path is None and url is None:
            raise ValueError("Either path or url must be provided")

        stream_key = ""
        if path is not None:
            stream_key = hashlib.sha256(path.encode()).hexdigest()
        elif url is not None:
            stream_key = hashlib.sha256(url.encode()).hexdigest()

        # cached
        if os.path.exists(self.BASE_DIR / stream_key / "index.m3u8"):
            return RedirectResponse(
                url=f"/video/stream/{stream_key}/index.m3u8", status_code=302
            )

        # download URL
        if url is not None:
            with tempfile.TemporaryDirectory(delete=False) as temp_dir:
                path = str(Path(temp_dir) / "image.jpg")
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=2.0)
                response.raise_for_status()
                with open(path, "wb") as f:
                    f.write(response.content)

        # path exsits
        assert path is not None, "Something wrong"

        _ = self.stream(path, stream_key)
        playlist = f"./stream/{stream_key}/index.m3u8"
        while not os.path.exists(playlist) or os.path.getsize(playlist) <= 0:
            await asyncio.sleep(0.1)

        return RedirectResponse(
            url=f"/video/stream/{stream_key}/index.m3u8", status_code=302
        )
