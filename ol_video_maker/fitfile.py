"""Läs pulsen, höjden och farten ur en .fit-fil från en Garmin-klocka (eller annan klocka som
sparar FIT).

FIT är Garmins binärformat med en publicerad beskrivning. Vi läser bara det vi behöver: tid, puls,
höjd och fart i "record"-meddelandena. Läsaren är skriven här med bara Pythons standardbibliotek,
så inget extra bibliotek (och ingen extra licens) behövs.

Formatet i korthet:
- Rubrik (12 eller 14 byte) med längden på datadelen och texten ".FIT".
- Poster. Varje post börjar med en byte:
    * bit 7 = 0, bit 6 = 1: definition av ett "lokalt meddelande" (vilka fält, storlek, typ)
    * bit 7 = 0, bit 6 = 0: data för ett tidigare definierat lokalt meddelande
    * bit 7 = 1: data med komprimerad tidsstämpel (5 bitar tidsförskjutning)
- 2 byte kontrollsumma. Flera FIT-filer kan ligga efter varandra i samma fil.
"""

from __future__ import annotations

import bisect
import os
import struct
import time
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .core import VideoMakerError

FIT_EPOCH = 631065600     # 1989-12-31 00:00:00 UTC i Unix-tid
MSG_RECORD = 20           # "record": ett mätvärde per sekund (puls, fart, position …)
FIELD_TIMESTAMP = 253
FIELD_HEART_RATE = 3
FIELD_ALTITUDE = 2            # uint16, (värde / 5) - 500 meter
FIELD_SPEED = 6               # uint16, värde / 1000 m/s
FIELD_ENH_ALTITUDE = 78       # uint32, samma skala som altitude men större omfång
FIELD_ENH_SPEED = 73          # uint32, värde / 1000 m/s
_WANTED = (FIELD_HEART_RATE, FIELD_ALTITUDE, FIELD_SPEED, FIELD_ENH_ALTITUDE, FIELD_ENH_SPEED)

# Bastyp (de 5 låga bitarna) -> (struct-tecken, storlek, ogiltigt värde)
_BASE_TYPES = {
    0x00: ("B", 1, 0xFF),        # enum
    0x01: ("b", 1, 0x7F),        # sint8
    0x02: ("B", 1, 0xFF),        # uint8
    0x03: ("h", 2, 0x7FFF),      # sint16
    0x04: ("H", 2, 0xFFFF),      # uint16
    0x05: ("i", 4, 0x7FFFFFFF),  # sint32
    0x06: ("I", 4, 0xFFFFFFFF),  # uint32
    0x0A: ("B", 1, 0x00),        # uint8z
    0x0B: ("H", 2, 0x0000),      # uint16z
    0x0C: ("I", 4, 0x00000000),  # uint32z
    0x0E: ("q", 8, 0x7FFFFFFFFFFFFFFF),  # sint64
    0x0F: ("Q", 8, 0xFFFFFFFFFFFFFFFF),  # uint64
    0x10: ("Q", 8, 0),           # uint64z
}

BAD_FILE = "Filen {name} går inte att läsa som en .fit-fil från klockan."


@dataclass
class _Def:
    global_num: int
    endian: str                              # "<" eller ">"
    fields: List[Tuple[int, int, int]]       # (fältnummer, storlek, bastyp)
    size: int                                # total storlek på datadelen


def _read_int(data: bytes, pos: int, size: int, base: int, endian: str) -> Optional[int]:
    """Första värdet i ett heltalsfält, eller None om det är ogiltigt/okänt."""
    t = _BASE_TYPES.get(base & 0x1F)
    if t is None or size < t[1]:
        return None
    fmt, n, invalid = t
    value = struct.unpack_from(endian + fmt, data, pos)[0]
    return None if value == invalid else value


@dataclass
class Sample:
    """Ett mätvärde från klockan. None = klockan mätte inte det värdet."""
    t: int                       # Unix-tid i sekunder
    hr: Optional[int] = None     # slag/min
    alt: Optional[float] = None  # meter över havet
    speed: Optional[float] = None  # m/s


