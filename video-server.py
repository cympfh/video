import logging
from pathlib import Path
from enum import Enum

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import util
from util.streaming.uploads import (
    UploadStore,
    content_type_for,
    default_upload_dir,
    detect_image,
    downscale_image,
    public_stream_url,
    resolve_stored_upload,
    MAX_UPLOAD_BYTES,
)

logger = logging.getLogger("uvicorn")
istream = util.ImageStream()
upload_store = UploadStore(default_upload_dir())
upload_store.ensure()
app = FastAPI(title="video")


class UrlType(Enum):
    Image = "image"
    Video = "video"
    YouTubeSearch = "youtube_search"
    Random = "random"
    RandomLive = "random-live"

    @classmethod
    async def from_url(cls, url: str) -> "UrlType":
        """URL種別を判定する"""
        # Prefer random-live over random (longer alias first)
        if url.startswith("random-live") or (
            url.startswith("random-") and "random-live".startswith(url)
        ):
            return cls.RandomLive
        if "random".startswith(url):
            return cls.Random

        # YouTube検索? (y!{keyword})
        if url.startswith("y!"):
            return cls.YouTubeSearch

        # Invalid URL
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Invalid URL")

        # 画像?
        if url.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return cls.Image
        async with httpx.AsyncClient() as client:
            try:
                response = await client.head(
                    url, headers={"Accept": "*/*"}, timeout=2.0
                )
                content_type = response.headers.get("content-type", "")
                if content_type.startswith("image/"):
                    return cls.Image
            except httpx.RequestError:
                logger.warning(f"Failed to fetch URL header: {url}")
                raise HTTPException(
                    status_code=400, detail="Failed to fetch URL header"
                )

        # その他は動画と見做す
        return cls.Video


INDEX_HTML = Path(__file__).parent / "static" / "index.html"
FAVICON_ICO = Path(__file__).parent / "static" / "favicon.ico"
FAVICON_PNG = Path(__file__).parent / "static" / "favicon.png"


def index_page() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/video/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return FileResponse(FAVICON_ICO)


@app.get("/favicon.png", include_in_schema=False)
@app.get("/video/favicon.png", include_in_schema=False)
async def favicon_png():
    return FileResponse(FAVICON_PNG)


def _trusted_upload_hosts(request: Request) -> set[str]:
    hosts = {"s.cympfh.cc", "localhost", "127.0.0.1"}
    if request.url.hostname:
        hosts.add(request.url.hostname.lower())
    return hosts


