from fastapi import FastAPI
from fastapi.responses import RedirectResponse

app = FastAPI(title="video")


@app.get("/")
async def root(url: str):
    """Redirect API

    Parameters
    ----------
    url
        リダイレクト先のURL
        このAPIはただリダイレクトだけする
    """
    url = convert(url)
    return RedirectResponse(url)


def convert(url: str) -> str:
    """一部URLを専用URLに変換する

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
