"""Höjd och fart i videon (skiss D, vald av användaren 2026-11-28).

Under headcam-bilden, i samma svarta fält som pulsbandet, visas loppets höjdprofil. Ytan under
kurvan färgas efter farten, som på Strava: en jämn skala från blå (långsamt) via cyan, grön, gul
och orange till röd (snabbt). Skalan räknas på loppets egen fart, så färgerna sprids även för
den som springer långsamt. Det man har sprungit är färgat, det som är kvar är grått.

En vit löpare springer längs kurvan vid aktuell tid. Ju högre fart, desto snabbare går benen
(ett stegpar tar 1,25 s när det går långsamt och 0,45 s när det går fort). Till vänster står
farten nu i min/km i samma färg som kurvan, och under den en färgskala med en pil.

Allt ritas av ffmpeg, på samma sätt som pulsbandet (pulse_video.py):
- Två bilder av profilen (färgad och grå) ritas här i Python som PPM. Den grå bilden beskärs
  och läggs över den färgade från löparens position och åt höger.
- Löparen är en kort bildsekvens (PAM med genomskinlighet). Samma bilder läses i några olika
  takter, en ffmpeg-ingång per takt. Kommandon i en sendcmd-fil visar den takt som passar
  farten och flyttar löparen i höjdled, byter siffran och flyttar pilen på färgskalan.
"""

from __future__ import annotations

import colorsys
import math
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .fitfile import HeartRate, Terrain, pace_text
from .placing_video import BLACK, WHITE, Shape, _strokes, raster

BAND_H = 180          # samma fält som pulsbandet (Full HD-pixlar)
SS = 2                # profilen ritas i dubbel storlek och skalas ner (mjuka kanter)
GREY_FILL = (42, 42, 42)
GREY_LINE = (102, 102, 102)
STEP = 0.5            # sekunder mellan uppdateringar av siffran, pilen och löparens höjd

# Löparen: ett stegpar (vänster + höger steg) på RUN_FRAMES bilder. Takterna nedan är sekunder
# per stegpar, från långsammast till snabbast.
RUN_FRAMES = 12
PERIODS = (1.25, 1.0, 0.8, 0.62, 0.45)
RUN_SCALE = 1.9                       # figurenheter -> Full HD-pixlar
RUN_BOX = (-17.0, -54.0, 17.0, 3.0)   # löparens bild: x0, y0, x1, y1 i figurenheter (fötter vid 0,0)

LEGEND_X, LEGEND_W, LEGEND_H = 32, 150, 8
PTR_W, PTR_H = 13, 9

Color = Tuple[int, int, int]


@dataclass
class TerrainTrack:
    """Höjd och fart som ska visas i videon. Samma tider som PulseTrack:

    offset: klocktid (Unix-sekunder) = headcam-tid + offset.
    graph_start/graph_end: vilken del av headcam-filmen profilen visar (hela loppet), även när
    bara en provbit skapas. None = videons början/slut.
    """
    hr: HeartRate
    offset: float
    graph_start: Optional[float] = None
    graph_end: Optional[float] = None

    @property
    def terrain(self) -> Terrain:
        assert self.hr.terrain is not None
        return self.hr.terrain


def speed_color(speed: float, lo: float, hi: float) -> Color:
    """Blå (långsamt, nyans 230°) -> cyan -> grön -> gul -> orange -> röd (snabbt, 0°)."""
    f = 0.5 if hi <= lo else max(0.0, min(1.0, (speed - lo) / (hi - lo)))
    r, g, b = colorsys.hls_to_rgb(230 * (1 - f) / 360, 0.55, 0.95)
    return int(r * 255), int(g * 255), int(b * 255)


def speed_level(speed: float, lo: float, hi: float) -> int:
    """Vilken av PERIODS som passar farten (0 = långsammast)."""
    f = 0.5 if hi <= lo else max(0.0, min(1.0, (speed - lo) / (hi - lo)))
    return min(len(PERIODS) - 1, int(f * len(PERIODS)))


