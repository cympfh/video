import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from util.streaming.artwork import compose_frame_png

logger = logging.getLogger("uvicorn")


class ImageStream:
    MAX_SECONDS = 1 * 60 * 60  # hours
    MAX_NUM_PROCESSES = 4
    BASE_DIR = Path("stream")

    processes = {}  # stream_key -> {'process': Popen, 'last_access': float}

    # 音声つきストリームはこの名前でリダイレクトする (先頭から再生させる)
    EVENT_PLAYLIST = "start.m3u8"
    _audio_locks: dict[str, asyncio.Lock] = {}  # key -> 同じトラックの同時作成を防ぐ

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

            # ストリームディレクトリを削除
            stream_dir = self.BASE_DIR / stream_key
            if stream_dir.exists():
                try:
                    shutil.rmtree(stream_dir)
                    logger.info(f"Removed stream directory: {stream_key}")
                except OSError as e:
                    logger.error(f"Failed to remove directory {stream_key}: {e}")

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

    def stream_slideshow(
        self,
        image_paths: list[str],
        stream_key: str,
        duration: int,
        loop_count: int,
    ):
        """スライドショーのHLSストリーム生成

        Parameters
        ----------
        image_paths : List[str]
            ダウンロード済み画像ファイルパスのリスト
        stream_key : str
            ストリームの一意キー
        duration : int
            各画像の表示秒数
        loop_count : int
            スライドショーのループ回数
        """
        outdir = self.BASE_DIR / stream_key
        os.makedirs(str(outdir), exist_ok=True)

        fps = 16

        # 画像リストを loop_count 倍に拡張
        expanded_images = image_paths * loop_count

        # concat demuxer 用のファイルリストを作成
        concat_file = outdir / "concat.txt"
        with open(concat_file, "w") as f:
            for image_path in expanded_images:
                f.write(f"file '{image_path}'\n")
                f.write(f"duration {duration}\n")
            # 最後の画像をもう一度書く（concat demuxer の仕様）
            f.write(f"file '{expanded_images[-1]}'\n")

        # hls_list_size を動的に計算（1時間分のセグメント数）
        # 3600秒 / hls_time(4秒) / duration / 画像枚数
        hls_list_size = max(int(3600 / 4 / duration / len(image_paths)), 1)
        logger.info(
            f"Calculated hls_list_size: {hls_list_size} "
            f"(duration={duration}s, images={len(image_paths)})"
        )

        # FFmpegコマンド構築
        cmd = [
            "ffmpeg",
            "-y",
            "-re",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-r",
            str(fps),
            "-g",
            str(fps * 4),
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
            str(hls_list_size),
            "-hls_flags",
            "delete_segments+append_list+omit_endlist",
            "-hls_segment_filename",
            os.path.join(outdir, "seg_%05d.ts"),
            str(outdir / "index.m3u8"),
        ]

        logger.info(f"Starting HLS slideshow stream: {stream_key}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.processes[stream_key] = {"process": process, "last_access": time.time()}
        logger.info(f"FFmpeg slideshow process started with PID: {process.pid}")
        return process

    @staticmethod
    def audio_stream_command(
        frame_path: str,
        audio_url: str,
        outdir: str | Path,
        duration: float | None = None,
        fps: int = 8,
    ) -> list[str]:
        """静止画 + 音声 を HLS (h264 + aac) にする ffmpeg コマンド

        静止画は ``-loop 1`` で流し続け、音が終わったところで止める
        (``-shortest``、長さが分かっていれば ``-t`` でも頭打ち)。
        ``-re`` は付けない: 実時間より速くエンコードして先にセグメントを作り、
        プレイリストは EVENT 型で先頭から全部残す (終わると ENDLIST が付く)。

        Examples
        --------
        >>> cmd = ImageStream.audio_stream_command(
        ...     "stream/k/frame.png", "https://cf-hls-media.sndcdn.com/x.m3u8", "stream/k", 213.886)
        >>> cmd[:7]
        ['ffmpeg', '-y', '-loop', '1', '-framerate', '8', '-i']
        >>> cmd[cmd.index("-map") + 1], cmd[cmd.index("-map") + 3]
        ('0:v:0', '1:a:0')
        >>> [cmd[cmd.index(k) + 1] for k in ("-c:v", "-tune", "-c:a", "-t", "-hls_playlist_type")]
        ['libx264', 'stillimage', 'aac', '214.886', 'event']
        >>> "-shortest" in cmd and "-re" not in cmd and "-an" not in cmd
        True
        >>> cmd[-1]
        'stream/k/index.m3u8'
        >>> "-t" in ImageStream.audio_stream_command("f.png", "a.m3u8", "o")
        False
        """
        outdir = Path(outdir)
        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(frame_path),
            "-i",
            audio_url,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            "format=yuv420p",
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
            str(fps * 4),
            "-keyint_min",
            str(fps * 4),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            "expr:gte(t,n_forced*4)",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-shortest",
        ]
        if duration:
            # 余白 1 秒。音が先に終われば -shortest で止まる
            cmd += ["-t", f"{duration + 1:.3f}"]
        cmd += [
            "-f",
            "hls",
            "-hls_time",
            "4",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_flags",
            "independent_segments+temp_file",
            "-hls_segment_filename",
            str(outdir / "seg_%05d.ts"),
            str(outdir / "index.m3u8"),
        ]
        return cmd

    def stream_with_audio(
        self,
        frame_path: str,
        audio_url: str,
        stream_key: str,
        duration: float | None = None,
    ):
        """静止画 + 音声 の HLS ストリームを開始する"""
        outdir = self.BASE_DIR / stream_key
        os.makedirs(str(outdir), exist_ok=True)
        cmd = self.audio_stream_command(frame_path, audio_url, outdir, duration)
        logger.info(f"Starting HLS audio+still stream: {stream_key}")
        # ログは配信ディレクトリの外に置く (署名付きの音声 URL が載るので)
        log = open(Path(tempfile.gettempdir()) / f"ffmpeg-{stream_key}.log", "wb")
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log)
        log.close()
        self.processes[stream_key] = {"process": process, "last_access": time.time()}
        logger.info(f"FFmpeg audio+still process started with PID: {process.pid}")
        return process

    def audio_lock(self, key: str) -> asyncio.Lock:
        """同じトラックへの同時リクエストで ffmpeg を二重に起動しないためのロック

        >>> s = ImageStream()
        >>> s.audio_lock("a") is s.audio_lock("a"), s.audio_lock("a") is s.audio_lock("b")
        (True, False)
        """
        return self._audio_locks.setdefault(self.audio_stream_key(key), asyncio.Lock())

    @staticmethod
    def audio_stream_key(key: str) -> str:
        """音声つきストリームのディレクトリ名

        >>> ImageStream.audio_stream_key("https://soundcloud.com/forss/flickermood")[:12]
        'b3b27cabc573'
        """
        return hashlib.sha256(f"audio-still|{key}".encode()).hexdigest()

    async def cached_audio_stream(
        self, key: str, timeout: float = 30.0
    ) -> RedirectResponse | None:
        """作成済み (または作成中) の音声つきストリームがあればそこへリダイレクトする

        署名付きの音声 URL を取り直さずに済むよう、解決より先に呼べる。
        途中で止まったもの (再起動など、ENDLIST が無く ffmpeg もいない) は消して None。
        """
        stream_key = self.audio_stream_key(key)
        # index.m3u8 に EXT-X-START を足したもの (video-server.py の event_playlist)
        location = f"/video/stream/{stream_key}/{self.EVENT_PLAYLIST}"
        outdir = self.BASE_DIR / stream_key
        playlist = outdir / "index.m3u8"

        running = self.processes.get(stream_key)
        alive = running is not None and running["process"].poll() is None
        if not alive and playlist.exists():
            if "#EXT-X-ENDLIST" not in playlist.read_text(
                encoding="utf-8", errors="replace"
            ):
                logger.info(f"Discarding unfinished audio+still stream: {stream_key}")
                shutil.rmtree(outdir, ignore_errors=True)
                self.processes.pop(stream_key, None)
                return None
        if not (playlist.exists() or alive):
            return None
        logger.info(f"Cache hit for audio+still stream: {stream_key}")
        if running:
            running["last_access"] = time.time()
        await self._wait_for_playlist(stream_key, timeout)
        return RedirectResponse(url=location, status_code=302)

    async def get_with_audio(
        self,
        key: str,
        audio_url: str,
        artwork_url: str | None,
        duration: float | None = None,
        timeout: float = 30.0,
    ) -> RedirectResponse:
        """アートワークの静止画に音声を重ねた HLS を作ってリダイレクトする

        Parameters
        ----------
        key
            キャッシュキーの元 (トラック URL など)。音声 URL は署名付きで毎回変わるので使わない
        audio_url
            ffmpeg が読める音声 (HLS m3u8 など)
        artwork_url
            静止画にする絵。None か取得失敗なら黒背景
        duration
            音声の長さ(秒)。分かれば ``-t`` の上限にする
        timeout
            最初のセグメントを待つ秒数
        """
        cached = await self.cached_audio_stream(key, timeout)
        if cached is not None:
            return cached
        stream_key = self.audio_stream_key(key)
        location = f"/video/stream/{stream_key}/{self.EVENT_PLAYLIST}"
        outdir = self.BASE_DIR / stream_key

        artwork = None
        if artwork_url:
            try:
                async with httpx.AsyncClient(
                    headers={"user-agent": "curl/7.54.1"}, follow_redirects=True
                ) as client:
                    response = await client.get(artwork_url, timeout=5.0)
                    response.raise_for_status()
                    artwork = response.content
            except httpx.HTTPError as e:
                logger.warning(f"Artwork unavailable, using black: {artwork_url}: {e}")
        else:
            logger.info("No artwork, using black background")

        os.makedirs(str(outdir), exist_ok=True)
        frame_path = outdir / "frame.png"
        png = await run_in_threadpool(compose_frame_png, artwork)
        frame_path.write_bytes(png)

        self._cleanup_old_processes()
        self.stream_with_audio(str(frame_path), audio_url, stream_key, duration)
        await self._wait_for_playlist(stream_key, timeout)
        logger.info(f"Audio+still stream ready, redirecting to: {location}")
        return RedirectResponse(url=location, status_code=302)

    @staticmethod
    def with_start_at_beginning(playlist: str) -> str:
        r"""EVENT プレイリストに ``#EXT-X-START:TIME-OFFSET=0`` を足す

        EVENT 型は ENDLIST が付くまでライブ扱いなので、プレイヤーによっては
        後から来た人が「今の端」から再生してしまう。先頭から再生させる。

        >>> src = "#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXTINF:4.0,\nseg_00000.ts\n"
        >>> print(ImageStream.with_start_at_beginning(src), end="")
        #EXTM3U
        #EXT-X-START:TIME-OFFSET=0,PRECISE=YES
        #EXT-X-VERSION:6
        #EXT-X-PLAYLIST-TYPE:EVENT
        #EXTINF:4.0,
        seg_00000.ts
        >>> again = ImageStream.with_start_at_beginning(src)
        >>> ImageStream.with_start_at_beginning(again) == again
        True
        >>> ImageStream.with_start_at_beginning("")
        ''
        """
        if not playlist.startswith("#EXTM3U") or "#EXT-X-START:" in playlist:
            return playlist
        head, _, rest = playlist.partition("\n")
        return f"{head}\n#EXT-X-START:TIME-OFFSET=0,PRECISE=YES\n{rest}"

    async def _wait_for_playlist(self, stream_key: str, timeout: float) -> None:
        """最初のセグメントがプレイリストに載るまで待つ。ffmpeg が落ちたら 502"""
        playlist = self.BASE_DIR / stream_key / "index.m3u8"
        deadline = time.monotonic() + timeout
        while True:
            info = self.processes.get(stream_key)
            exited = info is not None and info["process"].poll() is not None
            try:
                if "#EXTINF" in playlist.read_text(encoding="utf-8"):
                    return
            except (FileNotFoundError, UnicodeDecodeError):
                pass
            if exited:
                logger.error(f"ffmpeg failed for stream: {stream_key}")
                shutil.rmtree(self.BASE_DIR / stream_key, ignore_errors=True)
                del self.processes[stream_key]
                raise HTTPException(status_code=502, detail="Failed to build stream")
            if time.monotonic() > deadline:
                raise HTTPException(status_code=504, detail="Stream did not start")
            await asyncio.sleep(0.1)

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
                headers={"user-agent": "curl/7.54.1"}, follow_redirects=True
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

    async def get_slideshow(
        self,
        urls: list[str],
        duration: int,
        loop_count: int,
        local_paths: dict[str, str] | None = None,
    ) -> RedirectResponse:
        """複数画像からスライドショーストリームを生成

        Parameters
        ----------
        urls : list[str]
            画像URLのリスト（2-10枚）
        duration : int
            各画像の表示秒数
        loop_count : int
            スライドショーのループ回数
        local_paths : dict[str, str] | None
            アップロード済み画像の URL からローカルパスへの対応。
            指定された URL は HTTP で取りに行かない。
        """
        if len(urls) < 2:
            raise ValueError("Slideshow requires at least 2 images")
        if len(urls) > 10:
            raise ValueError("Slideshow supports maximum 10 images")

        # ストリームキー生成（URL順序 + duration + loop_count）
        key_parts = json.dumps(urls) + f"|duration={duration}|loop={loop_count}"
        stream_key = hashlib.sha256(key_parts.encode()).hexdigest()
        logger.info(f"Generated slideshow stream key: {stream_key}")

        # キャッシュチェック
        if os.path.exists(self.BASE_DIR / stream_key / "index.m3u8"):
            logger.info(f"Cache hit for slideshow: {stream_key}")
            if stream_key in self.processes:
                self.processes[stream_key]["last_access"] = time.time()
            return RedirectResponse(
                url=f"/video/stream/{stream_key}/index.m3u8", status_code=302
            )

        # 全画像をダウンロード
        logger.info(f"Cache miss, downloading {len(urls)} images")
        with tempfile.TemporaryDirectory(delete=False) as temp_dir:
            image_paths = []
            async with httpx.AsyncClient(
                headers={"user-agent": "curl/7.54.1"}, follow_redirects=True
            ) as client:
                for idx, url in enumerate(urls):
                    image_path = str(Path(temp_dir) / f"image_{idx:03d}.jpg")
                    if local_paths and url in local_paths:
                        shutil.copyfile(local_paths[url], image_path)
                        image_paths.append(image_path)
                        logger.info(
                            f"Using uploaded image {idx + 1}/{len(urls)}: {url}"
                        )
                        continue
                    try:
                        response = await client.get(url, timeout=5.0)
                        response.raise_for_status()
                        with open(image_path, "wb") as f:
                            f.write(response.content)
                        image_paths.append(image_path)
                        logger.info(f"Downloaded image {idx + 1}/{len(urls)}: {url}")
                    except httpx.RequestError as e:
                        logger.error(f"Failed to download {url}: {e}")
                        raise HTTPException(
                            status_code=400, detail=f"Failed to download: {url}"
                        )

        # スライドショーストリーム作成
        logger.info(f"Creating slideshow stream: {stream_key} (loop={loop_count})")
        self._cleanup_old_processes()
        _ = self.stream_slideshow(image_paths, stream_key, duration, loop_count)

        # プレイリスト作成待ち
        playlist = f"./stream/{stream_key}/index.m3u8"
        logger.info(f"Waiting for playlist creation: {playlist}")
        while not os.path.exists(playlist) or os.path.getsize(playlist) <= 0:
            await asyncio.sleep(0.1)

        logger.info(
            f"Slideshow ready, redirecting to: /video/stream/{stream_key}/index.m3u8"
        )
        return RedirectResponse(
            url=f"/video/stream/{stream_key}/index.m3u8", status_code=302
        )