def _sample(d: "_Def", data: bytes, p: int, ts: int) -> Sample:
    vals: Dict[int, int] = {}
    q = p
    for num, size, base in d.fields:
        if num in _WANTED:
            v = _read_int(data, q, size, base, d.endian)
            if v is not None:
                vals[num] = v
        q += size
    alt = vals.get(FIELD_ENH_ALTITUDE, vals.get(FIELD_ALTITUDE))
    speed = vals.get(FIELD_ENH_SPEED, vals.get(FIELD_SPEED))
    return Sample(ts + FIT_EPOCH, vals.get(FIELD_HEART_RATE) or None,
                  None if alt is None else alt / 5.0 - 500.0,
                  None if speed is None else speed / 1000.0)


def read_samples(data: bytes, name: str = "filen") -> List[Sample]:
    """Alla mätvärden (record-meddelanden) ur FIT-data, sorterade på tid."""
    out: List[Sample] = []
    pos = 0
    files = 0
    while pos + 12 <= len(data):
        hsize = data[pos]
        if hsize < 12 or pos + hsize > len(data) or data[pos + 8:pos + 12] != b".FIT":
            if files:
                break  # skräp efter sista filen: strunta i det
            raise VideoMakerError(BAD_FILE.format(name=name))
        data_size = struct.unpack_from("<I", data, pos + 4)[0]
        p = pos + hsize
        end = min(len(data), p + data_size)
        defs: Dict[int, _Def] = {}
        last_ts: Optional[int] = None
        try:
            while p < end:
                h = data[p]
                p += 1
                if h & 0x80:  # komprimerad tidsstämpel
                    local = (h >> 5) & 0x03
                    offset = h & 0x1F
                    d = defs.get(local)
                    if d is None:
                        raise ValueError("odefinierat meddelande")
                    if last_ts is not None:
                        last_ts += (offset - (last_ts & 0x1F)) & 0x1F
                    ts = last_ts
                    if p + d.size > end:
                        break
                    if d.global_num == MSG_RECORD and ts is not None:
                        out.append(_sample(d, data, p, ts))
                    p += d.size
                    continue
                local = h & 0x0F
                if h & 0x40:  # definition
                    if p + 5 > end:
                        raise ValueError("kort definition")
                    endian = ">" if data[p + 1] == 1 else "<"
                    global_num = struct.unpack_from(endian + "H", data, p + 2)[0]
                    n = data[p + 4]
                    p += 5
                    fields = []
                    for _ in range(n):
                        fields.append((data[p], data[p + 1], data[p + 2]))
                        p += 3
                    size = sum(f[1] for f in fields)
                    if h & 0x20:  # utvecklarfält: vi hoppar bara över dem
                        nd = data[p]
                        p += 1
                        for _ in range(nd):
                            size += data[p + 1]
                            p += 3
                    defs[local] = _Def(global_num, endian, fields, size)
                    continue
                d = defs.get(local)
                if d is None:
                    raise ValueError("odefinierat meddelande")
                if p + d.size > end:
                    break  # avklippt fil: använd det vi har
                ts = None
                q = p
                for num, size, base in d.fields:
                    if num == FIELD_TIMESTAMP:
                        ts = _read_int(data, q, size, base, d.endian)
                    q += size
                if ts is not None:
                    last_ts = ts
                if d.global_num == MSG_RECORD and ts is not None:
                    out.append(_sample(d, data, p, ts))
                p += d.size
        except (IndexError, ValueError, struct.error):
            if not out:
                raise VideoMakerError(BAD_FILE.format(name=name))
        files += 1
        pos = pos + hsize + data_size + 2
    out.sort(key=lambda smp: smp.t)   # stabil: vid samma sekund står den första först
    # Samma sekund två gånger (t.ex. två ihopslagna filer): behåll den första.
    uniq: List[Sample] = []
    for smp in out:
        if not uniq or smp.t != uniq[-1].t:
            uniq.append(smp)
    return uniq


def read_heart_rate(data: bytes, name: str = "filen") -> List[Tuple[int, int]]:
    """[(Unix-tid i sekunder, puls)] ur FIT-data, sorterat på tid."""
    return [(smp.t, smp.hr) for smp in read_samples(data, name) if smp.hr]


# ---------------------------------------------------------------------------
# Pulsdata
# ---------------------------------------------------------------------------


def _smooth(vals: List[float], times: List[float], half: float) -> List[float]:
    """Glidande medelvärde över ±half sekunder (GPS-farten hoppar mycket från sekund till
    sekund)."""
    out: List[float] = []
    pre = [0.0]
    for v in vals:
        pre.append(pre[-1] + v)
    a = b = 0
    for i, t in enumerate(times):
        while times[a] < t - half:
            a += 1
        while b < len(times) and times[b] <= t + half:
            b += 1
        out.append((pre[b] - pre[a]) / max(1, b - a))
    return out


