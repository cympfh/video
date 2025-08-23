import asyncio
import hashlib
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from fastapi.responses import RedirectResponse

logger = logging.getLogger("uvicorn")


class ImageStream:
    MAX_SECONDS = 1 * 60 * 60  # hours
    MAX_NUM_PROCESSES = 4
    BASE_DIR = Path("stream")

    processes = {}  # stream_key -> {'process': Popen, 'last_access': float}

    def __init___(self):
        os.makedirs(str(self.BASE_DIR), exist_ok=True)

    def _cleanup_old_processes(self):
        """古いストリームの削除

        Note
        ----
        この関数が呼ばれた直後に新しくストリームを開始するため
        この時点で MAX_NUM_PROCESSES 未満である必要がある
        """
        logger.info(
            f"#Working: {len(self.processes)} processes, max={self.MAX_NUM_PROCESSES}"
        )
        if len(self.processes) < self.MAX_NUM_PROCESSES:
            return
        sorted_processes = sorted(
            self.processes.items(), key=lambda x: x[1]["last_access"]
        )
        num_to_kill = len(self.processes) - self.MAX_NUM_PROCESSES + 1
        for stream_key, process_info in sorted_processes[:num_to_kill]:
            logger.info(f"Terminating old process for stream: {stream_key}")
            try:
                process_info["process"].terminate()
                process_info["process"].wait(timeout=5)
                logger.info(f"Process terminated gracefully for stream: {stream_key}")
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    process_info["process"].kill()
                    logger.info(f"Process killed forcefully for stream: {stream_key}")
                except ProcessLookupError:
                    logger.warning(
                        f"Process already terminated for stream: {stream_key}"
                    )
            del self.processes[stream_key]

    def stream(self, image_path: str, stream_key: str):
        """ffmpeg を用いて HLS ストリームを開始する

        Parameters
        ----------
        image_path
            入力画像のパス

        Returns
        -------
        str
            ストリームのキー（ディレクトリ名）
            ./stream/<stream_key>/index.m3u8 でアクセス可能になる
        """
        outdir = self.BASE_DIR / stream_key
        os.makedirs(str(outdir), exist_ok=True)

        fps = 16
        cmd = [
            "ffmpeg",
            "-y",
            "-re",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            image_path,
            "-vf",
            "scale=1280:720,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "stillimage",
            "-crf",
            "30",
            "-r",
            str(fps),
            "-g",
            str(fps * 4),  # GOP size
            "-sc_threshold",
            "0",
            "-force_key_frames",
            "expr:gte(t,n_forced*4)",
            "-an",
            "-t",
            str(self.MAX_SECONDS),
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
        logger.info(f"Starting HLS stream for: {image_path} -> {stream_key}")
        process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.processes[stream_key] = {"process": process, "last_access": time.time()}
        logger.info(f"FFmpeg process started with PID: {process.pid}")
        return process

    async def get(
        self, path: str | None = None, url: str | None = None
    ) -> RedirectResponse:
        """画像のストリームを作成してリダイレクトする

        Parameters
        ----------
        path
            画像ファイルのパス
            指定しない場合は URL を使用する
        url
            画像ファイルの URL
            指定しない場合は path を使用する
        """
        if path is None and url is None:
            raise ValueError("Either path or url must be provided")

        stream_key = ""
        if path is not None:
            stream_key = hashlib.sha256(path.encode()).hexdigest()
            logger.info(f"Generated stream key for path {path}: {stream_key}")
        elif url is not None:
            stream_key = hashlib.sha256(url.encode()).hexdigest()
            logger.info(f"Generated stream key for URL {url}: {stream_key}")

        # cached
        if os.path.exists(self.BASE_DIR / stream_key / "index.m3u8"):
            logger.info(f"Cache hit for stream: {stream_key}")
            if stream_key in self.processes:
                self.processes[stream_key]["last_access"] = time.time()
            return RedirectResponse(
                url=f"/video/stream/{stream_key}/index.m3u8", status_code=302
            )

        # download URL
        if url is not None:
            logger.info(f"Cache miss, downloading from URL: {url}")
            with tempfile.TemporaryDirectory(delete=False) as temp_dir:
                path = str(Path(temp_dir) / "image.jpg")
            async with httpx.AsyncClient(
                headers={"user-agent": "curl/7.54.1"}
            ) as client:
                response = await client.get(url, timeout=2.0)
                response.raise_for_status()
                with open(path, "wb") as f:
                    f.write(response.content)

        # path exsits
        assert path is not None, "Something wrong"

        logger.info(f"Creating new stream for: {stream_key}")
        self._cleanup_old_processes()
        _ = self.stream(path, stream_key)
        playlist = f"./stream/{stream_key}/index.m3u8"

        logger.info(f"Waiting for playlist creation: {playlist}")
        while not os.path.exists(playlist) or os.path.getsize(playlist) <= 0:
            await asyncio.sleep(0.1)

        logger.info(
            f"Playlist ready, redirecting to: /video/stream/{stream_key}/index.m3u8"
        )
        return RedirectResponse(
            url=f"/video/stream/{stream_key}/index.m3u8", status_code=302
        )
