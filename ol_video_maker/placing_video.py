"""Placeringslinjen i videon (design A "Hela banan", godkänd 2026-11-28).

I det svarta fältet under headcam-bilden (där pulsbandet annars ligger) går en linje från
start till mål med kontrollerna utmärkta. Varje vald löpare är en prick på linjen, placerad
där löparen var vid samma lopptid som du (som om alla startade samtidigt). Pricken bär
placeringen vid senaste kontrollen och namnet (förnamn + initial). Den som vinner minst
3 placeringar på sträckan den springer nu blir en grön springande figur, den som tappar
minst 3 en orange trött figur. Du är gul, och till vänster står din placering i stort.

Väljer man ingen "du" försvinner den stora placeringen, linjen blir längre och ingen prick
är gul.

Allt ritas som vektorgrafik och text i en ASS-fil (undertextformatet som ffmpegs ass-filter
läser med libass). Samma figurer kan också skrivas som SVG, som fönstret visar i steg 4.

Juristens krav (jurist_placering.md):
- Utan ikryssat samtycke visas bara du med namn. Övriga visas med placering, utan namn.
- Klubb, födelseår och totaltid visas aldrig. Felstämplade skrivs aldrig ut som "mp":
  pricken tonas bara bort vid kontrollen som saknas.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .splits import (MAX_SHOWN, TREND_LIMIT, Moment, Splits, moment, ordinal, race_time,
                     short_names)

BAND_H = 180                 # samma fält som pulsbandet (Full HD-pixlar)
STEP = 0.5                   # sekunder mellan uppdateringar i videon
FONT_SCALE = 1.0             # ASS-storlek per px i skissen (libass ritar ungefär som CSS)
YELLOW = (255, 212, 0)
GREEN = (34, 197, 94)
ORANGE = (255, 106, 61)
MAGENTA = (214, 60, 160)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
LINE_Y = 1052
ROWS = (1024, 996, 968, 940)
PANEL_X0 = 215               # linjens början när din placering står till vänster
NO_PANEL_X0 = 60             # … och utan "du"
CREDIT = "Karta och sträcktider: Livelox"

Color = Tuple[int, int, int]


@dataclass
class PlacingTrack:
    """Placeringar som ska visas i videon.

    race_start: tid i headcam-filmen när loppet startar (synkögonblicket i steg 2).
    race_end: tid i headcam-filmen vid målgången (steg 3), eller None om den inte är markerad.
    shown: index (i splits.runners) för de löpare som syns. me: ditt index eller None.
    consent: kryssrutan "De jag visar med namn vet om det …".
    """
    splits: Splits
    shown: List[int]
    me: Optional[int]
    consent: bool
    race_start: float = 0.0
    race_end: Optional[float] = None

    def finish(self) -> Optional[int]:
        """Din sluttid enligt sträcktiderna (sekunder), eller None."""
        sp = self.splits
        if self.me is None or not (0 <= self.me < len(sp.runners)):
            return None
        r = sp.runners[self.me]
        return r.splits[-1] if r.splits else None

    def race_time(self, video_t: float) -> float:
        """Lopptiden enligt Livelox vid tiden video_t i headcam-filmen. Är målgången markerad
        i filmen står du i mål i linjen precis när du går i mål i filmen (se
        splits.race_time)."""
        return race_time(video_t, self.race_start, self.race_end, self.finish())


# ---------------------------------------------------------------------------
# Figurer
# ---------------------------------------------------------------------------


@dataclass
class Shape:
    """En fylld figur (polygoner) eller cirkel, med ev. kant. Koordinater i Full HD."""
    polys: List[List[Tuple[float, float]]] = field(default_factory=list)
    circles: List[Tuple[float, float, float]] = field(default_factory=list)
    fill: Optional[Color] = WHITE
    border: Optional[Color] = None
    bw: float = 0.0
    alpha: float = 1.0


@dataclass
class Text:
    x: float
    y: float                  # mitten av texten i höjd
    parts: List[Tuple[str, Color, bool]]
    size: float
    align: str = "l"          # l = vänsterkant vid x, r = högerkant vid x, c = mitten
    alpha: float = 1.0
    outline: float = 2.0


@dataclass
class Item:
    slot: str                 # samma slot i flera bilder = samma sak (ASS-händelser slås ihop)
    layer: int
    obj: object


def text_width(parts: Sequence[Tuple[str, Color, bool]], size: float) -> float:
    """Ungefärlig textbredd (Segoe UI/Arial/DejaVu Sans är ungefär 0,55 em per tecken)."""
    return sum(len(t) * size * (0.58 if b else 0.54) for t, _, b in parts)


def _seg(x1, y1, x2, y2, w) -> List[Tuple[float, float]]:
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / n * w / 2, dx / n * w / 2
    return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


def _strokes(paths: Sequence[Sequence[Tuple[float, float]]], w: float, ox: float, oy: float,
             s: float) -> Tuple[List, List]:
    """Streck (polylinjer) som polygoner med runda leder."""
    polys, circles = [], []
    for p in paths:
        pts = [(ox + x * s, oy + y * s) for x, y in p]
        for a, b in zip(pts, pts[1:]):
            polys.append(_seg(a[0], a[1], b[0], b[1], w * s))
        for x, y in pts:
            circles.append((x, y, w * s / 2))
    return polys, circles


# Figurerna (figurer_animerade.gif): radie 19, ritade kring 0,0. Animationen går runt på
# ANIM_FRAMES bilder i ANIM_FPS bilder/s (2 s): den snabba springer 4 steg, den trötta
# släpar sig fram 2 långsamma steg medan två svettdroppar faller.
ANIM_FRAMES = 24
ANIM_FPS = 12
FIG_R = 19.0
FIG_BOX = (-31.0, -21.0, 21.0, 21.0)     # figurens bild: x0, y0, x1, y1 i figurenheter


def _limb(o, a, b, l1, l2):
    k = (o[0] + l1 * math.sin(a), o[1] + l1 * math.cos(a))
    f = (k[0] + l2 * math.sin(a - b), k[1] + l2 * math.cos(a - b))
    return [o, k, f]


def figure_shapes(kind: str, frame: int = 0) -> List[Shape]:
    """Figuren i bildruta frame (0..ANIM_FRAMES-1), i figurenheter kring 0,0."""
    out: List[Shape] = []
    w = 2.8
    if kind == "fast":
        ph = frame / ANIM_FRAMES * 2 * math.pi * 4
        out.append(Shape(circles=[(0, 0, FIG_R)], fill=(11, 61, 28)))
        out.append(Shape(circles=[(0, 0, FIG_R - 1.5)], fill=GREEN))
        bob = -1.2 * abs(math.sin(ph))
        lines = []
        for i, y in enumerate((-6, 0, 6)):   # fartstrecken glider bakåt
            o = ((ph * 6 / (2 * math.pi)) + i * 2) % 6
            lines.append([(-20 - o, y), (-14 - o, y)])
        p, c = _strokes(lines, 2, 0, 0, 1)
        out.append(Shape(polys=p, circles=c, fill=WHITE, alpha=0.85))
        hip, sh = (-2, 4 + bob), (3, -5 + bob)
        a = 0.9 * math.sin(ph)
        paths = [[hip, sh]]
        for sgn in (1, -1):
            aa = sgn * a
            paths.append(_limb(hip, aa, 1.3 if aa < 0 else 0.4, 6, 6.5))
            paths.append(_limb(sh, -aa * 0.9, -1.4, 4.5, 4.5))
        p, c = _strokes(paths, w, 0, 0, 1)
        out.append(Shape(polys=p, circles=c + [(6, -10 + bob, 3.8)], fill=WHITE))
    else:
        t = frame / ANIM_FRAMES * 2
        out.append(Shape(circles=[(0, 0, FIG_R)], fill=(90, 26, 5)))
        out.append(Shape(circles=[(0, 0, FIG_R - 1.5)], fill=ORANGE))
        sway = math.sin(t * 2 * math.pi)       # ett långsamt steg per sekund
        bob = 0.8 * abs(sway)
        hip, sh = (-3, 6 + bob), (0, -2 + bob)
        paths = [_limb(hip, sgn * 0.25 * sway, 0.2, 5, 5.5) for sgn in (1, -1)]
        paths.append([hip, (-3, 1 + bob), sh])
        paths.append(_limb(sh, 0.15 + 0.1 * sway, -0.2, 4.5, 4))   # armen hänger
        p, c = _strokes(paths, w, 0, 0, 1)
        out.append(Shape(polys=p, circles=c + [(4, -5 + bob * 1.3, 3.8)], fill=WHITE))
        for off, x0 in ((0.0, 8.0), (0.5, 11.0)):   # svettdroppar som faller och tonas bort
            u = (t * 1.3 + off) % 1
            x, y = x0 + u * 2, -12 + u * 20
            out.append(Shape(polys=[[(x, y - 2.6), (x + 1.6, y), (x - 1.6, y)]],
                             circles=[(x, y, 1.6)], fill=(191, 233, 255), alpha=1 - u * u))
    return out


@dataclass
class Figure:
    """En animerad figur (ritas som bild i videon, som vektorer i förhandsbilden)."""
    kind: str
    x: float
    y: float
    alpha: float
    slot: int


def place_shapes(shapes: Sequence[Shape], x: float, y: float, s: float = 1.0,
                 alpha: float = 1.0) -> List[Shape]:
    out = []
    for sh in shapes:
        out.append(Shape(polys=[[(x + px * s, y + py * s) for px, py in p] for p in sh.polys],
                         circles=[(x + cx * s, y + cy * s, r * s) for cx, cy, r in sh.circles],
                         fill=sh.fill, border=sh.border, bw=sh.bw * s, alpha=sh.alpha * alpha))
    return out


def runner_marker(kind: Optional[str], me: bool, x: float, y: float, alpha: float,
                  slot: int) -> List[object]:
    if kind is None:
        if me:
            return [Shape(circles=[(x, y, 12)], fill=YELLOW, border=BLACK, bw=2, alpha=alpha)]
        return [Shape(circles=[(x, y, 9.25)], fill=WHITE, border=BLACK, bw=1.5, alpha=alpha)]
    out: List[object] = []
    if me:
        out.append(Shape(circles=[(x, y, 23)], fill=None, border=YELLOW, bw=3, alpha=alpha))
    out.append(Figure(kind, x, y, alpha, slot))
    return out


def _in_poly(px: float, py: float, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def raster(shapes: Sequence[Shape], w: int, h: int, scale: float, ox: float, oy: float) -> bytes:
    """Rita figurer till en PAM-bild (RGBA) med mjuka kanter. Pixel (px, py) motsvarar
    figurpunkten ((px + 0.5) / scale + ox, (py + 0.5) / scale + oy)."""
    img = [[0.0, 0.0, 0.0, 0.0] for _ in range(w * h)]   # förmultiplicerad RGBA
    n = 3
    offs = [((sx + 0.5) / n, (sy + 0.5) / n) for sy in range(n) for sx in range(n)]
    for sh in shapes:
        if sh.fill is None:
            continue
        parts = [("c", c) for c in sh.circles] + [("p", p) for p in sh.polys]
        for kind, g in parts:
            if kind == "c":
                cx, cy, r = g
                x0, x1, y0, y1 = cx - r, cx + r, cy - r, cy + r
            else:
                xs, ys = [q[0] for q in g], [q[1] for q in g]
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            pa = max(0, int((x0 - ox) * scale)), min(w - 1, int((x1 - ox) * scale) + 1)
            pb = max(0, int((y0 - oy) * scale)), min(h - 1, int((y1 - oy) * scale) + 1)
            for py in range(pb[0], pb[1] + 1):
                for px in range(pa[0], pa[1] + 1):
                    hit = 0
                    for dx, dy in offs:
                        fx, fy = (px + dx) / scale + ox, (py + dy) / scale + oy
                        if kind == "c":
                            if (fx - g[0]) ** 2 + (fy - g[1]) ** 2 <= g[2] * g[2]:
                                hit += 1
                        elif _in_poly(fx, fy, g):
                            hit += 1
                    if not hit:
                        continue
                    a = sh.alpha * hit / len(offs)
                    p = img[py * w + px]
                    inv = 1 - a
                    p[0] = sh.fill[0] * a + p[0] * inv
                    p[1] = sh.fill[1] * a + p[1] * inv
                    p[2] = sh.fill[2] * a + p[2] * inv
                    p[3] = a + p[3] * inv
    out = bytearray(w * h * 4)
    for i, (r, g, b, a) in enumerate(img):
        if a > 0:
            out[i * 4:i * 4 + 4] = bytes((min(255, int(r / a + 0.5)), min(255, int(g / a + 0.5)),
                                          min(255, int(b / a + 0.5)), min(255, int(a * 255 + 0.5))))
    head = (b"P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
            % (w, h))
    return head + bytes(out)


def figure_frames(kind: str, k: int, folder: str) -> Tuple[str, int, int]:
    """Skriv figurens animering som PAM-bilder. Returnerar (mönster för ffmpeg, bredd, höjd)."""
    x0, y0, x1, y1 = FIG_BOX
    w, h = int(round((x1 - x0) * k)), int(round((y1 - y0) * k))
    for f in range(ANIM_FRAMES):
        data = raster(figure_shapes(kind, f), w, h, k, x0, y0)
        with open(os.path.join(folder, f"{kind}_{f:02d}.pam"), "wb") as fh:
            fh.write(data)
    return os.path.join(folder, f"{kind}_%02d.pam"), w, h


# ---------------------------------------------------------------------------
# En bild av fältet vid en viss lopptid
# ---------------------------------------------------------------------------


class Scene:
    """Räknar ut vad som ska ritas. Kommer ihåg var etiketterna stod förra gången, så att de
    inte hoppar mellan raderna i onödan."""

    def __init__(self, track: PlacingTrack, box_w: int) -> None:
        self.track = track
        sp = track.splits
        self.me = track.me if track.me is not None and track.me < len(sp.runners) else None
        self.shown = [i for i in dict.fromkeys(track.shown + ([self.me] if self.me is not None
                                                                else []))
                      if 0 <= i < len(sp.runners) and sp.runners[i].selectable]
        if self.me in self.shown:   # du först, högst MAX_SHOWN löpare
            self.shown.remove(self.me)
            self.shown.insert(0, self.me)
        self.shown = self.shown[:MAX_SHOWN]
        names = short_names([r.name for r in sp.runners])
        self.names = {i: names[i] for i in self.shown}
        self.x0 = PANEL_X0 if self.me is not None else NO_PANEL_X0
        self.x1 = max(self.x0 + 200, box_w - 35)
        self.box_w = box_w
        self.prev: Dict[int, Tuple[int, int]] = {}
        acc, self.ticks = 0.0, []
        for k, ln in enumerate(sp.lengths[:-1]):
            acc += ln
            self.ticks.append((self.x0 + acc / sp.total * (self.x1 - self.x0), sp.controls[k]))

    def xpos(self, pos: float) -> float:
        return self.x0 + pos * (self.x1 - self.x0)

    def static(self) -> List[Item]:
        x0, x1, y = self.x0, self.x1, LINE_Y
        items = [Item("linje", 0, Shape(polys=[_seg(x0, y, x1, y, 6)],
                                        circles=[(x0, y, 3), (x1, y, 3)], alpha=0.35))]
        tick_polys = [_seg(x, y - 7, x, y + 7, 3) for x, _ in self.ticks]
        items.append(Item("kontroller", 1, Shape(polys=tick_polys, fill=MAGENTA)))
        for j, (x, name) in enumerate(self.ticks):
            items.append(Item(f"knr{j}", 1, Text(x, y + 19, [(name, MAGENTA, False)], 13,
                                                 align="c", outline=0)))
        tri = [(x0 - 12, y), (x0, y - 10), (x0 + 12, y)]
        p, c = _strokes([tri + [tri[0]]], 3, 0, 0, 1)
        items.append(Item("start", 1, Shape(polys=p, circles=c, fill=MAGENTA)))
        items.append(Item("mal", 1, Shape(circles=[(x1, y, 7.5)], fill=None, border=MAGENTA,
                                          bw=3)))
        items.append(Item("mal2", 1, Shape(circles=[(x1, y, 3.75)], fill=None, border=MAGENTA,
                                           bw=2.5)))
        return items

    def at(self, t: float) -> List[Item]:
        tr, sp = self.track, self.track.splits
        items = self.static()
        ms: Dict[int, Moment] = {i: moment(sp, sp.runners[i], max(0.0, t)) for i in self.shown}
        # Din placering till vänster och den gula delen av linjen.
        if self.me is not None:
            m = ms[self.me]
            if m.visible > 0:
                xm = self.xpos(m.pos)
                items.append(Item("gul", 0, Shape(polys=[_seg(self.x0, LINE_Y, xm, LINE_Y, 6)],
                                                  circles=[(self.x0, LINE_Y, 3), (xm, LINE_Y, 3)],
                                                  fill=YELLOW, alpha=0.8 * m.visible)))
                if m.finished:
                    big, small = ordinal(m.place) if m.place else "Mål", "i mål"
                elif m.passed >= 0 and m.place:
                    big, small = ordinal(m.place), f"vid kontroll {sp.controls[m.passed]}"
                elif m.passed < 0:
                    big, small = "Start", f"mot kontroll {sp.controls[0]}"
                else:
                    big, small = "", ""
                if big:
                    items.append(Item("stor", 3, Text(28, 963, [(big, YELLOW, True)], 62,
                                                      outline=0, alpha=m.visible)))
                if small:
                    items.append(Item("vid", 3, Text(30, 1011, [(small, WHITE, False)], 20,
                                                     outline=0, alpha=0.8 * m.visible)))
                if abs(m.trend) >= TREND_LIMIT:
                    up = m.trend > 0
                    items.append(Item("trend", 3, Text(
                        30, 1041, [(f"{'▲' if up else '▼'} {abs(m.trend)} på sträckan",
                                    GREEN if up else ORANGE, True)], 20, outline=0)))
            items += self.legend_items(30, 1065)
        else:
            items += self.legend_items(30, 923)
        # Löparna: prickar i rader ovanför linjen, etiketter bredvid.
        boxes = []
        for i in self.shown:
            m = ms[i]
            if m.visible <= 0:
                continue
            is_me = i == self.me
            kind = "fast" if m.trend >= TREND_LIMIT else ("tired" if m.trend <= -TREND_LIMIT
                                                          else None)
            col = YELLOW if is_me else WHITE
            parts = []
            if m.place:
                parts.append((f"{m.place}" + (" " if is_me or tr.consent else ""), col, True))
            if is_me or tr.consent:
                parts.append((self.names[i], col, is_me))
            w = text_width(parts, 21) if parts else 0
            boxes.append((self.xpos(m.pos), w, i, m, kind, parts))
        placed = self._rows(boxes)
        for x, w, i, m, kind, parts in boxes:
            row, side = placed[i]
            y = ROWS[row]
            a = m.visible
            items.append(Item(f"streck{i}", 2, Shape(polys=[_seg(x, y, x, LINE_Y, 1.5)],
                                                     alpha=0.25 * a)))
            layer = 5 if i == self.me else 4
            slot = self.shown.index(i)
            for j, sh in enumerate(runner_marker(kind, i == self.me, x, y, a, slot)):
                items.append(Item(f"prick{i}_{j}", layer, sh))
            if parts and side:
                off = (38 if side < 0 else 28) if kind else 22
                items.append(Item(f"namn{i}", layer, Text(x + side * off, y, parts, 21,
                                                          align="l" if side > 0 else "r",
                                                          alpha=a)))
        return items

    def legend_items(self, x: float, y: float) -> List[Item]:
        """Teckenförklaringen för figurerna (där lopptiden stod förut). Figurerna står still
        här och är mindre än på linjen."""
        s = 0.5
        out: List[Item] = []
        for j, (kind, label) in enumerate((("fast", "snabb"), ("tired", "trött"))):
            cx = x + 88 * j + FIG_R * s
            for k_, sh in enumerate(place_shapes(figure_shapes(kind, 3), cx, y, s)):
                out.append(Item(f"forkl_{kind}{k_}", 3, sh))
            out.append(Item(f"forkl_{kind}_text", 3,
                            Text(cx + FIG_R * s + 6, y, [(label, WHITE, False)], 15,
                                 outline=0, alpha=0.75)))
        return out

    def _rows(self, boxes) -> Dict[int, Tuple[int, int]]:
        """Lägg varje löpare i en rad och på en sida (1 höger, -1 vänster, 0 = ingen etikett)
        utan att etiketterna krockar. Försöker först med förra platsen."""
        occ: Dict[int, List[Tuple[float, float]]] = {r: [] for r in range(len(ROWS))}
        soft: Dict[int, List[Tuple[float, float]]] = {r: [] for r in range(len(ROWS))}
        left_limit = 188 if self.me is not None else 8
        right_limit = self.box_w - 8
        out: Dict[int, Tuple[int, int]] = {}

        def span(x, w, kind, side):
            off = ((38 if side < 0 else 28) if kind else 22) if w else 0
            r = 23 if kind else 14
            if side > 0:
                return x - r, x + off + w
            return x - off - w, x + r

        def free(row, a, b):
            return all(b < c or a > d for c, d in occ[row])

        for x, w, i, m, kind, parts in sorted(boxes, key=lambda b: (b[2] != self.me, -b[0])):
            sides = (1, -1) if w else (1,)
            tries = []
            if i in self.prev:
                tries.append(self.prev[i])
            tries += [(r, s) for r in range(len(ROWS)) for s in sides]
            done = False
            # Först en plats där en figur inte överlappar grannraderna, annars vilken som helst
            # (hellre lite överlapp än att namnet försvinner).
            for strict, (row, side) in [(st, t) for st in (True, False) for t in tries]:
                if done:
                    break
                if side == 0 or side not in sides:
                    continue
                a, b = span(x, w, kind, side)
                if a < left_limit or b > right_limit:
                    continue
                near_ok = not strict or (
                    all(free(rr, x - 23, x + 23) for rr in (row - 1, row + 1) if rr in occ)
                    and all(b < c or a > d for c, d in soft[row]))
                if free(row, a, b) and near_ok:
                    occ[row].append((a, b))
                    if kind:   # en figur är högre än en rad: håll grannraderna fria vid den
                        for rr in (row - 1, row + 1):
                            if rr in soft:
                                soft[rr].append((x - 23, x + 23))
                    out[i] = (row, side)
                    done = True
            if not done:
                # Ingen plats för etiketten: bara pricken, i den rad där den krockar minst.
                r = 23 if kind else 14
                row = min(range(len(ROWS)),
                          key=lambda rr: sum(1 for c, d in occ[rr] if not (x + r < c or x - r > d)))
                occ[row].append((x - r, x + r))
                out[i] = (row, 0)
        self.prev = {i: v for i, v in out.items() if v[1]}
        return out


# ---------------------------------------------------------------------------
# ASS (videon)
# ---------------------------------------------------------------------------


def _ass_color(c: Color) -> str:
    return "&H%02X%02X%02X&" % (c[2], c[1], c[0])


def _ass_alpha(a: float) -> str:
    return "&H%02X&" % int(round(255 * (1 - max(0.0, min(1.0, a)))))


def _circle_path(cx: float, cy: float, r: float, k: float) -> str:
    c = 0.5523 * r
    f = lambda v: f"{v * k:.0f}" if k * r > 6 else f"{v * k:.1f}"
    return (f"m {f(cx + r)} {f(cy)} b {f(cx + r)} {f(cy + c)} {f(cx + c)} {f(cy + r)} {f(cx)} {f(cy + r)} "
            f"b {f(cx - c)} {f(cy + r)} {f(cx - r)} {f(cy + c)} {f(cx - r)} {f(cy)} "
            f"b {f(cx - r)} {f(cy - c)} {f(cx - c)} {f(cy - r)} {f(cx)} {f(cy - r)} "
            f"b {f(cx + c)} {f(cy - r)} {f(cx + r)} {f(cy - c)} {f(cx + r)} {f(cy)}")


def _poly_path(pts, k: float) -> str:
    # Samma rotationsriktning för alla delar, så att överlapp fylls (inga hål).
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    if area < 0:
        pts = list(reversed(pts))
    return "m " + " l ".join(f"{x * k:.1f} {y * k:.1f}" for x, y in pts)


def ass_event(obj, k: float) -> str:
    """Texten i en Dialogue-rad (utan tider)."""
    if isinstance(obj, Shape):
        tags = "\\an7\\pos(0,0)\\shad0"
        if obj.fill is None:
            tags += "\\1a&HFF&"
        else:
            tags += f"\\1c{_ass_color(obj.fill)}\\1a{_ass_alpha(obj.alpha)}"
        if obj.border is not None and obj.bw > 0:
            tags += (f"\\bord{obj.bw * k / 2:.2f}\\3c{_ass_color(obj.border)}"
                     f"\\3a{_ass_alpha(obj.alpha)}")
        else:
            tags += "\\bord0"
        paths = [_poly_path(p, k) for p in obj.polys]
        # En cirkel med kant: kanten ligger utanför, så cirkeln görs lika mycket mindre.
        inset = obj.bw / 4 if obj.border is not None else 0.0
        paths += [_circle_path(x, y, max(0.5, r - inset), k) for x, y, r in obj.circles]
        return "{" + tags + "\\p1}" + " ".join(paths) + "{\\p0}"
    t = obj
    an = {"l": 4, "r": 6, "c": 5}[t.align]
    tags = (f"\\an{an}\\pos({t.x * k:.0f},{t.y * k:.0f})\\fs{t.size * k * FONT_SCALE:.0f}"
            f"\\bord{t.outline * k:.1f}\\shad0\\3c&H000000&\\alpha{_ass_alpha(t.alpha)}")
    if t.outline <= 0:
        tags += "\\bord0"
    out = "{" + tags + "}"
    for s, col, bold in t.parts:
        s = s.replace("\\", "/").replace("{", "(").replace("}", ")")
        out += "{\\c" + _ass_color(col) + ("\\b1" if bold else "\\b0") + "}" + s
    return out


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


@dataclass
class Band:
    inputs: List[str]          # extra ffmpeg-argument (figurernas bildsekvenser)
    chains: List[str]          # filterkedjor som skapar [fig0] … (en per löpare)
    overlays: str              # filter som läggs på den färdiga bilden: figurer + ASS
    files: List[str]
    events: int


def build_band(track: PlacingTrack, tmpdir: str, k: int, box_w: int, start: float,
               duration: float, first_input: int, fonts: Sequence[Optional[str]],
               escape) -> Band:
    """Skriv ASS-filen, figurernas bilder och kommandofilen för videons del
    [start, start+duration] av headcam-filmen. escape skyddar sökvägar i filtergrafen."""
    scene = Scene(track, box_w)
    W, H = 1920 * k, 1080 * k
    open_ev: Dict[str, Tuple[float, int, str]] = {}
    lines: List[str] = []
    cmds: List[str] = []
    fig_state: Dict[str, Tuple[int, int]] = {}    # overlay-namn -> (x, y)
    fx0, fy0 = FIG_BOX[0], FIG_BOX[1]
    slots = len(scene.shown)
    used = {"fast": False, "tired": False}

    def close(slot: str, end: float) -> None:
        t0, layer, txt = open_ev.pop(slot)
        if end > t0:
            lines.append(f"Dialogue: {layer},{_ass_time(t0)},{_ass_time(end)},D,,0,0,0,,{txt}")

    n = int(math.ceil(duration / STEP)) + 1
    for j in range(n):
        tau = min(duration, j * STEP)
        race_t = track.race_time(start + tau)
        seen = set()
        figs: Dict[str, Tuple[int, int]] = {}
        for it in scene.at(race_t):
            if isinstance(it.obj, Figure):
                f = it.obj
                if f.alpha >= 0.5:
                    used[f.kind] = True
                    figs[f"{f.kind}{f.slot}"] = (int(round((f.x + fx0) * k)),
                                                 int(round((f.y + fy0) * k)))
                continue
            txt = ass_event(it.obj, k)
            seen.add(it.slot)
            cur = open_ev.get(it.slot)
            if cur is not None and cur[2] == txt and cur[1] == it.layer:
                continue
            if cur is not None:
                close(it.slot, tau)
            open_ev[it.slot] = (tau, it.layer, txt)
        for slot in [s for s in open_ev if s not in seen]:
            close(slot, tau)
        # Figurerna flyttas med kommandon (utanför bild = gömd).
        for kind in ("fast", "tired"):
            for i in range(slots):
                name = f"{kind}{i}"
                pos = figs.get(name, (-1000, -1000))
                if fig_state.get(name) != pos:
                    fig_state[name] = pos
                    cmds.append(f"{tau:.2f} overlay@{name} x {pos[0]}, overlay@{name} y {pos[1]};")
    for slot in list(open_ev):
        close(slot, duration + 1.0)
    fontdir, family = prepare_fonts(fonts, tmpdir)
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: D,{family},{21 * k},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,"
        "100,100,0,0,1,0,0,7,0,0,0,1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines.sort(key=lambda s: s.split(",", 2)[1])
    ass = os.path.join(tmpdir, "placering.ass")
    with open(ass, "w", encoding="utf-8") as f:
        f.write("\n".join(head + lines) + "\n")
    files = [ass, fontdir]

    inputs: List[str] = []
    chains: List[str] = []
    overlays = ""
    idx = first_input
    kinds = [kd for kd in ("fast", "tired") if used[kd]]
    for kind in kinds:
        folder = os.path.join(tmpdir, "figurer")
        os.makedirs(folder, exist_ok=True)
        pattern, fw, fh = figure_frames(kind, k, folder)
        inputs += ["-framerate", str(ANIM_FPS), "-i", pattern]
        rep = f"loop=loop=-1:size={ANIM_FRAMES}:start=0,setpts=N/{ANIM_FPS}/TB"
        outs = "".join(f"[{kind}{i}]" for i in range(slots))
        chains.append(f"[{idx}:v]format=rgba,{rep},split={slots}{outs}" if slots > 1
                      else f"[{idx}:v]format=rgba,{rep}[{kind}0]")
        idx += 1
    if kinds:
        cmd_file = os.path.join(tmpdir, "placering_kommandon.txt")
        with open(cmd_file, "w", encoding="utf-8") as f:
            f.write("\n".join(c for c in cmds if any(c.split(" ", 2)[1].startswith(f"overlay@{kd}")
                                                     for kd in kinds)) + "\n")
        files.append(cmd_file)
        overlays += f"[plc];[plc]sendcmd=f='{escape(cmd_file)}'[plc0]"
        cur = "plc0"
        for kind in kinds:
            for i in range(slots):
                nxt = f"plc_{kind}{i}"
                overlays += (f";[{cur}][{kind}{i}]overlay@{kind}{i}=x=-1000:y=-1000"
                             f":eof_action=repeat[{nxt}]")
                cur = nxt
        overlays += f";[{cur}]"
    else:
        overlays += ","
    overlays += f"ass=filename='{escape(ass)}':fontsdir='{escape(fontdir)}'"
    return Band(inputs=inputs, chains=chains, overlays=overlays, files=files, events=len(lines))


# ---------------------------------------------------------------------------
# SVG (förhandsbilden i steg 4)
# ---------------------------------------------------------------------------


def _hex(c: Color) -> str:
    return "#%02x%02x%02x" % c


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_items(items: Sequence[Item]) -> str:
    out = []
    flat = []
    for it in sorted(items, key=lambda i: i.layer):
        if isinstance(it.obj, Figure):
            f = it.obj
            flat += [Item(it.slot, it.layer, sh) for sh in place_shapes(figure_shapes(f.kind, 3),
                                                                         f.x, f.y, 1.0, f.alpha)]
        else:
            flat.append(it)
    for it in flat:
        o = it.obj
        if isinstance(o, Shape):
            fill = "none" if o.fill is None else _hex(o.fill)
            stroke = (f' stroke="{_hex(o.border)}" stroke-width="{o.bw:.1f}"'
                      if o.border is not None and o.bw > 0 else "")
            g = f'<g opacity="{o.alpha:.2f}" fill="{fill}"{stroke}>'
            for p in o.polys:
                g += '<path d="M' + " L".join(f"{x:.1f} {y:.1f}" for x, y in p) + 'Z"/>'
            for x, y, r in o.circles:
                g += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}"/>'
            out.append(g + "</g>")
        else:
            anchor = {"l": "start", "r": "end", "c": "middle"}[o.align]
            stroke = (f' paint-order="stroke" stroke="#000" stroke-width="{o.outline * 2:.1f}"'
                      if o.outline > 0 else "")
            spans = "".join(f'<tspan fill="{_hex(c)}" font-weight="{700 if b else 500}">'
                            f'{_esc(s)}</tspan>' for s, c, b in o.parts)
            out.append(f'<text x="{o.x:.1f}" y="{o.y + o.size * 0.36:.1f}" font-size="{o.size}" '
                       f'text-anchor="{anchor}" opacity="{o.alpha:.2f}" style="white-space:pre"{stroke}>'
                       f'{spans}</text>')
    return "".join(out)


def svg_at(track: PlacingTrack, box_w: int, race_t: float) -> str:
    """Fältet vid lopptiden race_t som SVG-innehåll (koordinater i Full HD, 1920 × 1080)."""
    return svg_items(Scene(track, box_w).at(race_t))


# ---------------------------------------------------------------------------
# Typsnitt till libass
# ---------------------------------------------------------------------------


def font_family(path: str) -> Optional[str]:
    """Typsnittsfamiljens namn ur en TrueType/OpenType-fil (namntabellen, id 1)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        num = struct.unpack(">H", data[4:6])[0]
        for i in range(num):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            if tag != b"name":
                continue
            _, count, strings = struct.unpack(">HHH", data[off:off + 6])
            best = None
            for j in range(count):
                pid, eid, lid, nid, ln, so = struct.unpack(
                    ">HHHHHH", data[off + 6 + 12 * j:off + 18 + 12 * j])
                if nid != 1:
                    continue
                raw = data[off + strings + so:off + strings + so + ln]
                if pid in (0, 3):
                    name = raw.decode("utf-16-be", "ignore")
                    if pid == 3 and lid == 0x409:
                        return name
                    best = best or name
                elif best is None:
                    best = raw.decode("latin-1", "ignore")
            return best
    except (OSError, struct.error, IndexError):
        return None
    return None


def prepare_fonts(fonts: Sequence[Optional[str]], tmpdir: str) -> Tuple[str, str]:
    """Kopiera typsnitten till en egen mapp som libass läser (fontsdir). Då behöver ffmpeg inte
    leta bland datorns alla typsnitt. Returnerar (mapp, familjenamn)."""
    folder = os.path.join(tmpdir, "typsnitt")
    os.makedirs(folder, exist_ok=True)
    family = None
    for f in fonts:
        if f and os.path.isfile(f):
            shutil.copy(f, os.path.join(folder, os.path.basename(f)))
            family = family or font_family(f)
    return folder, family or "Arial"