@dataclass
class Terrain:
    """Höjd (meter över havet) och fart (m/s) från klockan, utjämnade."""
    times: List[float]
    alt: List[float]
    speed: List[float]

    @classmethod
    def from_samples(cls, samples: Sequence[Sample]) -> Optional["Terrain"]:
        rows = [(float(smp.t), smp.alt, smp.speed) for smp in samples
                if smp.alt is not None and smp.speed is not None]
        if len(rows) < 10:
            return None
        times = [r[0] for r in rows]
        return cls(times, _smooth([r[1] for r in rows], times, 5.0),
                   _smooth([max(0.0, r[2]) for r in rows], times, 15.0))

    def at(self, t: float) -> Optional[Tuple[float, float]]:
        """(höjd, fart) vid tiden t, linjärt mellan mätvärdena. None utanför mätningen eller i
        långa glapp (klockan pausad)."""
        ts = self.times
        if not ts or t < ts[0] or t > ts[-1]:
            return None
        i = bisect.bisect_right(ts, t)
        if i >= len(ts):
            return self.alt[-1], self.speed[-1]
        t0, t1 = ts[i - 1], ts[i]
        if t1 - t0 > 60:
            return None
        f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return (self.alt[i - 1] + f * (self.alt[i] - self.alt[i - 1]),
                self.speed[i - 1] + f * (self.speed[i] - self.speed[i - 1]))

    def window(self, t0: float, t1: float) -> Optional[Tuple[float, float, float, float]]:
        """(lägsta höjd, högsta höjd, långsam fart, snabb fart) mellan t0 och t1. Farten tas
        som 5:e och 95:e percentilen, så att stopp vid kontrollerna och enstaka GPS-hopp inte
        tar hela färgskalan."""
        i, j = bisect.bisect_left(self.times, t0), bisect.bisect_right(self.times, t1)
        if j - i < 2:
            return None
        alts = self.alt[i:j]
        sp = sorted(self.speed[i:j])
        lo, hi = sp[int(0.05 * (len(sp) - 1))], sp[int(0.95 * (len(sp) - 1))]
        return min(alts), max(alts), lo, hi


def pace_text(speed: Optional[float]) -> str:
    """Fart som minuter per kilometer, t.ex. 5:18. Står man nästan still: –."""
    if speed is None or speed < 1000 / (60 * 30):   # långsammare än 30 min/km
        return "–"
    sec = int(round(1000 / speed))
    return f"{sec // 60}:{sec % 60:02d}"


