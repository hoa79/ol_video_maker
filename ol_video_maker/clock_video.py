"""Tid sedan start i videon (design B "I fältet ovanför filmen").

Ett stoppur och en stor tid står uppe till vänster i det svarta fältet ovanför headcam-filmen,
med en grå etikett direkt efter siffrorna på samma baslinje ("sedan start" eller "i mål").
Etikettens plats räknas ur typsnittets teckenbredder (fontwidth.py). Klockan täcker varken filmen,
kartan eller fältet under videon, och den fungerar med alla val under videon.

- Före starten står klockan på grå 0:00.
- Från starten (synkögonblicket i steg 2) räknar den upp, vit.
- Vid målgången stannar den och blir grön.

Lopptiden räknas med race_time(): har du klistrat in sträcktider och valt dig själv blir tiden
i mål exakt din officiella tid från Livelox. Med målgång i steg 3 sträcks klockan jämnt mellan
starten och målgången så att den landar där (som placeringsbandet), annars stannar den när
den officiella tiden är nådd. Utan sträcktider är det filmens egen tid från starten till
målgången (eller till filmens slut).

Allt ritas med ffmpegs drawtext (tiden räknas fram i ffmpeg för varje bildruta) och en liten
bild av stoppuret, så inga extra program eller bibliotek behövs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .fontwidth import text_width

WHITE = "white"
GREY = "0x8a8a8a"
LABEL = "0xa8a8a8"
GREEN = "0x38d26b"

# Geometri i Full HD-pixlar (skalas med k i 4K). Fältet ovanför en 16:9-film är 180 px högt.
BASELINE = 132               # siffrornas och etikettens baslinje
TIME_SIZE = 96
TEXT_X = 128
ICON_X, ICON_SIZE = 40, 76
ICON_Y = BASELINE - 35 - ICON_SIZE // 2   # centrerat mot siffrornas höjd (ca 0,73 em)
LABEL_SIZE = 26
LABEL_GAP = 22               # mellanrum mellan siffrorna och etiketten

LABEL_RUNNING = "sedan start"
LABEL_FINISH = "i mål"
LABEL_OFFICIAL = "i mål · officiell tid från Livelox"


@dataclass
class ClockTrack:
    """Det klockan behöver veta, i headcam-filmens tid (sekunder).

    race_start: starten (synkögonblicket i steg 2).
    finish: målgången (steg 3) eller None om den inte är vald.
    official: din officiella sluttid från de inklistrade sträcktiderna, eller None.
    """
    race_start: float
    finish: Optional[float] = None
    official: Optional[float] = None

    def rate(self) -> float:
        """Lopptid per sekund film. 1 utan officiell tid eller målgång."""
        if self.official and self.finish is not None and self.finish - self.race_start > 1.0:
            return self.official / (self.finish - self.race_start)
        return 1.0

    def finish_at(self) -> Optional[float]:
        """Tidpunkt i headcam-filmen när klockan stannar, eller None (räknar till slutet)."""
        if self.finish is not None and self.finish > self.race_start:
            return self.finish
        if self.official:
            return self.race_start + self.official
        return None

    def race_time(self, t: float) -> float:
        """Lopptid (sekunder sedan start) vid tiden t i headcam-filmen."""
        e = max(0.0, (t - self.race_start) * self.rate())
        stop = self.finish_at()
        if stop is not None:
            e = min(e, self.final_time())
        return e

    def final_time(self) -> float:
        """Tiden som står i mål."""
        if self.official:
            return float(self.official)
        stop = self.finish_at()
        return max(0.0, (stop - self.race_start) * self.rate()) if stop is not None else 0.0


def fmt_clock(seconds: float) -> str:
    """41:27 eller 1:02:13 (som resultatlistor i orientering)."""
    s = int(max(0.0, seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _in_watch(u: float, v: float) -> bool:
    """Stoppuret i en ruta -1..1: ring, visare och knapp upptill. Linjebredd ca 0,13."""
    w = 0.065
    cy = 0.14
    r = (u * u + (v - cy) ** 2) ** 0.5
    if abs(r - 0.70) <= w:                       # ringen
        return True
    if abs(u) <= w and cy - 0.42 <= v <= cy:     # visaren (rakt upp från mitten)
        return True
    if abs(u) <= w and -0.80 <= v <= -0.56:      # skaftet
        return True
    if abs(v + 0.86) <= w and abs(u) <= 0.26:    # knappen
        return True
    return False


def draw_watch(size: int, rgb: Tuple[int, int, int] = (168, 168, 168)) -> bytes:
    """PAM-bild (RGBA) av stoppuret med mjuka kanter."""
    out = bytearray(size * size * 4)
    n = 4
    for py in range(size):
        for px in range(size):
            hit = 0
            for sy in range(n):
                for sx in range(n):
                    u = ((px + (sx + 0.5) / n) / size) * 2 - 1
                    v = ((py + (sy + 0.5) / n) / size) * 2 - 1
                    hit += _in_watch(u, v)
            if hit:
                i = (py * size + px) * 4
                out[i:i + 4] = bytes((*rgb, int(round(255 * hit / (n * n)))))
    head = (b"P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
            % (size, size))
    return head + bytes(out)


@dataclass
class Clock:
    inputs: List[str]      # extra ffmpeg-argument (stoppurets bild)
    chains: List[str]      # filterkedja som skapar [clk]
    overlays: str          # läggs till efter övriga overlay-filter, börjar med "[..];" eller ","
    files: List[str]


def _running_text(t0: float, rate: float, hours: bool) -> str:
    """drawtext-text som räknar upp från t0 (videons tid) med rate lopp-sekunder per sekund."""
    e = f"(t-{t0:.3f})*{rate:.6f}" if abs(rate - 1.0) > 1e-9 else f"(t-{t0:.3f})"
    if hours:
        return (f"%{{eif:floor({e}/3600):d}}:%{{eif:mod(floor({e}/60),60):d:2}}"
                f":%{{eif:mod(floor({e}),60):d:2}}")
    return f"%{{eif:floor({e}/60):d}}:%{{eif:mod(floor({e}),60):d:2}}"


def build_clock(track: ClockTrack, tmpdir: str, k: int, start: float, duration: float,
                first_input: int, fonts: Tuple[Optional[str], Optional[str]],
                escape: Callable[[str], str], fps: str = "30") -> Clock:
    """Bygg klockans filter. start/duration: videons del av headcam-filmen (videons t=0 är
    start). Klockan läggs på [in] med overlays, som börjar med ',' (fortsätter kedjan)."""
    font, bold = fonts
    bold = bold or font
    icon = os.path.join(tmpdir, "stoppur.pam")
    size = ICON_SIZE * k
    with open(icon, "wb") as f:
        f.write(draw_watch(size))
    rep = f"loop=loop=-1:size=1,setpts=N/({fps})/TB"
    chains = [f"[{first_input}:v]format=rgba,{rep}[clk]"]

    t_start = track.race_start - start
    stop = track.finish_at()
    t_stop = (stop - start) if stop is not None else None
    rate = track.rate()
    end = duration + 1.0

    # (från, till, text, färg, expansion, etikett, provtext för bredden)
    parts: List[Tuple[float, float, str, str, bool, str, str]] = []
    if t_start > 0:
        parts.append((0.0, t_start, "0:00", GREY, False, LABEL_RUNNING, "0:00"))
    run_end = min(end, t_stop) if t_stop is not None else end
    a = max(0.0, t_start)
    # Siffrorna blir bredare vid 10 minuter (m:ss -> mm:ss) och en timme (h:mm:ss), så
    # etiketten flyttas där.
    for e_to, hours, sample in ((600.0, False, "0:00"), (3600.0, False, "00:00"),
                                (None, True, "0:00:00")):
        b = run_end if e_to is None else min(run_end, t_start + e_to / rate)
        if b > a:
            parts.append((a, b, _running_text(t_start, rate, hours), WHITE, True,
                          LABEL_RUNNING, sample))
            a = b
    if t_stop is not None and t_stop < end:
        final = fmt_clock(track.final_time())
        parts.append((max(0.0, t_stop), end, final, GREEN, False,
                      LABEL_OFFICIAL if track.official else LABEL_FINISH, final))

    ov = f"[clkin];[clkin][clk]overlay=x={ICON_X * k}:y={ICON_Y * k}:shortest=0:eof_action=repeat"
    if not bold:
        return Clock(inputs=["-i", icon], chains=chains, overlays=ov, files=[icon])
    fb, fr = escape(bold), escape(font or bold)
    base = BASELINE * k
    for a, b, text, color, expand, label, sample in parts:
        if b <= a:
            continue
        en = f"between(t\\,{a:.3f}\\,{b - 0.0005:.3f})"
        lx = TEXT_X + text_width(bold, sample, TIME_SIZE) + LABEL_GAP
        ov += (f",drawtext=fontfile='{fb}':text='{escape(text)}'"
               f":expansion={'normal' if expand else 'none'}:fontsize={TIME_SIZE * k}"
               f":fontcolor={color}:x={TEXT_X * k}:y={base}-max_glyph_a:enable='{en}'")
        ov += (f",drawtext=fontfile='{fr}':text='{escape(label)}':expansion=none"
               f":fontsize={LABEL_SIZE * k}:fontcolor={LABEL}:x={int(round(lx * k))}"
               f":y={base}-max_glyph_a:enable='{en}'")
    return Clock(inputs=["-i", icon], chains=chains, overlays=ov, files=[icon])
