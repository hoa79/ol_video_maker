"""Läs pulsen ur en .fit-fil från en Garmin-klocka (eller annan klocka som sparar FIT).

FIT är Garmins binärformat med en publicerad beskrivning. Vi läser bara det vi behöver: tid och puls i
"record"-meddelandena. Läsaren är skriven här med bara Pythons standardbibliotek,
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


def read_heart_rate(data: bytes, name: str = "filen") -> List[Tuple[int, int]]:
    """[(Unix-tid i sekunder, puls)] ur FIT-data, sorterat på tid."""
    out: List[Tuple[int, int]] = []
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
                    hr = None
                    q = p
                    for num, size, base in d.fields:
                        if d.global_num == MSG_RECORD and num == FIELD_HEART_RATE:
                            hr = _read_int(data, q, size, base, d.endian)
                        q += size
                    p += d.size
                    if d.global_num == MSG_RECORD and ts is not None and hr:
                        out.append((ts + FIT_EPOCH, hr))
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
                ts = hr = None
                q = p
                for num, size, base in d.fields:
                    if num == FIELD_TIMESTAMP:
                        ts = _read_int(data, q, size, base, d.endian)
                    elif d.global_num == MSG_RECORD and num == FIELD_HEART_RATE:
                        hr = _read_int(data, q, size, base, d.endian)
                    q += size
                p += d.size
                if ts is not None:
                    last_ts = ts
                if d.global_num == MSG_RECORD and ts is not None and hr:
                    out.append((ts + FIT_EPOCH, hr))
        except (IndexError, ValueError, struct.error):
            if not out:
                raise VideoMakerError(BAD_FILE.format(name=name))
        files += 1
        pos = pos + hsize + data_size + 2
    out.sort()
    # Samma sekund två gånger (t.ex. två ihopslagna filer): behåll den första.
    uniq: List[Tuple[int, int]] = []
    for t, v in out:
        if not uniq or t != uniq[-1][0]:
            uniq.append((t, v))
    return uniq


# ---------------------------------------------------------------------------
# Pulsdata
# ---------------------------------------------------------------------------


@dataclass
class HeartRate:
    """Pulsen från klockan: tider (Unix-sekunder) och slag per minut."""
    times: List[float]
    bpm: List[int]
    name: str = ""

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
    samples = read_heart_rate(data, name)
    if len(samples) < 10:
        raise VideoMakerError(
            f"Filen {name} innehåller ingen puls. Hade du pulsmätningen på under loppet?")
    return HeartRate([float(t) for t, _ in samples], [v for _, v in samples], name)


def write_fit(path: str, samples: Sequence[Tuple[float, int]]) -> None:
    """Skriv en enkel FIT-fil med puls (används i testerna)."""
    body = bytearray()
    # Definition: lokalt meddelande 0 = record med timestamp (uint32) och heart_rate (uint8).
    body += bytes([0x40, 0, 0]) + struct.pack("<H", MSG_RECORD) + bytes([2])
    body += bytes([FIELD_TIMESTAMP, 4, 0x86, FIELD_HEART_RATE, 1, 0x02])
    for t, hr in samples:
        body += bytes([0x00]) + struct.pack("<IB", int(t) - FIT_EPOCH, hr)
    header = struct.pack("<BBHI4s", 12, 0x10, 2132, len(body), b".FIT")
    with open(path, "wb") as f:
        f.write(header + bytes(body) + b"\x00\x00")
