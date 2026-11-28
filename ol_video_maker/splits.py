"""Sträcktider som användaren kopierar från Livelox och klistrar in i programmet.

Livelox tabell kopieras som tabbseparerad text med två rader per löpare:

    Pl  Name         Time   Time behind  3          5          …
        Club                             513 m      482 m      …
    1   Lova Strand  47:12  −0:31        8:02 (8)   5:56 (3)   …   <- sträcktid (placering)
        OK Tallen                        8:02 (8)   13:58 (2)  …   <- tid totalt (placering)

Felstämplade (mp) och ej startade (dns) står längst ned. Tider kan vara h:mm:ss och
minustecknet kan vara Unicode (−). Allt tolkas här med Pythons standardbibliotek.
Programmet hämtar aldrig något från Livelox: användaren kopierar tabellen själv.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_PLACE = re.compile(r"^\((\d+)\)$")
_LENGTH = re.compile(r"^(\d+)\s*m$")
STATUSES = {"mp", "dns", "dnf", "dsq", "dq", "ot", "ej start", "utg", "felst", "disk", "bröt",
            "ejs", "ej st"}
NOT_STARTED = {"dns", "ej start", "ejs", "ej st"}
TREND_LIMIT = 3          # så många placeringar på en sträcka räknas som snabb/trött
MAX_SHOWN = 8            # högst så många löpare i videon (fler blir en klump på linjen)


def parse_seconds(text: str) -> Optional[int]:
    s = (text or "").strip().replace("−", "-").lstrip("+-")
    m = _TIME.match(s)
    if not m:
        return None
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se)


def _place(text: str) -> Optional[int]:
    m = _PLACE.match((text or "").strip())
    return int(m.group(1)) if m else None


def _pairs(cells: Sequence[str]) -> List[Tuple[Optional[int], Optional[int]]]:
    """Celler från kolumn 5 och framåt -> (tid, placering) parvis."""
    c = list(cells[4:])
    return [(parse_seconds(c[i]), _place(c[i + 1] if i + 1 < len(c) else ""))
            for i in range(0, len(c), 2)]


@dataclass
class Runner:
    name: str
    club: str
    place: Optional[int]                  # placering i mål (None för mp/dns)
    time: Optional[int]                   # sluttid i sekunder
    status: str                           # "ok", "mp", "dns" …
    splits: List[Optional[int]]           # tid totalt vid varje kontroll (sista = mål)
    split_places: List[Optional[int]]     # placering totalt vid varje kontroll

    @property
    def started(self) -> bool:
        return self.status not in NOT_STARTED

    @property
    def selectable(self) -> bool:
        """Kan visas i videon: har startat och har en tid vid första kontrollen."""
        return self.started and bool(self.splits) and self.splits[0] is not None


@dataclass
class Splits:
    controls: List[str]                   # kontrollnummer, sista är "Mål"
    lengths: List[float]                  # sträckornas längd i meter (lika långa om okänt)
    runners: List[Runner] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(self.lengths)


def parse(text: str) -> Splits:
    """Tolka inklistrad text. Ger tomt resultat (inga löpare) om inget känns igen."""
    rows = [r.split("\t") for r in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    rows = [r + [""] * (4 - len(r)) for r in rows if any(x.strip() for x in r)]
    controls: List[str] = []
    lengths: List[int] = []
    for r in rows[:4]:   # rubrikrader: kontrollnummer och sträcklängder
        key = r[1].strip().lower()
        if key in ("name", "namn"):
            controls = [x.strip() for x in r[4::2] if x.strip()]
        if key in ("club", "klubb"):
            lengths = [int(m.group(1)) for x in r[4::2] if (m := _LENGTH.match(x.strip()))]
    runners: List[Runner] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        tstat = r[2].strip().lower()
        if r[1].strip() and r[1].strip().lower() not in ("name", "namn") and (
                parse_seconds(tstat) is not None or tstat in STATUSES):
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            has_club = (nxt is not None and not nxt[0].strip() and not nxt[2].strip()
                        and nxt[1].strip() != "")
            leg = _pairs(r)
            tot = _pairs(nxt) if has_club else []
            n = max(len(leg), len(tot))
            leg += [(None, None)] * (n - len(leg))
            tot += [(None, None)] * (n - len(tot))
            cum: List[Optional[int]] = [t for t, _ in tot]
            # Saknas tiden totalt men sträcktiden finns: räkna ut den.
            for k in range(n):
                if cum[k] is None and leg[k][0] is not None:
                    before = 0 if k == 0 else cum[k - 1]
                    if before is not None:
                        cum[k] = before + leg[k][0]
            ok = parse_seconds(tstat) is not None
            runners.append(Runner(
                name=" ".join(r[1].split()), club=" ".join(nxt[1].split()) if has_club else "",
                place=int(r[0]) if r[0].strip().isdigit() else None,
                time=parse_seconds(r[2]) if ok else None,
                status="ok" if ok else tstat,
                splits=cum, split_places=[p for _, p in tot]))
            i += 2 if has_club else 1
        else:
            i += 1
    # Antal kolumner (kontroller + mål): det största av rubriken, längderna och tiderna.
    n = max([len(lengths), len(controls)] +
            [max((k + 1 for k, t in enumerate(x.splits) if t is not None), default=0)
             for x in runners])
    for x in runners:
        x.splits = (x.splits + [None] * n)[:n]
        x.split_places = (x.split_places + [None] * n)[:n]
        # Sluttiden är tiden vid målet om tabellen saknar den sista kolumnen.
        if x.status == "ok" and x.time is not None and n and x.splits[-1] is None:
            x.splits[-1] = x.time
    if controls and len(controls) == n:
        controls = controls[:-1]
    controls = (controls + [str(k + 1) for k in range(len(controls), n - 1)])[:max(0, n - 1)]
    if len(lengths) != n:
        lengths = [1] * n          # okända längder: alla sträckor lika långa
    return Splits(controls=controls + ["Mål"], lengths=[float(v) for v in lengths],
                  runners=runners)


# ---------------------------------------------------------------------------
# Namn
# ---------------------------------------------------------------------------


def short_names(names: Sequence[str]) -> List[str]:
    """Förnamn + första bokstaven i efternamnet ("Robin S."). Krockar två får fler bokstäver
    ("Elias Ho." och "Elias Ha.")."""
    parts = [n.split() for n in names]
    out = []
    for i, d in enumerate(parts):
        if len(d) <= 1:
            out.append(d[0] if d else "")
            continue
        n = 1
        while True:
            clash = [j for j, e in enumerate(parts) if j != i and len(e) > 1 and e[0] == d[0]
                     and e[-1][:n] == d[-1][:n]]
            if not clash or n >= len(d[-1]):
                break
            n += 1
        out.append(f"{d[0]} {d[-1][:n]}.")
    return out


def ordinal(n: int) -> str:
    """1:a, 2:a, 3:e … 11:e, 12:e … 21:a, 22:a (svenska)."""
    last2 = n % 100
    return f"{n}:a" if n % 10 in (1, 2) and last2 not in (11, 12) else f"{n}:e"


# ---------------------------------------------------------------------------
# Läget under loppet
# ---------------------------------------------------------------------------


@dataclass
class Moment:
    """Var en löpare är vid en viss lopptid."""
    pos: float                 # 0..1 av banan (sträcklängd-viktat)
    visible: float             # 1 = syns, 0 = borta (felstämplad efter den saknade kontrollen)
    passed: int                # index för senast passerade kontroll, -1 = inte passerat någon
    place: Optional[int]       # placering vid den kontrollen
    trend: int                 # placeringar vunna (+) eller tappade (−) på sträckan nu
    finished: bool


FADE_SECONDS = 1.5


def moment(sp: Splits, r: Runner, t: float) -> Moment:
    tot = sp.total or 1.0
    known = [(k, v) for k, v in enumerate(r.splits) if v is not None]
    # Felstämplad: första kontrollen som saknas och som har en känd kontroll före eller efter.
    missing = next((k for k, v in enumerate(r.splits) if v is None), None)
    # Läget: linjärt mellan kontroller med kända tider (sträcklängd-viktat).
    pos_at = [sum(sp.lengths[:k + 1]) / tot for k in range(len(sp.lengths))]
    prev_t, prev_p = 0.0, 0.0
    pos = 1.0 if known else 0.0
    for k, tk in known:
        if t <= tk:
            span = max(1e-6, tk - prev_t)
            pos = prev_p + (pos_at[k] - prev_p) * max(0.0, t - prev_t) / span
            break
        prev_t, prev_p = tk, pos_at[k]
    visible = 1.0
    if missing is not None:
        # Löparen når den saknade kontrollen när pos passerar dess läge: tona bort där.
        miss_pos = pos_at[missing]
        if pos >= miss_pos - 1e-9 and known:
            # tidpunkt då miss_pos nåddes (interpolerat), annars sista kända tiden
            before = [(k, v) for k, v in known if k < missing]
            after = [(k, v) for k, v in known if k > missing]
            t0, p0 = (before[-1][1], pos_at[before[-1][0]]) if before else (0.0, 0.0)
            if after:
                t1, p1 = after[0][1], pos_at[after[0][0]]
                t_miss = t0 + (t1 - t0) * (miss_pos - p0) / max(1e-6, p1 - p0)
            else:
                t_miss = t0
            visible = max(0.0, 1.0 - (t - t_miss) / FADE_SECONDS)
            pos = min(pos, miss_pos)
        elif not known:
            visible = 0.0
    passed = max((k for k, v in known if v <= t), default=-1)
    if missing is not None:
        passed = min(passed, missing - 1)
    place = r.split_places[passed] if passed >= 0 else None
    trend = 0
    if passed >= 0 and passed + 1 < len(r.split_places):
        nxt = r.split_places[passed + 1]
        if place and nxt:
            trend = place - nxt
    finished = bool(r.splits) and r.splits[-1] is not None and t >= r.splits[-1] and missing is None
    if finished:
        trend = 0
        pos = 1.0
    return Moment(pos=pos, visible=visible, passed=passed, place=place, trend=trend,
                  finished=finished)


# ---------------------------------------------------------------------------
# Urval
# ---------------------------------------------------------------------------


def preset(sp: Splits, kind: str, me: Optional[int]) -> List[int]:
    """Förval i listan. Index i sp.runners. "du" räknas in i max 8."""
    ok = [i for i, r in enumerate(sp.runners) if r.selectable]
    mine = [me] if me is not None and me in ok else []
    others = [i for i in ok if i not in mine]
    if kind == "top":
        n = 5 if mine else MAX_SHOWN
        return sorted(set(others[:n] + mine))
    if kind == "near" and mine:
        finished = [i for i in ok if sp.runners[i].status == "ok"]
        if me in finished:
            k = finished.index(me)
            lo = max(0, min(k - 3, len(finished) - MAX_SHOWN))
            return finished[lo:lo + MAX_SHOWN]
        return mine + others[:MAX_SHOWN - 1]
    if kind == "club" and mine:
        club = sp.runners[me].club
        same = [i for i in others if club and sp.runners[i].club == club]
        return sorted(mine + same[:MAX_SHOWN - 1])
    return mine


# ---------------------------------------------------------------------------
# "Vem är du?" sparas till nästa gång (bara namnet, på den egna datorn)
# ---------------------------------------------------------------------------

ME_FILE = "placering.json"


def _me_path() -> str:
    from .villkor import data_dir
    return os.path.join(data_dir(), ME_FILE)


def load_me() -> Optional[str]:
    try:
        with open(_me_path(), encoding="utf-8") as f:
            v = json.load(f).get("namn")
        return v if isinstance(v, str) and v else None
    except (OSError, ValueError, AttributeError):
        return None


def save_me(name: Optional[str]) -> None:
    """Spara ditt namn, eller ta bort filen (name=None)."""
    path = _me_path()
    try:
        if name is None:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"namn": name}, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def summary(sp: Splits) -> Dict[str, int]:
    return {"runners": len(sp.runners), "controls": len(sp.controls),
            "mp": sum(1 for r in sp.runners if r.started and r.status != "ok"),
            "dns": sum(1 for r in sp.runners if not r.started)}
