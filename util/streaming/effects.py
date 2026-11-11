"""Audio-reactive overlays for SoundCloud video (``&effect=...``).

Everything is drawn by ffmpeg filters available in ffmpeg 5.1 (production):

- ``wave``: ``showwaves`` line along the bottom of the frame
- ``spectrum``: ``showcqt`` bars (low to high, colored) along the bottom of the frame
- ``circle``: ``showwaves`` bent into a ring with ``remap`` around the artwork,
  which is drawn smaller and round for this effect
- ``volume``: ``showvolume`` meters (L/R) at the right edge

The still frame is input 0 (looped), the audio is input 1. ``circle`` also
reads the two ``remap`` coordinate maps as inputs 2 and 3.
"""

import array
import math
import sys
from pathlib import Path

from util.streaming.artwork import FRAME_SIZE

EFFECTS = ("wave", "spectrum", "circle", "volume")
EFFECT_FPS = 15

# circle: 絵は半径 CIRCLE_ART_RADIUS の円、波形の輪は内側 CIRCLE_RING[0] から外側 CIRCLE_RING[1]
CIRCLE_ART_RADIUS = 170
CIRCLE_RING = (176, 262)
# 輪の元になる横長の波形 (半周分。左右反転して 1 周にするので継ぎ目が出ない)
CIRCLE_WAVE_SIZE = (720, 86)


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
    >>> "[1:a]showwaves=s=960x120" in g and g.endswith("format=yuv420p[v]")
    True
    >>> "showcqt" in effect_filtergraph("spectrum")
    True
    >>> g = effect_filtergraph("circle")
    >>> "[2:v]" in g and "[3:v]" in g and "remap" in g and "hflip" in g
    True
    >>> "showvolume" in effect_filtergraph("volume")
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
        h = 120
        return (
            f"[1:a]showwaves=s={width}x{h}:mode=cline:r={fps}:scale=sqrt:draw=full"
            f":colors=white,format=rgba,colorchannelmixer=aa=0.75[fx];"
            f"[0:v][fx]overlay=0:{height - h - 12}:format=auto,format=yuv420p[v]"
        )
    if effect == "spectrum":
        h = 150
        # showcqt の背景は黒なので colorkey で抜く
        return (
            f"[1:a]showcqt=s={width}x{h}:r={fps}:bar_h={h}:sono_h=0:axis=0"
            f":bar_g=2:bar_v=8:count=2:basefreq=40:endfreq=12000,format=rgba,"
            f"colorkey=black:0.05:0.05,colorchannelmixer=aa=0.75[fx];"
            f"[0:v][fx]overlay=0:{height - h}:format=auto,format=yuv420p[v]"
        )
    if effect == "circle":
        w, h = CIRCLE_WAVE_SIZE
        side = 2 * CIRCLE_RING[1]
        return (
            f"[1:a]showwaves=s={w}x{h}:mode=cline:r={fps}:scale=sqrt:draw=full"
            f":colors=white,format=rgba,split[w1][w2];[w2]hflip[w3];"
            f"[w1][w3]hstack,format=rgba[strip];"
            f"[strip][2:v][3:v]remap=fill=black@0,"
            f"colorchannelmixer=aa=0.85[fx];"
            f"[0:v][fx]overlay={(width - side) // 2}:{(height - side) // 2}"
            f":format=auto,format=yuv420p[v]"
        )
    # volume
    meter = height - 80
    return (
        f"[1:a]showvolume=r={fps}:o=v:w={meter}:h=14:b=2:t=0:v=0:f=0.9:p=0,"
        f"format=rgba,colorchannelmixer=aa=0.85[fx];"
        f"[0:v][fx]overlay=W-w-24:(H-h)/2:format=auto,format=yuv420p[v]"
    )


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
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(header + data.tobytes())
        tmp.replace(path)
    return xmap_path, ymap_path
