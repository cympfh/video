"""SoundCloud の映像に重ねる、音に合わせて動く演出 (``&effect=...``)

どれも本番の ffmpeg 5.1 にあるフィルタだけで描く。色は虹色の帯 (``_rainbow``) を
波形やバーの形で切り抜いて付け、ぼかしを下に敷いて光らせる。

- ``wave``: ``showwaves`` の波形を画面の下に。色相が横に一周し、時間とともに流れる
- ``spectrum``: ``showcqt`` をブロックのバーにして画面の下に。低音が赤、高音が紫
- ``circle``: ``showwaves`` を左右反転でつないだ帯を ``remap`` で輪に曲げ、
  円く切り抜いたアートワークのまわりに置く。色相が輪を一周する
- ``volume``: ``showvolume`` の左右の音量メーターを右端に

入力 0 が静止画 (ループ)、入力 1 が音声。``circle`` は ``remap`` の座標マップを
入力 2, 3 として読む。
"""

import array
import math
import os
import sys
import tempfile
from pathlib import Path

from util.streaming.artwork import FRAME_SIZE

EFFECTS = ("wave", "spectrum", "circle", "volume")
EFFECT_FPS = 15

# circle: 絵は半径 CIRCLE_ART_RADIUS の円、波形の輪は内側 CIRCLE_RING[0] から外側 CIRCLE_RING[1]
CIRCLE_ART_RADIUS = 170
CIRCLE_RING = (176, 262)
# 輪の元になる横長の波形 (半周分。左右反転して 1 周にするので継ぎ目が出ない)
CIRCLE_WAVE_SIZE = (720, 86)
# spectrum: バーの本数
SPECTRUM_BARS = 64
# 帯の高さと強さ。音圧の高い曲でもバーや波形が帯の上端で切れないように選んだ
# (flickermood と Pegboard Nerds - Disconnected で上端に当たるコマが 1% 以下)
WAVE_HEIGHT = 200
WAVE_GAIN = 0.65
SPECTRUM_HEIGHT = 216  # 画面の下 4 割
SPECTRUM_GAIN = 10
# volume: RMS の -VOLUME_RANGE_DB..0 dB を高さいっぱいに割り当てる。
# -6 dB 以上で赤、-18 dB 以上で黄、それ未満は緑
VOLUME_RANGE_DB = 36
VOLUME_RED_DB = -6
VOLUME_YELLOW_DB = -18


def _volume_position(db: float) -> float:
    """showvolume の ``ds=log`` で dB が何割の高さになるか (``0.21 * log10(x) + 1``)

    >>> _volume_position(0), round(_volume_position(-36), 3)
    (1.0, 0.622)
    """
    return 1 + 0.21 * db / 20


# wave / spectrum の不透明度 (濃さ)。サムネが少し透けて見えるよう 0.7
OVERLAY_OPACITY = 0.7


def parse_effect(value: str | None) -> str | None:
    """``effect`` クエリを正規化する。空なら None (従来どおり静止画だけ)

    >>> parse_effect(None) is None, parse_effect("") is None, parse_effect("  ") is None
    (True, True, True)
    >>> parse_effect("wave"), parse_effect(" Spectrum ")
    ('wave', 'spectrum')
    >>> parse_effect("sparkle")
    Traceback (most recent call last):
    ...
    ValueError: Unknown effect 'sparkle'. Choose one of: wave, spectrum, circle, volume
    """
    if value is None or not value.strip():
        return None
    name = value.strip().lower()
    if name not in EFFECTS:
        raise ValueError(
            f"Unknown effect {value.strip()!r}. Choose one of: {', '.join(EFFECTS)}"
        )
    return name


