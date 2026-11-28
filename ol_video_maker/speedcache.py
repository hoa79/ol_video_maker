"""Minne för hur snabbt den här datorn skapar video (för tidsuppskattningen i steg 5).

Förr gjorde programmet en kort provvideo i Full HD och i 4K varje gång steg 5 visades, och
igen så fort man ändrat något. Nu sparas hastigheten (sekunder video per sekund) för en
*sorts* film: headcam- och Livelox-filmens upplösning, bildfrekvens och codec, om pulsen är
med och vilken upplösning videon skapas i. Samma sorts filmer nästa gång behöver då ingen
provvideo alls. När en riktig video skapats sparas den uppmätta hastigheten, så att
uppskattningen blir bättre för varje video.

Filen ligger bredvid godkännandet av villkoren:
    %LOCALAPPDATA%\\OL Video Maker\\hastighet.json   (Windows)
Den innehåller inga filnamn eller sökvägar, bara filmformat och hastigheter.
"""
import json
import os
import threading
import time
from typing import Dict, Optional

from . import __version__, villkor

FILE_NAME = "hastighet.json"
MAX_ENTRIES = 40
# Hur mycket en ny mätning väger mot den sparade (resten är det gamla värdet).
NEW_WEIGHT = {"prov": 0.5, "video": 0.7}

_lock = threading.Lock()


def _path() -> str:
    return os.path.join(villkor.data_dir(), FILE_NAME)


def profile(hc, ll, pulse: bool, scale: int, extra: str = "") -> str:
    """Nyckel för en sorts film. hc och ll är MediaInfo (eller None)."""
    def fmt(info) -> str:
        if info is None:
            return "-"
        return f"{info.width}x{info.height}@{info.fps}/{info.codec or '?'}"
    hw = "utan-grafikkort" if os.environ.get("OLVM_NO_HW") else "grafikkort"
    return (f"v{__version__}|{hw}|hc {fmt(hc)}|ll {fmt(ll)}|puls {int(bool(pulse))}"
            f"|skala {scale}{'|' + extra if extra else ''}")


def _load() -> Dict[str, Dict]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items()
                if isinstance(v, dict) and isinstance(v.get("fart"), (int, float))
                and v["fart"] > 0}
    except (OSError, ValueError, AttributeError):
        return {}


def get(key: str) -> Optional[float]:
    """Sparad hastighet (sekunder video per sekund) eller None."""
    with _lock:
        entry = _load().get(key)
    return float(entry["fart"]) if entry else None


def put(key: str, speed: float, source: str = "prov") -> None:
    """Spara en uppmätt hastighet. Går det inte att spara mäts det igen nästa gång."""
    if not speed or speed <= 0:
        return
    with _lock:
        data = _load()
        old = data.get(key)
        if old is not None:
            w = NEW_WEIGHT.get(source, 0.5)
            speed = w * speed + (1 - w) * float(old["fart"])
        data[key] = {"fart": round(speed, 4), "kalla": source, "tid": int(time.time())}
        if len(data) > MAX_ENTRIES:
            keep = sorted(data.items(), key=lambda kv: kv[1].get("tid", 0))[-MAX_ENTRIES:]
            data = dict(keep)
        try:
            os.makedirs(villkor.data_dir(), exist_ok=True)
            tmp = _path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _path())
        except OSError:
            pass
