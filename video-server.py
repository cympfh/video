import hashlib
import subprocess
import tempfile
from enum import Enum
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response

import util

app = FastAPI(title="video")


class UrlType(Enum):
    Image = "image"
    Video = "video"
    YouTubeSearch = "youtube_search"

    @classmethod
    async def from_url(cls, url: str) -> "UrlType":
        # YouTube検索? (y!{keyword})
        if url.startswith("y!"):
            return cls.YouTubeSearch

        # 画像?
        async with httpx.AsyncClient() as client:
            response = await client.head(url, timeout=2.0)
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("image/"):
                return cls.Image

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
    print(f"URL: {url}, Type: {url_type}")
    if url_type == UrlType.Image:
        return await ImageResponse(url=url)
    elif url_type == UrlType.YouTubeSearch:
        url_part = url[2:]  # y! の後の部分を取得

        # y!{keyword}!{index} の場合は特定の動画を取得
        if "!" in url_part:
            parts = url_part.split("!")
            keyword = parts[0]
            try:
                index = int(parts[1])
                youtube = util.YouTube()
                video_info = await youtube.get_from_search(keyword, index)
                print(f"Redirecting to YouTube video: {video_info['url']}")
                return RedirectResponse(video_info["url"])
            except (ValueError, IndexError):
                # インデックスが無効な場合は検索結果画像を表示
                pass

        # y!{keyword} の場合は検索結果画像を表示
        keyword = url_part.split("!")[0]  # !があっても最初の部分をキーワードとする
        image_path = await util.YouTube().search_result(keyword)
        return await ImageResponse(path=image_path)
    else:
        url = convert(url)
        return RedirectResponse(url)


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

    # それ以外はそのまま返す
    return url


def image_to_video(image_path: str, video_path: str) -> None:
    """ffmpeg で動画に変換

    Parameters
    ----------
    image_path : str
        入力画像のパス
    video_path : str
        出力動画のパス
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        image_path,
        "-c:v",
        "libx264",
        "-t",
        "15",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        video_path,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    print(f"ffmpeg finished with statuscode={result.returncode}")
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)


async def ImageResponse(url: str | None = None, path: str | None = None) -> Response:
    """画像URLまたはローカルパスから画像データを取得してレスポンスとして返す

    Parameters
    ----------
    url : str, optional
        画像のURL
    path : str, optional
        ローカル画像ファイルのパス

    Returns
    -------
    Response
        画像データのレスポンス
    """
    if url is None and path is None:
        return Response(
            content=b"Either url or path must be provided",
            status_code=400,
            media_type="text/plain",
        )

    if url is not None and path is not None:
        return Response(
            content=b"Cannot specify both url and path",
            status_code=400,
            media_type="text/plain",
        )
    # キャッシュディレクトリの作成
    cache_dir = Path("cache")
    cache_dir.mkdir(exist_ok=True)

    # キャッシュキーを計算（URLまたはパス）
    cache_key = ""
    if url is not None:
        cache_key = hashlib.sha256(url.encode()).hexdigest()
    elif path is not None:
        cache_key = hashlib.sha256(path.encode()).hexdigest()

    cached_file = cache_dir / f"{cache_key}.mp4"

    # キャッシュファイルが存在する場合は返す
    if cached_file.exists():
        with open(cached_file, "rb") as f:
            video_data = f.read()
        return Response(
            content=video_data,
            media_type="video/mp4",
            headers={"Content-Disposition": "inline; filename=image_video.mp4"},
        )

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_file = Path(temp_dir) / "image.jpg"

            if url is not None:
                # URLから画像をダウンロード
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=5.0)
                    response.raise_for_status()
                    with open(image_file, "wb") as f:
                        f.write(response.content)
            else:
                # ローカルをそのまま使う
                image_file = path

            image_to_video(str(image_file), str(cached_file))

            with open(cached_file, "rb") as f:
                video_data = f.read()
            return Response(
                content=video_data,
                media_type="video/mp4",
                headers={"Content-Disposition": "inline; filename=image_video.mp4"},
            )

    except subprocess.CalledProcessError:
        return Response(
            content=b"Video conversion failed", status_code=500, media_type="text/plain"
        )
    except Exception:
        return Response(
            content=b"Image processing failed", status_code=404, media_type="text/plain"
        )