def effect_filtergraph(
    effect: str, size: tuple[int, int] = FRAME_SIZE, fps: int = EFFECT_FPS
) -> str:
    """``-filter_complex`` の文字列。出力は ``[v]`` (yuv420p)

    Examples
    --------
    >>> g = effect_filtergraph("wave")
    >>> "[1:a]volume=0.65,showwaves=s=960x200" in g and g.endswith("format=yuv420p[v]")
    True

    白一色ではなく、虹色の帯を形で切り抜いて光らせる

    >>> all("alphamerge" in effect_filtergraph(e) and _GLOW in effect_filtergraph(e)
    ...     for e in ("wave", "spectrum", "circle"))
    True

    wave と spectrum はサムネが透けるよう OVERLAY_OPACITY を掛ける (circle は掛けない)

    >>> [_FADE in effect_filtergraph(e) for e in EFFECTS]
    [True, True, False, False]
    >>> _FADE
    'colorchannelmixer=aa=0.7'

    >>> g = effect_filtergraph("spectrum")
    >>> "showcqt" in g and "a_weighting(f)" in g and "colorkey" not in g
    True
    >>> g = effect_filtergraph("circle")
    >>> "[2:v]" in g and "[3:v]" in g and "remap" in g and "hflip" in g
    True
    >>> g = effect_filtergraph("volume")
    >>> "showvolume" in g and ":m=r:ds=log:" in g and "crop=iw:460:0:0" in g
    True
    >>> "if(gte(PEAK,0.9370)" in g and "if(gte(PEAK,0.8110)" in g
    True
    >>> all(f"r={EFFECT_FPS}" in effect_filtergraph(e) for e in EFFECTS)
    True
    >>> effect_filtergraph("sparkle")
    Traceback (most recent call last):
    ...
    ValueError: Unknown effect 'sparkle'. Choose one of: wave, spectrum, circle, volume
    """
    effect = parse_effect(effect)
    width, height = size
    if effect == "wave":
        h = WAVE_HEIGHT
        # 音を WAVE_GAIN 倍してから描く (sqrt 目盛でも帯の上下に当たらない)
        return (
            f"[1:a]volume={WAVE_GAIN},showwaves=s={width}x{h}:mode=cline:r={fps}:scale=sqrt:draw=full"
            f":colors=white,format=rgba,alphaextract[m];"
            f"{_rainbow(width, h, fps)}[c];"
            f"[c][m]alphamerge,{_GLOW},{_FADE}[fx];"
            f"[0:v][fx]overlay=0:{height - h - 8}:format=auto,format=yuv420p[v]"
        )
    if effect == "spectrum":
        h = SPECTRUM_HEIGHT
        bars = SPECTRUM_BARS
        step = width // bars
        # showcqt の明るさを帯ごとに平均して二値にし、ブロックのバーにする。
        # 低音ばかり伸びないよう A 特性の平方根で重み付けする。
        # 色はバーの位置 (低音=赤 → 高音=紫) で決める
        return (
            f"[1:a]showcqt=s={width}x{h}:r={fps}:bar_h={h}:sono_h=0:axis=0"
            f":bar_g=2:bar_v={SPECTRUM_GAIN}*sqrt(a_weighting(f)):count=2"
            f":basefreq=30:endfreq=8000,"
            f"format=gray,scale={bars}x{h}:flags=area,"
            f"lut=y='if(gt(val,25),255,0)',"
            f"scale={width}x{h}:flags=neighbor[bars];"
            f"nullsrc=s={width}x{h}:r={fps},format=gray,"
            f"geq=lum='if(lt(mod(X,{step}),{step - 3})*lt(mod(Y,8),6),255,0)',"
            f"loop=loop=-1:size=1[grid];"
            f"[bars][grid]blend=all_mode=multiply[m];"
            f"{_rainbow(width, h, fps, period=0, span=5)}[c];"
            f"[c][m]alphamerge,{_GLOW},{_FADE}[fx];"
            f"[0:v][fx]overlay=0:{height - h}:format=auto,format=yuv420p[v]"
        )
    if effect == "circle":
        w, h = CIRCLE_WAVE_SIZE
        side = 2 * CIRCLE_RING[1]
        return (
            f"[1:a]showwaves=s={w}x{h}:mode=cline:r={fps}:scale=sqrt:draw=full"
            f":colors=white,format=rgba,alphaextract,split[w1][w2];[w2]hflip[w3];"
            f"[w1][w3]hstack[m];"
            f"{_rainbow(2 * w, h, fps)}[c];"
            f"[c][m]alphamerge,format=rgba[strip];"
            f"[strip][2:v][3:v]remap=fill=black@0,{_GLOW}[fx];"
            f"[0:v][fx]overlay={(width - side) // 2}:{(height - side) // 2}"
            f":format=auto,format=yuv420p[v]"
        )
    # volume: 音圧 (RMS) を dB で。showvolume の ds=log は約 95 dB を高さに詰めるので
    # 曲だとほぼ上まで届いてしまう。上から VOLUME_RANGE_DB 分だけ切り出して使う。
    # 色の式の PEAK はその画素の高さ (0..1) なので、閾値も高さに直して塗り分ける
    meter = height - 80
    bottom = _volume_position(-VOLUME_RANGE_DB)
    full = round(meter / (1 - bottom))
    red = _volume_position(VOLUME_RED_DB)
    yellow = _volume_position(VOLUME_YELLOW_DB)
    color = (
        f"if(gte(PEAK,{red:.4f}),0xff0000ff,"
        f"if(gte(PEAK,{yellow:.4f}),0xff00ffff,0xff00ff00))"
    )
    return (
        f"[1:a]showvolume=r={fps}:o=v:w={full}:h=14:b=2:t=0:v=0:f=0.9:p=0"
        f":m=r:ds=log:c='{color}',crop=iw:{meter}:0:0,"
        f"format=rgba,colorchannelmixer=aa=0.85[fx];"
        f"[0:v][fx]overlay=W-w-24:(H-h)/2:format=auto,format=yuv420p[v]"
    )


# ぼかした同じ絵を下に敷いて光らせる (ネオン風)。ぼかしは 1/4 に縮めてから掛けて軽くする
_GLOW = (
    "split[g1][g2];[g2]scale=iw/4:ih/4,boxblur=2:1,scale=iw*4:ih*4[g3];"
    "[g3][g1]overlay=format=auto"
)


