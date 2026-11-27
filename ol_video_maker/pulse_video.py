"""Pulsbandet i videon (design A, godkänd 2026-11-27).

Under headcam-bilden, i det svarta fältet, visas hela loppets pulskurva. Till vänster står
pulsen nu i stora siffror. Ett rött hjärta åker längs kurvan vid aktuell tid och slår i samma
takt som pulsen (159 slag/min = 159 slag per minut). Kurvan till vänster om hjärtat är röd
(det man har sprungit), till höger grå (det som är kvar).

Allt ritas av ffmpeg:
- Två bilder av kurvan (röd och grå) ritas här i Python som PPM, utan extra bibliotek.
  Den grå bilden beskärs och läggs över den röda från hjärtats position och åt höger.
- Hjärtat är en PAM-bild med genomskinlighet. Det flyttas och slår med kommandon från
  en sendcmd-fil, som också byter siffran.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .fitfile import HeartRate

BAND_H = 180          # pulsbandets höjd i Full HD-pixlar (svarta fältet under en 16:9-headcam)
SS = 2                # kurvan ritas i dubbel storlek och skalas ner (mjuka kanter)
RED = (255, 90, 106)
RED_FILL = (52, 12, 18)
GREY_LINE = (110, 110, 110)
BEAT_SECONDS = 0.13   # hur länge hjärtat är stort vid varje slag


@dataclass
class PulseTrack:
    """Puls som ska visas i videon.

    offset: klocktid (Unix-sekunder) = headcam-tid + offset.
    graph_start/graph_end: vilken del av headcam-filmen kurvan visar (hela loppet), även när
    bara en provbit skapas. None = videons början/slut.
    """
    hr: HeartRate
    offset: float
    graph_start: Optional[float] = None
    graph_end: Optional[float] = None


@dataclass
class Band:
    inputs: List[str]      # extra ffmpeg-argument (-loop 1 -i bild …)
    chains: List[str]      # filterkedjor som skapar [pband], [phs] och [phb]
    files: List[str]       # temporära filer att ta bort efteråt
    cmd_file: str          # sendcmd-filen
    gx: int                # kurvans vänsterkant i videon
    gy: int                # kurvans överkant i videon
    gw: int
    gh: int
    heart: int             # hjärtats storlek (litet)
    heart_big: int
    x_expr: str            # hjärtats mittpunkt i x som funktion av t (videons tid)
    max_bpm: Optional[int]
    first_bpm: str


def _y_of(bpm: float, lo: float, hi: float, h: int, pad: int) -> float:
    return pad + (hi - bpm) / (hi - lo) * (h - 2 * pad)


def _draw_curve(ys: List[Optional[float]], w: int, h: int, line, fill, r: float) -> bytes:
    """PPM-bild med en kurva (y per kolumn, None = inget värde) och ev. fyllning under."""
    img = bytearray(w * h * 3)
    line_b, fill_b = bytes(line), bytes(fill) if fill else None
    for c in range(w):
        y = ys[c]
        if y is None:
            continue
        nb = [v for v in ys[max(0, c - 1):c + 2] if v is not None]
        top = max(0, int(min(nb) - r))
        bot = min(h - 1, int(max(nb) + r))
        if fill_b is not None:
            for row in range(bot + 1, h):
                i = (row * w + c) * 3
                img[i:i + 3] = fill_b
        for row in range(top, bot + 1):
            i = (row * w + c) * 3
            img[i:i + 3] = line_b
    return b"P6\n%d %d\n255\n" % (w, h) + bytes(img)


def _in_heart(x: float, y: float) -> bool:
    """Klassisk hjärtkurva, (x² + y² - 1)³ - x²y³ <= 0, med y uppåt."""
    a = x * x + y * y - 1
    return a * a * a - x * x * y * y * y <= 0


def _draw_heart(size: int) -> bytes:
    """PAM-bild (RGBA) av ett rött hjärta med vit kant i en svagt röd ring."""
    out = bytearray(size * size * 4)
    n = 3  # 3x3 delpunkter per pixel för mjuka kanter
    for py in range(size):
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(n):
                for sx in range(n):
                    # -1..1 över bilden
                    u = ((px + (sx + 0.5) / n) / size) * 2 - 1
                    v = ((py + (sy + 0.5) / n) / size) * 2 - 1
                    hx, hy = u * 1.75, -(v * 1.75) + 0.15
                    if _in_heart(hx / 0.92 * 1.0, hy / 0.92) and not _in_heart(hx / 0.78, hy / 0.78):
                        c = (255, 255, 255, 1.0)       # vit kant
                    elif _in_heart(hx / 0.78, hy / 0.78):
                        c = (255, 60, 80, 1.0)         # hjärtat
                    elif u * u + v * v <= 1.0:
                        c = (255, 60, 80, 0.30)        # ring
                    else:
                        continue
                    acc[0] += c[0] * c[3]
                    acc[1] += c[1] * c[3]
                    acc[2] += c[2] * c[3]
                    acc[3] += c[3]
            a = acc[3] / (n * n)
            i = (py * size + px) * 4
            if acc[3] > 0:
                out[i:i + 4] = bytes((int(acc[0] / acc[3]), int(acc[1] / acc[3]),
                                      int(acc[2] / acc[3]), int(round(a * 255))))
    head = (b"P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
            % (size, size))
    return head + bytes(out)


def _write(path: str, data: bytes) -> str:
    with open(path, "wb") as f:
        f.write(data)
    return path


def build_band(track: PulseTrack, tmpdir: str, k: int, box_w: int, H: int, start: float,
               duration: float, first_input: int, fps: str = "30") -> Band:
    """Rita bilderna och skriv kommandofilen. start/duration: videons del av headcam-filmen."""
    hr = track.hr
    g0 = start if track.graph_start is None else track.graph_start
    g1 = start + duration if track.graph_end is None else track.graph_end
    if g1 <= g0:
        g0, g1 = start, start + max(duration, 1.0)
    band_y = H - BAND_H * k
    gx = 180 * k
    gw = max(100 * k, box_w - gx - 34 * k)
    gw -= gw % 2
    gy = band_y + 30 * k
    gh = BAND_H * k - 44 * k
    pad = 8 * k

    lo_hi = hr.window(g0 + track.offset, g1 + track.offset)
    lo, hi = (lo_hi if lo_hi[0] is not None else (min(hr.bpm), max(hr.bpm)))
    max_bpm = hi
    lo, hi = float(lo) - 4, float(hi) + 4
    if hi - lo < 40:
        mid = (hi + lo) / 2
        lo, hi = mid - 20, mid + 20

    # Kurvan, en y per kolumn (i dubbel storlek).
    W2, H2 = gw * SS, gh * SS
    ys: List[Optional[float]] = []
    for c in range(W2):
        t = g0 + (g1 - g0) * c / max(1, W2 - 1)
        v = hr.at(t + track.offset)
        ys.append(None if v is None else _y_of(v, lo, hi, H2, pad * SS))
    r = 1.6 * k * SS
    red = _write(os.path.join(tmpdir, "puls_rod.ppm"), _draw_curve(ys, W2, H2, RED, RED_FILL, r))
    grey = _write(os.path.join(tmpdir, "puls_gra.ppm"),
                  _draw_curve(ys, W2, H2, GREY_LINE, None, r * 0.8))
    hs, hb = 44 * k, 56 * k
    h_small = _write(os.path.join(tmpdir, "hjarta.pam"), _draw_heart(hs))
    h_big = _write(os.path.join(tmpdir, "hjarta_stort.pam"), _draw_heart(hb))

    # Hjärtats x (mitt) som funktion av videons tid t. Samma uttryck flyttar gränsen röd/grå.
    span = g1 - g0
    rel = f"clip(({start - g0:.3f}+t)/{span:.3f},0,1)"
    cut = f"trunc({rel}*{gw})"
    x_expr = f"{gx}+{rel}*{gw}"

    # Kommandon: hjärtats höjd och siffran varje halv sekund, stora hjärtat vid varje slag.
    lines: List[str] = []
    last_txt = None
    step = 0.5
    n = int(duration / step) + 1
    first_bpm = "–"
    for i in range(n):
        tau = i * step
        v = hr.at(start + tau + track.offset)
        if v is None:
            y = -1000
            txt = "–"
        else:
            y = int(gy + _y_of(v, lo, hi, gh, pad) - hs / 2)
            txt = str(int(round(v)))
        if i == 0:
            first_bpm = txt
        cmd = f"overlay@phs y {y}"
        if txt != last_txt:
            cmd += f", drawtext@pbpm reinit text={txt}"
            last_txt = txt
        lines.append(f"{tau:.2f} {cmd};")
    # Slagen: räkna upp fasen (slag) med pulsen och lägg ett slag när den passerar ett heltal.
    phase, dt, tau = 0.0, 0.02, 0.0
    while tau < duration:
        v = hr.at(start + tau + track.offset)
        if v:
            phase += v / 60.0 * dt
            if phase >= 1.0:
                phase -= 1.0
                y = int(gy + _y_of(v, lo, hi, gh, pad) - hb / 2)
                lines.append(f"{tau:.3f} overlay@phb y {y};")
                lines.append(f"{tau + BEAT_SECONDS:.3f} overlay@phb y -1000;")
        tau += dt
    lines.sort(key=lambda s: float(s.split(" ", 1)[0]))
    cmd_file = os.path.join(tmpdir, "puls_kommandon.txt")
    with open(cmd_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    i_r, i_g, i_hs, i_hb = range(first_input, first_input + 4)
    # Varje bild läses och skalas EN gång och upprepas sedan av loop-filtret (billigt, samma
    # bildruta återanvänds). Med "-loop 1" lästes, avkodades och skalades bilderna om för
    # varje bildruta i videon, vilket kostade runt 10 % av renderingstiden.
    inputs = []
    for p in (red, grey, h_small, h_big):
        inputs += ["-i", p]
    rep = f"loop=loop=-1:size=1,setpts=N/({fps})/TB"
    chains = [
        f"[{i_r}:v]scale={gw}:{gh}:flags=area,format=rgba,{rep}[pr]",
        f"[{i_g}:v]scale={gw}:{gh}:flags=area,format=rgba,"
        f"pad={2 * gw}:{gh}:0:0:color=black@0,{rep}[pg]",
        f"[pg]crop={gw}:{gh}:x='{cut}':y=0[pgc]",
        f"[pr][pgc]overlay=x='{cut}':y=0:format=rgb[pband]",
        f"[{i_hs}:v]format=rgba,{rep}[phs]",
        f"[{i_hb}:v]format=rgba,{rep}[phb]",
    ]
    return Band(inputs=inputs, chains=chains, files=[red, grey, h_small, h_big, cmd_file],
                cmd_file=cmd_file, gx=gx, gy=gy, gw=gw, gh=gh, heart=hs, heart_big=hb,
                x_expr=x_expr, max_bpm=max_bpm, first_bpm=first_bpm)