def _local_upload(url: str, request: Request) -> Path | None:
    """Local file for a trusted upload URL, or None when ``url`` is not one."""
    try:
        return resolve_stored_upload(
            url,
            upload_store.directory,
            trusted_hosts=_trusted_upload_hosts(request),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Uploaded image not found")


@app.get("/")
async def root(
    request: Request,
    url: list[str] | None = Query(None),
    interval: int = Query(8, ge=1, le=30),
    loop: int = Query(100, ge=1),
    p: int | None = Query(None, ge=1),
):
    """Redirect API

    Parameters
    ----------
    url
        画像/動画のURL（複数指定可能）
        2つ以上の場合はスライドショーモード
    interval
        スライドショーの画像切り替え間隔（秒）
    loop
        スライドショーのループ回数（デフォルト: 100）
    p
        ビリビリ動画のページ番号（シリーズの何本目か、1始まり）
    """
    if not url:
        return index_page()

    # スライドショーモード判定
    if len(url) >= 2:
        logger.info(
            f"Slideshow mode: {len(url)} images, duration={interval}s, loop={loop}"
        )
        local_paths = {}
        for item in url:
            local = _local_upload(item, request)
            if local is not None:
                local_paths[item] = str(local)
        return await istream.get_slideshow(
            urls=url,
            duration=interval,
            loop_count=loop,
            local_paths=local_paths or None,
        )

    # 単一URL（既存の動作）
    url: str = url[0]
    url_type = await UrlType.from_url(url)
    logger.info(f"Accepted {url_type}({url})")

    match url_type:
        case UrlType.Video:
            converted_url = await convert(url, p=p)
            logger.info(f"Video URL converted: {url} -> {converted_url}")
            return RedirectResponse(converted_url)

        case UrlType.Random:
            video_url = await util.Random().get()
            converted_url = await convert(video_url)
            logger.info(f"A random video chosen: {converted_url}")
            return RedirectResponse(converted_url)

        case UrlType.RandomLive:
            try:
                live_url = await util.RandomLive().get()
            except LookupError as e:
                raise HTTPException(status_code=404, detail=str(e))
            converted_url = await convert(live_url)
            logger.info(f"A random live stream chosen: {converted_url}")
            return RedirectResponse(converted_url)

        case UrlType.Image:
            local = _local_upload(url, request)
            if local is not None:
                logger.info(f"Streaming an uploaded image: {local.name}")
                return await istream.get(path=str(local))
            logger.info(f"Streaming an image: {url}")
            return await istream.get(url=url)

        case UrlType.YouTubeSearch:
            url_part = url[2:]  # y! の後の部分を取得

            # y!{keyword}!{index} の場合は特定の動画を取得
            if "!" in url_part:
                parts = url_part.split("!")
                keyword = parts[0]
                try:
                    index = int(parts[1])
                    youtube = util.YouTube()
                    video_info = await youtube.get_from_search(keyword, index)
                    logger.info(f"Redirecting to YouTube video: {video_info['url']}")
                    return RedirectResponse(video_info["url"])
                except (ValueError, IndexError):
                    # インデックスが無効な場合は検索結果画像を表示
                    pass

            # y!{keyword} の場合は検索結果画像を表示
            keyword = url_part.split("!")[0]  # !があっても最初の部分をキーワードとする
            logger.info(f"YouTube search for keyword: {keyword}")
            image_path = await util.YouTube().search_result(keyword)
            return await istream.get(path=image_path)


@app.get("/video")
async def video(
    request: Request,
    url: list[str] | None = Query(None),
    interval: int = Query(8, ge=1, le=30),
    loop: int = Query(100, ge=1),
    p: int | None = Query(None, ge=1),
):
    if not url:
        return index_page()
    return await root(request, url, interval, loop, p)


async def convert(url: str, p: int | None = None) -> str:
    """一部動画URLを専用URLに変換する

    Parameters
    ----------
    url
        変換対象の動画URL
    p
        ビリビリ動画のページ番号（シリーズの何本目か、1始まり）

    Examples
    --------
    ニコニコ動画
    >>> import asyncio
    >>> asyncio.run(convert("https://www.nicovideo.jp/watch/sm44886216"))
    'https://www.nicovideo.life/watch?v=sm44886216'

    >>> asyncio.run(convert("https://www.nicovideo.jp/watch/sm44886216?hoge=fuga"))
    'https://www.nicovideo.life/watch?v=sm44886216'

    ビリビリ動画
    >>> asyncio.run(convert("https://www.bilibili.com/video/BV1smLczPEa5/?spm_id_from=333.1007.tianma.1-1-1.click"))
    'https://biliplayer.91vrchat.com/player/?url=https://www.bilibili.com/video/BV1smLczPEa5/?spm_id_from=333.1007.tianma.1-1-1.click'

    >>> asyncio.run(convert("https://www.bilibili.com/video/BV1y13462ESA", p=4))
    'https://biliplayer.91vrchat.com/player/?url=https://www.bilibili.com/video/BV1y13462ESA&p=4'

    iwara
    >>> asyncio.run(convert("https://www.iwara.tv/video/rdcIORhbbfaf15"))
    'https://nicovrc.net/?url=https://www.iwara.tv/video/rdcIORhbbfaf15'

    それ以外はそのまま返す
    >>> asyncio.run(convert("https://www.youtube.com/watch?v=abcd"))
    'https://www.youtube.com/watch?v=abcd'
    """

    # ニコニコ動画
    if "nicovideo.jp/watch/" in url:
        video_id = url.split("/")[-1].split("?")[0]
        return f"https://www.nicovideo.life/watch?v={video_id}"

    # ビリビリ動画
    if "bilibili.com/video/" in url:
        result = f"https://biliplayer.91vrchat.com/player/?url={url}"
        if p is not None:
            result += f"&p={p}"
        return result

    # iwara
    if "iwara.tv/video/" in url:
        return f"https://nicovrc.net/?url={url}"

    # hanimeone / hanime1: CDN は Referer がないと囮の m3u8 を返す。
    # 署名付き mp4 をこちらで中継する。
    hanime = util.Hanime()
    if hanime.is_watch(url):
        try:
            media = await hanime.resolve(url)
        except (LookupError, httpx.HTTPError, ValueError) as e:
            logger.warning(f"Failed to resolve hanime video: {url}: {e}")
            raise HTTPException(
                status_code=502, detail="Failed to resolve hanime video"
            )
        logger.info(
            f"Hanime {media.video_id} resolved at {media.quality}p via {media.referer}"
        )
        return hanime.proxy_path(url)

    # X (Twitter): syndication で mp4 を直接返す。nicovrc は使わない。
    x = util.X()
    if x.is_status(url):
        try:
            return await x.resolve_mp4(url)
        except (LookupError, httpx.HTTPError, ValueError) as e:
            logger.warning(f"Failed to resolve X video: {url}: {e}")
            raise HTTPException(status_code=502, detail="Failed to resolve X video")

    # それ以外はそのまま返す
    return url


@app.api_route(
    "/video/hanime/{video_id}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def hanime_media(video_id: str, request: Request):
    if not video_id.isdigit():
        raise HTTPException(status_code=400, detail="Invalid hanime id")

    hanime = util.Hanime()
    try:
        media = hanime.cached(video_id) or await hanime.resolve(
            f"https://hanimeone.me/watch?v={video_id}"
        )
    except (LookupError, httpx.HTTPError, ValueError) as e:
        logger.warning(f"Failed to resolve hanime video: {video_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to resolve hanime video")

    range_header = None if request.method == "HEAD" else request.headers.get("range")
    headers = hanime.upstream_headers(media, range_header)
    timeout = httpx.Timeout(20.0, read=120.0)
    client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
    try:
        upstream = await client.send(
            client.build_request(request.method, media.media_url, headers=headers),
            stream=True,
        )
    except httpx.HTTPError as e:
        await client.aclose()
        hanime.invalidate(video_id)
        logger.warning(f"Failed to fetch hanime video: {video_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to resolve hanime video")

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code not in (200, 206) or "mpegurl" in content_type:
        await upstream.aclose()
        await client.aclose()
        hanime.invalidate(video_id)
        logger.warning(
            f"Hanime upstream rejected: {video_id}: "
            f"{upstream.status_code} {content_type}"
        )
        raise HTTPException(status_code=502, detail="Failed to resolve hanime video")

    out_headers: dict[str, str] = {"content-type": "video/mp4"}
    for key in (
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
    ):
        value = upstream.headers.get(key)
        if value:
            out_headers[key] = value
    out_headers.setdefault("accept-ranges", "bytes")

    if request.method == "HEAD":
        await upstream.aclose()
        await client.aclose()
        return Response(status_code=upstream.status_code, headers=out_headers)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(), status_code=upstream.status_code, headers=out_headers
    )


async def _read_upload(upload: UploadFile) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        if len(buffer) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds 20MB")
        buffer.extend(chunk)
    return bytes(buffer)


@app.post("/video/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Save an image and return a URL that ImageStream can play.

    ``url`` is a ``/video?url=...`` link, the same kind of URL the form builds
    for a remote image. ``path`` is the stored file, used to fill that form.
    """
    data = await _read_upload(file)
    kind = detect_image(data, file.content_type)
    if kind is None:
        raise HTTPException(status_code=400, detail="Only image files are accepted")
    try:
        data = downscale_image(data, kind)
    except (OSError, ValueError) as exc:
        logger.warning(f"Failed to downscale upload: {exc}")
        raise HTTPException(status_code=400, detail="Only image files are accepted")
    record = upload_store.add(data, kind)
    base = str(request.base_url).rstrip("/")
    logger.info(f"Stored upload {record.name} ({len(data)} bytes)")
    return {
        "url": public_stream_url(base, record.name),
        "path": f"/video/uploads/{record.name}",
    }


@app.get("/video/uploads/{name}")
async def uploaded_image(name: str):
    path = upload_store.get(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Uploaded image not found")
    return FileResponse(
        path,
        media_type=content_type_for(name),
        headers={"x-content-type-options": "nosniff"},
    )


# ImageStream
app.mount("/video/stream", StaticFiles(directory="stream/"), name="stream")