def runner_shapes(frame: int, lean: float = 10.0) -> List[Shape]:
    """Löparen i bildruta frame (0..RUN_FRAMES-1), fötterna kring 0,0, i figurenheter."""
    a = 2 * math.pi * frame / RUN_FRAMES
    bounce = 1.2 * abs(math.sin(a))
    hip = (0.0, -22.0 - bounce)
    leg_len = 11.5
    paths = []
    for off in (0.0, math.pi):
        th = math.radians(38) * math.sin(a + off)
        knee = (hip[0] + leg_len * math.sin(th), hip[1] + leg_len * math.cos(th))
        sh = th - math.radians(70) * max(0.0, -math.cos(a + off))
        foot = (knee[0] + leg_len * math.sin(sh), knee[1] + leg_len * math.cos(sh))
        paths.append([hip, knee, foot])
    sho = (hip[0] + 4 * math.sin(math.radians(lean)) + 2, hip[1] - 16)
    paths.append([hip, sho])
    for off in (0.0, math.pi):
        th = math.radians(45) * math.sin(a + off)
        el = (sho[0] - 8 * math.sin(th), sho[1] + 8 * math.cos(th))
        hand = (el[0] + 7 * math.cos(th) + 3, el[1] - 4)
        paths.append([sho, el, hand])
    head = (sho[0] + 2.5, sho[1] - 7)
    p_out, c_out = _strokes(paths, 6.5, 0, 0, 1)
    p_in, c_in = _strokes(paths, 3.4, 0, 0, 1)
    return [Shape(polys=p_out, circles=c_out + [(head[0], head[1], 5.6)], fill=BLACK),
            Shape(polys=p_in, circles=c_in + [(head[0], head[1], 4.2)], fill=WHITE)]


def _ppm(img: bytearray, w: int, h: int) -> bytes:
    return b"P6\n%d %d\n255\n" % (w, h) + bytes(img)


def _pam(img: bytes, w: int, h: int) -> bytes:
    return (b"P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
            % (w, h)) + img


def draw_profile(ys: Sequence[Optional[float]], cols: Sequence[Optional[Color]], w: int, h: int,
                 line: Color, r: float, fill: Optional[Color] = None) -> bytes:
    """PPM-bild: yta under kurvan (färg per kolumn, eller fill) och en linje överst."""
    img = bytearray(w * h * 3)
    line_b = bytes(line)
    for c in range(w):
        y = ys[c]
        if y is None:
            continue
        fc = fill if fill is not None else cols[c]
        if fc is not None:
            fb = bytes(fc)
            for row in range(max(0, int(y)), h):
                i = (row * w + c) * 3
                img[i:i + 3] = fb
        nb = [v for v in ys[max(0, c - 1):c + 2] if v is not None]
        for row in range(max(0, int(min(nb) - r)), min(h - 1, int(max(nb) + r)) + 1):
            i = (row * w + c) * 3
            img[i:i + 3] = line_b
    return _ppm(img, w, h)


def draw_legend(w: int, h: int, lo: float, hi: float) -> bytes:
    """PAM: färgskalan som en rundad stav."""
    out = bytearray(w * h * 4)
    rad = h / 2
    for x in range(w):
        c = speed_color(lo + (hi - lo) * x / max(1, w - 1), lo, hi)
        for y in range(h):
            # rundade ändar: avstånd till stavens mittlinje
            cx = min(max(x + 0.5, rad), w - rad)
            d = math.hypot(x + 0.5 - cx, y + 0.5 - rad)
            a = max(0.0, min(1.0, rad - d + 0.5))
            if a > 0:
                i = (y * w + x) * 4
                out[i:i + 4] = bytes((*c, int(a * 255)))
    return _pam(bytes(out), w, h)


def draw_pointer(w: int, h: int) -> bytes:
    """PAM: en vit triangel som pekar nedåt."""
    shape = Shape(polys=[[(0, 0), (w, 0), (w / 2, h)]], fill=WHITE)
    return raster([shape], w, h, 1.0, 0, 0)


def _write(path: str, data: bytes) -> str:
    with open(path, "wb") as f:
        f.write(data)
    return path


@dataclass
class Band:
    inputs: List[str]          # extra ffmpeg-argument
    chains: List[str]          # filterkedjor som skapar [tband], [tleg], [tptr], [trun0] …
    overlays: str              # filter som läggs på den färdiga bilden
    files: List[str]
    first_pace: str
    lo_hi_alt: Tuple[float, float]


