"""Turn a (square) cover image into a 16:9 still frame for audio-only tracks.

The artwork sits in the middle at full height. The rest of the frame is the
same picture scaled up to cover, blurred, and darkened, so the sides are not
plain bars. Without artwork (missing, or not decodable) the frame is black.
"""

import io

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

FRAME_SIZE = (960, 540)


def compose_frame(
    artwork: bytes | None,
    size: tuple[int, int] = FRAME_SIZE,
    circle_radius: int | None = None,
) -> Image.Image:
    """16:9 の静止画フレームを作る

    ``circle_radius`` を渡すと、絵は中央の半径 ``circle_radius`` の円に切り抜く
    (``effect=circle`` で周りに波形の輪を描くため)。

    Examples
    --------
    アートワークなし、または読めないデータは黒一色になる

    >>> frame = compose_frame(None)
    >>> frame.size, frame.mode, frame.getextrema()
    ((960, 540), 'RGB', ((0, 0), (0, 0), (0, 0)))
    >>> compose_frame(b"not an image").getpixel((480, 270))
    (0, 0, 0)

    正方形の絵は中央に高さいっぱいで置かれ、左右はぼかした背景で埋まる

    >>> buf = io.BytesIO()
    >>> Image.new("RGB", (500, 500), (200, 40, 40)).save(buf, format="PNG")
    >>> frame = compose_frame(buf.getvalue())
    >>> frame.size
    (960, 540)
    >>> frame.getpixel((480, 270))
    (200, 40, 40)
    >>> frame.getpixel((214, 270)) == (200, 40, 40)  # 絵の左端の内側
    True
    >>> left = frame.getpixel((10, 270))  # 背景: 同じ色を暗くしたもの
    >>> left != (200, 40, 40) and left[0] > left[1] and left[0] < 200
    True

    横長の絵は幅に合わせて縮み、上下が背景になる

    >>> buf = io.BytesIO()
    >>> Image.new("RGB", (1920, 400), (10, 120, 10)).save(buf, format="PNG")
    >>> frame = compose_frame(buf.getvalue())
    >>> frame.getpixel((480, 270))
    (10, 120, 10)
    >>> frame.getpixel((480, 5)) != (10, 120, 10)
    True

    円に切り抜くと、中心は絵、円の外は背景になる

    >>> buf = io.BytesIO()
    >>> Image.new("RGB", (500, 500), (200, 40, 40)).save(buf, format="PNG")
    >>> frame = compose_frame(buf.getvalue(), circle_radius=100)
    >>> frame.getpixel((480, 270)), frame.getpixel((480, 270 - 95))
    ((200, 40, 40), (200, 40, 40))
    >>> frame.getpixel((480, 270 - 140)) != (200, 40, 40)
    True
    """
    width, height = size
    canvas = Image.new("RGB", size, (0, 0, 0))
    if not artwork:
        return canvas
    try:
        with Image.open(io.BytesIO(artwork)) as opened:
            opened.seek(0)
            picture = opened.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return canvas
    if picture.width <= 0 or picture.height <= 0:
        return canvas

    # 背景: はみ出すまで拡大してぼかし、少し暗くする
    cover = max(width / picture.width, height / picture.height)
    bg = picture.resize(
        (max(1, round(picture.width * cover)), max(1, round(picture.height * cover))),
        Image.Resampling.BILINEAR,
    )
    left = (bg.width - width) // 2
    top = (bg.height - height) // 2
    bg = bg.crop((left, top, left + width, top + height))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(width, height) // 40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    canvas.paste(bg, (0, 0))

    if circle_radius:
        # 前景: 正方形に切り出して円形のマスクで中央へ
        d = 2 * circle_radius
        cover = max(d / picture.width, d / picture.height)
        fg = picture.resize(
            (
                max(d, round(picture.width * cover)),
                max(d, round(picture.height * cover)),
            ),
            Image.Resampling.LANCZOS,
        )
        left = (fg.width - d) // 2
        top = (fg.height - d) // 2
        fg = fg.crop((left, top, left + d, top + d))
        # 4 倍で描いて縮め、縁をなめらかにする
        mask = Image.new("L", (d * 4, d * 4), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
        mask = mask.resize((d, d), Image.Resampling.LANCZOS)
        canvas.paste(fg, ((width - d) // 2, (height - d) // 2), mask)
        return canvas

    # 前景: 枠に収まるように拡大縮小して中央へ
    contain = min(width / picture.width, height / picture.height)
    fg = picture.resize(
        (
            max(1, round(picture.width * contain)),
            max(1, round(picture.height * contain)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
    return canvas


def compose_frame_png(
    artwork: bytes | None,
    size: tuple[int, int] = FRAME_SIZE,
    circle_radius: int | None = None,
) -> bytes:
    """``compose_frame`` を PNG バイト列で返す

    >>> compose_frame_png(None)[:8]
    b'\\x89PNG\\r\\n\\x1a\\n'
    """
    buf = io.BytesIO()
    compose_frame(artwork, size, circle_radius).save(buf, format="PNG")
    return buf.getvalue()