# 光らせたあと全体の不透明度を OVERLAY_OPACITY 倍にする
_FADE = f"colorchannelmixer=aa={OVERLAY_OPACITY}"


def _rainbow(
    width: int, height: int, fps: int, period: float = 6, span: float = 6
) -> str:
    """横方向に色相が変わる虹色の帯 (彩度・明度は最大)。``span`` 6 で一周、5 で赤から紫まで

    1 枚だけ ``geq`` で作って ``loop`` で使い回す。``period`` 秒で色相が一周して流れる
    (0 なら止まったまま)。

    >>> g = _rainbow(960, 140, 15)
    >>> g.startswith("nullsrc=s=960x140:r=15,format=rgb24,geq=") and "loop=loop=-1:size=1" in g
    True
    >>> "hue=H=" in g, "hue=H=" in _rainbow(960, 140, 15, period=0)
    (True, False)
    >>> "5*X/960" in _rainbow(960, 140, 15, span=5)  # 赤から紫まで (一周しない)
    True
    """
    hue = f"{span:g}*X/{width}"
    graph = (
        f"nullsrc=s={width}x{height}:r={fps},format=rgb24,"
        f"geq=r='255*clip(abs({hue}-3)-1,0,1)'"
        f":g='255*clip(2-abs({hue}-2),0,1)'"
        f":b='255*clip(2-abs({hue}-4),0,1)',"
        f"loop=loop=-1:size=1"
    )
    if period:
        graph += f",hue=H=2*PI*t/{period}"
    return graph + ",format=rgba"


def write_circle_maps(directory: str | Path) -> tuple[Path, Path]:
    """``circle`` の ``remap`` 用座標マップ (16bit PGM) を書いて (xmap, ymap) を返す

    出力は 1 辺 ``2 * CIRCLE_RING[1]`` の正方形。輪の外は範囲外の座標で ``fill`` になる。
    横長の波形 (2 * CIRCLE_WAVE_SIZE[0] 幅) の x を角度、y を半径に割り当てる。

    >>> import tempfile
    >>> d = tempfile.mkdtemp()
    >>> xmap, ymap = write_circle_maps(d)
    >>> xmap.name
    'circle_xmap_176-262-1440x86.pgm'
    >>> xmap.read_bytes()[:16]
    b'P5\\n524 524\\n65535'
    >>> len(ymap.read_bytes()) == len(b"P5\\n524 524\\n65535\\n") + 524 * 524 * 2
    True

    同時に何本作っても失敗せず、同じ中身になり、一時ファイルも残らない

    >>> from concurrent.futures import ThreadPoolExecutor
    >>> d = tempfile.mkdtemp()
    >>> with ThreadPoolExecutor(8) as pool:
    ...     results = list(pool.map(lambda _: write_circle_maps(d), range(16)))
    >>> len(set(results)), sorted(p.name for p in Path(d).iterdir())
    (1, ['circle_xmap_176-262-1440x86.pgm', 'circle_ymap_176-262-1440x86.pgm'])
    >>> xmap.read_bytes() == results[0][0].read_bytes()
    True
    """
    directory = Path(directory)
    inner, outer = CIRCLE_RING
    strip_w = 2 * CIRCLE_WAVE_SIZE[0]
    strip_h = CIRCLE_WAVE_SIZE[1]
    # 寸法を変えたら別ファイルになるように名前に入れる
    tag = f"{inner}-{outer}-{strip_w}x{strip_h}"
    xmap_path = directory / f"circle_xmap_{tag}.pgm"
    ymap_path = directory / f"circle_ymap_{tag}.pgm"
    if xmap_path.exists() and ymap_path.exists():
        return xmap_path, ymap_path
    side = 2 * outer
    c = (side - 1) / 2
    out_of_range = 65535
    xs = array.array("H")
    ys = array.array("H")
    for y in range(side):
        dy = y - c
        for x in range(side):
            dx = x - c
            r = math.hypot(dx, dy)
            if inner <= r <= outer:
                # 真上から時計回り
                angle = (math.atan2(dx, -dy) / (2 * math.pi)) % 1.0
                xs.append(min(strip_w - 1, int(angle * strip_w)))
                ys.append(min(strip_h - 1, int((outer - r) / (outer - inner) * strip_h)))
            else:
                xs.append(out_of_range)
                ys.append(out_of_range)
    if sys.byteorder == "little":  # PGM は big endian
        xs.byteswap()
        ys.byteswap()
    header = f"P5\n{side} {side}\n65535\n".encode()
    directory.mkdir(parents=True, exist_ok=True)
    for path, data in ((xmap_path, xs), (ymap_path, ys)):
        # 同時に作っても壊れないよう、一時ファイルは呼び出しごとに別名にして置き換える
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=path.stem, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(header + data.tobytes())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return xmap_path, ymap_path