@dataclass
class HeartRate:
    """Pulsen från klockan: tider (Unix-sekunder) och slag per minut. terrain: höjd och fart
    från samma fil, om klockan mätte dem."""
    times: List[float]
    bpm: List[int]
    name: str = ""
    terrain: Optional[Terrain] = None

    @property
    def start(self) -> float:
        return self.times[0]

    @property
    def end(self) -> float:
        return self.times[-1]

    @property
    def duration(self) -> float:
        return self.end - self.start

    def at(self, t: float) -> Optional[float]:
        """Pulsen vid tiden t (linjärt mellan mätvärdena). None utanför mätningen eller i
        långa glapp (klockan pausad)."""
        ts = self.times
        if not ts or t < ts[0] or t > ts[-1]:
            return None
        i = bisect.bisect_right(ts, t)
        if i >= len(ts):
            return float(self.bpm[-1])
        t0, t1 = ts[i - 1], ts[i]
        if t1 - t0 > 60:
            return None
        f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return self.bpm[i - 1] + f * (self.bpm[i] - self.bpm[i - 1])

    def window(self, t0: float, t1: float) -> Tuple[Optional[int], Optional[int]]:
        """Lägsta och högsta puls mellan t0 och t1."""
        i, j = bisect.bisect_left(self.times, t0), bisect.bisect_right(self.times, t1)
        vals = self.bpm[i:j]
        return (min(vals), max(vals)) if vals else (None, None)

    def resample(self, t0: float, t1: float, step: float = 1.0) -> List[Optional[float]]:
        n = max(1, int((t1 - t0) / step) + 1)
        return [self.at(t0 + k * step) for k in range(n)]

    def suggest_start(self) -> float:
        """Gissa när loppet startade: där pulsen stiger brantast och håller sig hög.

        Vi jämför medelpulsen de närmaste två minuterna efter varje tidpunkt med de två
        minuterna före, tar den tidpunkt där skillnaden är störst och går sedan bakåt till
        där stigningen började."""
        step = 5.0
        series = self.resample(self.start, self.end, step)
        vals = [v for v in series if v is not None]
        if len(vals) < 4:
            return self.start
        filled, last = [], vals[0]
        for v in series:
            last = v if v is not None else last
            filled.append(last)
        w = max(2, int(120 / step))
        n = len(filled)
        pre = [0.0]
        for v in filled:
            pre.append(pre[-1] + v)

        def mean(a: int, b: int) -> float:
            a, b = max(0, a), min(n, b)
            return (pre[b] - pre[a]) / (b - a) if b > a else filled[min(a, n - 1)]

        best_i, best = 0, float("-inf")
        for i in range(1, n - 1):
            score = mean(i, i + w) - mean(i - w, i)
            if score > best:
                best_i, best = i, score
        # Gå bakåt så länge pulsen fortfarande sjunker (högst två minuter): där började ökningen.
        i = best_i
        smooth = [mean(k - 1, k + 2) for k in range(n)]
        lim = max(0, best_i - w)
        while i > lim and smooth[i - 1] <= smooth[i] + 0.5:
            i -= 1
        return self.start + i * step

    def clock(self, t: float, seconds: bool = True) -> str:
        """Klocktid (datorns tidszon), t.ex. 18:02:13."""
        return time.strftime("%H:%M:%S" if seconds else "%H:%M", time.localtime(t))

    def day(self, t: float) -> str:
        months = ("jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov",
                  "dec")
        lt = time.localtime(t)
        return f"{lt.tm_mday} {months[lt.tm_mon - 1]}"


def load_fit(path: str) -> HeartRate:
    name = os.path.basename(path)
    try:
        if zipfile.is_zipfile(path):
            # Garmin Connect ("Export File") ger en zip-fil med .fit-filen i.
            with zipfile.ZipFile(path) as z:
                fits = [n for n in z.namelist() if n.lower().endswith(".fit")]
                if not fits:
                    raise VideoMakerError(f"Zip-filen {name} innehåller ingen .fit-fil.")
                name = os.path.basename(fits[0])
                data = z.read(fits[0])
        else:
            with open(path, "rb") as f:
                data = f.read()
    except (OSError, zipfile.BadZipFile):
        raise VideoMakerError(f"Kunde inte öppna filen {name}.")
    all_samples = read_samples(data, name)
    samples = [(smp.t, smp.hr) for smp in all_samples if smp.hr]
    if len(samples) < 10:
        raise VideoMakerError(
            f"Filen {name} innehåller ingen puls. Hade du pulsmätningen på under loppet?")
    return HeartRate([float(t) for t, _ in samples], [v for _, v in samples], name,
                     Terrain.from_samples(all_samples))


def write_fit(path: str, samples: Sequence[Tuple[float, int]],
              terrain: Optional[Sequence[Tuple[float, float]]] = None) -> None:
    """Skriv en enkel FIT-fil med puls (används i testerna). terrain: (höjd i m, fart i m/s)
    för varje mätvärde, skrivs som enhanced_altitude och enhanced_speed."""
    body = bytearray()
    # Definition: lokalt meddelande 0 = record med timestamp (uint32) och heart_rate (uint8).
    body += bytes([0x40, 0, 0]) + struct.pack("<H", MSG_RECORD) + bytes([4 if terrain else 2])
    body += bytes([FIELD_TIMESTAMP, 4, 0x86, FIELD_HEART_RATE, 1, 0x02])
    if terrain:
        body += bytes([FIELD_ENH_ALTITUDE, 4, 0x86, FIELD_ENH_SPEED, 4, 0x86])
    for n, (t, hr) in enumerate(samples):
        body += bytes([0x00]) + struct.pack("<IB", int(t) - FIT_EPOCH, hr)
        if terrain:
            alt, speed = terrain[n]
            body += struct.pack("<II", int(round((alt + 500) * 5)), int(round(speed * 1000)))
    header = struct.pack("<BBHI4s", 12, 0x10, 2132, len(body), b".FIT")
    with open(path, "wb") as f:
        f.write(header + bytes(body) + b"\x00\x00")
