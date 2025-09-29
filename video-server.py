import logging
from enum import Enum

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import util

logger = logging.getLogger("uvicorn")
istream = util.ImageStream()
app = FastAPI(title="video")


class UrlType(Enum):
    Image = "image"
    Video = "video"
    YouTubeSearch = "youtube_search"
    Random = "random"

    @classmethod
    async def from_url(cls, url: str) -> "UrlType":
        """URL種別を判定する"""
        if url == "random":
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


@app.get("/")
async def root(url: str):
    """Redirect API

    Parameters
    ----------
    url
        リダイレクト先のURL
        このAPIはただリダイレクトだけする
    """
    url_type = await UrlType.from_url(url)
    logger.info(f"Accepted {url_type}({url})")

    match url_type:
        case UrlType.Video:
            converted_url = convert(url)
            logger.info(f"Video URL converted: {url} -> {converted_url}")
            return RedirectResponse(converted_url)

        case UrlType.Random:
            video_url = await util.Random().get()
            converted_url = convert(video_url)
            logger.info(f"A random video chosen: {converted_url}")
            return RedirectResponse(converted_url)

        case UrlType.Image:
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
async def video(url: str):
    return await root(url)


def convert(url: str) -> str:
    """一部動画URLを専用URLに変換する

    Parameters
    ----------
    url
        変換対象の動画URL

    Examples
    --------
    ニコニコ動画
    >>> convert("https://www.nicovideo.jp/watch/sm44886216")
    'https://www.nicovideo.life/watch?v=sm44886216'

    >>> convert("https://www.nicovideo.jp/watch/sm44886216?hoge=fuga")
    'https://www.nicovideo.life/watch?v=sm44886216'

    ビリビリ動画
    >>> convert("https://www.bilibili.com/video/BV1smLczPEa5/?spm_id_from=333.1007.tianma.1-1-1.click")
    'https://biliplayer.91vrchat.com/player/?url=https://www.bilibili.com/video/BV1smLczPEa5/?spm_id_from=333.1007.tianma.1-1-1.click'

    それ以外はそのまま返す
    >>> convert("https://www.youtube.com/watch?v=abcd")
    'https://www.youtube.com/watch?v=abcd'
    """

    # ニコニコ動画
    if "nicovideo.jp/watch/" in url:
        video_id = url.split("/")[-1].split("?")[0]
        return f"https://www.nicovideo.life/watch?v={video_id}"

    # ビリビリ動画
    if "bilibili.com/video/" in url:
        return f"https://biliplayer.91vrchat.com/player/?url={url}"

    # X (Twitter)
    if "x.com/" in url:
        return f"https://nicovrc.net/proxy/?{url}"

    # それ以外はそのまま返す
    return url


# ImageStream
app.mount("/video/stream", StaticFiles(directory="stream/"), name="stream")
