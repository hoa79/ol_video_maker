"""Textbredd ur en TrueType-fil (bara standardbiblioteket).

Läser tabellerna head, hhea, hmtx och cmap (format 4) så att programmet vet hur bred en text
blir, till exempel för att ställa en etikett direkt efter klockans siffror. Kerning räknas inte.
Går filen inte att läsa används en uppskattning (0,6 em per tecken).
"""

from __future__ import annotations

import struct
from functools import lru_cache
from typing import Dict, Optional, Tuple

FALLBACK_EM = 0.6


@lru_cache(maxsize=8)
def _metrics(path: str) -> Optional[Tuple[int, Dict[int, int]]]:
    """(unitsPerEm, {tecken: bredd i fontenheter}) eller None."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] == b"ttcf":                       # samling: ta första fonten
            off = struct.unpack(">I", data[12:16])[0]
        else:
            off = 0
        num = struct.unpack(">H", data[off + 4:off + 6])[0]
        tables = {}
        for i in range(num):
            rec = off + 12 + 16 * i
            tag = data[rec:rec + 4].decode("latin-1")
            t_off, t_len = struct.unpack(">II", data[rec + 8:rec + 16])
            tables[tag] = t_off
        upem = struct.unpack(">H", data[tables["head"] + 18:tables["head"] + 20])[0]
        n_hm = struct.unpack(">H", data[tables["hhea"] + 34:tables["hhea"] + 36])[0]
        hmtx = tables["hmtx"]
        cmap = tables["cmap"]
        n_sub = struct.unpack(">H", data[cmap + 2:cmap + 4])[0]
        sub = None
        for i in range(n_sub):
            pid, eid, so = struct.unpack(">HHI", data[cmap + 4 + 8 * i:cmap + 12 + 8 * i])
            if pid == 3 and eid in (1, 10) or pid == 0:
                if struct.unpack(">H", data[cmap + so:cmap + so + 2])[0] == 4:
                    sub = cmap + so
                    break
        if sub is None:
            return None
        seg2 = struct.unpack(">H", data[sub + 6:sub + 8])[0]
        ends = sub + 14
        starts = ends + seg2 + 2
        deltas = starts + seg2
        ranges = deltas + seg2

        def glyph(c: int) -> int:
            for s in range(seg2 // 2):
                end = struct.unpack(">H", data[ends + 2 * s:ends + 2 * s + 2])[0]
                if c > end:
                    continue
                start = struct.unpack(">H", data[starts + 2 * s:starts + 2 * s + 2])[0]
                if c < start:
                    return 0
                delta = struct.unpack(">h", data[deltas + 2 * s:deltas + 2 * s + 2])[0]
                ro_at = ranges + 2 * s
                ro = struct.unpack(">H", data[ro_at:ro_at + 2])[0]
                if ro == 0:
                    return (c + delta) & 0xFFFF
                g_at = ro_at + ro + 2 * (c - start)
                g = struct.unpack(">H", data[g_at:g_at + 2])[0]
                return (g + delta) & 0xFFFF if g else 0
            return 0

        def advance(g: int) -> int:
            g = min(g, n_hm - 1)
            return struct.unpack(">H", data[hmtx + 4 * g:hmtx + 4 * g + 2])[0]

        widths = {c: advance(glyph(c)) for c in range(32, 256)}
        for c in (0x00B7, 0x2013):
            widths[c] = advance(glyph(c))
        return upem, widths
    except (OSError, KeyError, struct.error, IndexError, UnicodeDecodeError):
        return None


def text_width(path: Optional[str], text: str, size: float) -> float:
    """Bredden i pixlar för text i storleken size (px)."""
    m = _metrics(path) if path else None
    if m is None:
        return FALLBACK_EM * size * len(text)
    upem, widths = m
    avg = sum(widths.values()) / max(1, len(widths))
    return sum(widths.get(ord(ch), avg) for ch in text) * size / upem