def build_band(track: TerrainTrack, tmpdir: str, k: int, box_w: int, H: int, start: float,
               duration: float, first_input: int, fonts: Sequence[Optional[str]],
               escape, fps: str = "30") -> Band:
    """Rita bilderna och skriv kommandofilen. start/duration: videons del av headcam-filmen.
    box_w: headcam-rutans bredd i videons pixlar. escape skyddar sökvägar i filtergrafen."""
    ter = track.terrain
    g0 = start if track.graph_start is None else track.graph_start
    g1 = start + duration if track.graph_end is None else track.graph_end
    if g1 <= g0:
        g0, g1 = start, start + max(duration, 1.0)
    band_y = H - BAND_H * k
    gx = 200 * k
    gw = max(100 * k, box_w - gx - 70 * k)
    gw -= gw % 2
    gy = band_y + 40 * k
    gh = 124 * k
    pad = 10 * k

    win = ter.window(g0 + track.offset, g1 + track.offset)
    if win is None:
        win = (min(ter.alt), max(ter.alt), min(ter.speed), max(ter.speed))
    a_lo, a_hi, s_lo, s_hi = win
    if a_hi - a_lo < 20:   # en platt bana ska se platt ut
        mid = (a_hi + a_lo) / 2
        a_lo, a_hi = mid - 10, mid + 10
    if s_hi - s_lo < 0.3:
        s_lo, s_hi = s_lo - 0.15, s_hi + 0.15

    def y_of(alt: float, h: int, p: int) -> float:
        return p + (a_hi - alt) / (a_hi - a_lo) * (h - 2 * p)

    # Profilen, en y och en färg per kolumn (i dubbel storlek).
    W2, H2 = gw * SS, gh * SS
    ys: List[Optional[float]] = []
    cols: List[Optional[Color]] = []
    for c in range(W2):
        v = ter.at(g0 + (g1 - g0) * c / max(1, W2 - 1) + track.offset)
        ys.append(None if v is None else y_of(v[0], H2, pad * SS))
        cols.append(None if v is None else speed_color(v[1], s_lo, s_hi))
    r = 1.1 * k * SS
    color_img = _write(os.path.join(tmpdir, "hojd_farg.ppm"),
                       draw_profile(ys, cols, W2, H2, WHITE, r))
    grey_img = _write(os.path.join(tmpdir, "hojd_gra.ppm"),
                      draw_profile(ys, cols, W2, H2, GREY_LINE, r, fill=GREY_FILL))
    lw, lh = LEGEND_W * k, LEGEND_H * k
    legend = _write(os.path.join(tmpdir, "fargskala.pam"), draw_legend(lw, lh, s_lo, s_hi))
    pw, ph = PTR_W * k, PTR_H * k
    pointer = _write(os.path.join(tmpdir, "pil.pam"), draw_pointer(pw, ph))
    x0, y0, x1, y1 = RUN_BOX
    s = RUN_SCALE * k
    rw, rh = int(round((x1 - x0) * s)), int(round((y1 - y0) * s))
    folder = os.path.join(tmpdir, "lopare")
    os.makedirs(folder, exist_ok=True)
    for f in range(RUN_FRAMES):
        _write(os.path.join(folder, f"lopare_{f:02d}.pam"),
               raster(runner_shapes(f), rw, rh, s, x0, y0))
    run_pattern = os.path.join(folder, "lopare_%02d.pam")

    # Löparens fötter (x) som funktion av videons tid t. Samma uttryck flyttar gränsen färg/grå.
    span = g1 - g0
    rel = f"clip(({start - g0:.3f}+t)/{span:.3f},0,1)"
    cut = f"trunc({rel}*{gw})"
    foot_x = f"{gx}+{rel}*{gw}"
    run_x = f"{foot_x}-{int(round(-x0 * s))}"

    leg_x, leg_y = LEGEND_X * k, band_y + 136 * k
    lines: List[str] = []
    state: dict = {}
    first_pace = "–"
    n = int(duration / STEP) + 1
    for i in range(n):
        tau = i * STEP
        v = ter.at(start + tau + track.offset)
        cmds = []
        if v is None:
            txt, colr, level, ry, px = "–", WHITE, -1, -1000, -1000
        else:
            alt, sp = v
            txt = pace_text(sp)
            colr = speed_color(sp, s_lo, s_hi)
            level = speed_level(sp, s_lo, s_hi)
            ry = int(gy + y_of(alt, gh, pad) - (-y0) * s + 2 * k)
            f = max(0.0, min(1.0, (sp - s_lo) / (s_hi - s_lo)))
            px = int(leg_x + f * lw - pw / 2)
        if i == 0:
            first_pace = txt
        for lv in range(len(PERIODS)):
            y = ry if lv == level else -1000
            if state.get(f"run{lv}") != y:
                state[f"run{lv}"] = y
                cmds.append(f"overlay@trun{lv} y {y}")
        if state.get("ptr") != px:
            state["ptr"] = px
            cmds.append(f"overlay@tptr x {px}")
        key = (txt, colr)
        if state.get("txt") != key:
            state["txt"] = key
            esc_txt = txt.replace(":", "\\\\:")
            cmds.append(f"drawtext@tpace reinit text={esc_txt}:fontcolor=0x%02x%02x%02x" % colr)
        if cmds:
            lines.append(f"{tau:.2f} " + ", ".join(cmds) + ";")
    cmd_file = os.path.join(tmpdir, "hojd_kommandon.txt")
    with open(cmd_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    inputs: List[str] = []
    for p in (color_img, grey_img, legend, pointer):
        inputs += ["-i", p]
    for per in PERIODS:
        inputs += ["-framerate", f"{RUN_FRAMES / per:.4f}", "-i", run_pattern]
    i_c, i_g, i_l, i_p = range(first_input, first_input + 4)
    # Varje bild läses och skalas EN gång och upprepas sedan av loop-filtret (som pulsbandet).
    rep = f"loop=loop=-1:size=1,setpts=N/({fps})/TB"
    chains = [
        f"[{i_c}:v]scale={gw}:{gh}:flags=area,format=rgba,{rep}[tc]",
        f"[{i_g}:v]scale={gw}:{gh}:flags=area,format=rgba,"
        f"pad={2 * gw}:{gh}:0:0:color=black@0,{rep}[tg]",
        f"[tg]crop={gw}:{gh}:x='{cut}':y=0[tgc]",
        f"[tc][tgc]overlay=x='{cut}':y=0:format=rgb[tband]",
        f"[{i_l}:v]format=rgba,{rep}[tleg]",
        f"[{i_p}:v]format=rgba,{rep}[tptr]",
    ]
    for lv, per in enumerate(PERIODS):
        rate = RUN_FRAMES / per
        chains.append(f"[{first_input + 4 + lv}:v]format=rgba,"
                      f"loop=loop=-1:size={RUN_FRAMES}:start=0,setpts=N/({rate:.4f})/TB,"
                      f"fps={fps}[trun{lv}]")

    ov = (f"[tm];[tm]sendcmd=f='{escape(cmd_file)}'[tm0];"
          f"[tm0][tband]overlay=x={gx}:y={gy}[tm1];"
          f"[tm1][tleg]overlay=x={leg_x}:y={leg_y}[tm2];"
          f"[tm2][tptr]overlay@tptr=x=-1000:y={leg_y - ph - 1 * k}[tm3]")
    cur = "tm3"
    for lv in range(len(PERIODS)):
        nxt = f"tr{lv}"
        ov += f";[{cur}][trun{lv}]overlay@trun{lv}=x='{run_x}':y=-1000[{nxt}]"
        cur = nxt
    ov += f";[{cur}]null"
    font, bold = (list(fonts) + [None, None])[:2]
    tfont = bold or font
    if tfont:
        ff = escape(tfont)
        ov += (f",drawtext@tpace=fontfile='{ff}':text='–':fontsize={66 * k}"
               f":fontcolor=white:x={30 * k}:y={band_y + 34 * k}")
    if font:
        f2 = escape(font)
        ov += (f",drawtext=fontfile='{f2}':text='min/km':expansion=none:fontsize={21 * k}"
               f":fontcolor=0xdddddd:x={32 * k}:y={band_y + 104 * k}")
        ov += (f",drawtext=fontfile='{f2}':text='långsamt':expansion=none:fontsize={15 * k}"
               f":fontcolor=0x99aaaa:x={leg_x}:y={leg_y + lh + 6 * k}")
        ov += (f",drawtext=fontfile='{f2}':text='snabbt':expansion=none:fontsize={15 * k}"
               f":fontcolor=0x99aaaa:x={leg_x + lw}-text_w:y={leg_y + lh + 6 * k}")
        top_alt, bot_alt = a_hi - (a_hi - a_lo) * pad / gh, a_lo + (a_hi - a_lo) * pad / gh
        ov += (f",drawtext=fontfile='{f2}':text='{top_alt:.0f} m':expansion=none"
               f":fontsize={14 * k}:fontcolor=0x777777:x={gx + gw + 8 * k}:y={gy + pad - 7 * k}")
        ov += (f",drawtext=fontfile='{f2}':text='{bot_alt:.0f} m':expansion=none"
               f":fontsize={14 * k}:fontcolor=0x777777:x={gx + gw + 8 * k}"
               f":y={gy + gh - pad - 7 * k}")
    files = [color_img, grey_img, legend, pointer, cmd_file, folder]
    return Band(inputs=inputs, chains=chains, overlays=ov, files=files, first_pace=first_pace,
                lo_hi_alt=(a_lo, a_hi))
