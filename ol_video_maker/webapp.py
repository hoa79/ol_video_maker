"""Fönstret i OL Video Maker, byggt med HTML (samma utseende som UX-skisserna).

Programmet startar en liten webbserver som bara den egna datorn når (127.0.0.1) och
öppnar sidan i ett eget appfönster i Microsoft Edge (finns i Windows 10 och 11). Edge
ritar text och bilder skarpt i alla skalningar (125 %, 150 % …). Inga nya program eller
bibliotek behövs: servern är Pythons egen http.server, och filväljarna är Windows egna
(via tkinter som redan följer med Python).

Guiden har fem steg:
1. Välj filmer (och frivilligt vad som visas under filmen: puls eller placering)
   1b. Placering (bara om man valt placering): klistra in sträcktider från Livelox
2. Synka starten (bläddra fram samma ögonblick i båda filmerna)
   2b. Synka pulsen (bara om man valt en pulsfil)
3. Välj slutet
4. Titta på en provbit
5. Skapa videon

Sidan (web/index.html, app.css, app.js) frågar servern efter läget med /api/state och
skickar knapptryck till /api/<åtgärd>. Bilder ur filmerna hämtas med /frame och /play.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__, speedcache, vila, villkor
from .core import (DEFAULT_LIVELOX_WIDTH, OUT_W, Job, MediaInfo, VideoMakerError, check_tools,
                   ensure_mp4, find_tool, format_time, is_packaged, probe, render,
                   sort_headcam_files)
from .filedialogs import ask_open, ask_save
from .fitfile import HeartRate, load_fit
from .frames import Timeline, grab_frame, mjpeg_command
from .placing_video import PlacingTrack, svg_at
from .pulse_video import PulseTrack
from . import splits as splitmod

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
GUIDE_URL = "https://github.com/hoa79/ol_video_maker/blob/main/docs/GUIDE.md"
FIT_TYPES = [("Pulsfil från klockan", "*.fit *.FIT *.zip *.ZIP"), ("Alla filer", "*.*")]
VIDEO_TYPES = [("Videofiler", "*.mp4 *.MP4 *.mov *.MOV *.mkv *.webm *.avi"), ("Alla filer", "*.*")]
LEAD_IN = 5.0          # sekunder före starten som tas med i videon
PREVIEW_SECONDS = 20   # längd på provvideor
PREVIEW_PREFIX = "OL_Video_provvideo_"
BENCH_SECONDS = 6.0    # så lång provbit som skapas för att mäta datorns hastighet
MIN_LEARN_SECONDS = 20.0  # kortare videor än så används inte för att lära sig hastigheten
LW_MIN, LW_MAX = 320, 960
MAX_FRAME = (1920, 1080)
HEARTBEAT_TIMEOUT = 8.0   # sekunder utan kontakt från sidan innan programmet avslutas
# Medan en video skapas: ett minimerat fönster frågar sällan (webbläsaren sparar ström), så
# programmet väntar mycket längre innan det tror att fönstret är stängt. Stängs fönstret på
# riktigt säger sidan till (/api/bye) och då avslutas programmet direkt.
HEARTBEAT_TIMEOUT_RUNNING = 600.0
FIRST_CONTACT_TIMEOUT = 90.0
CREATE_NO_WINDOW = 0x08000000


def fmt(seconds: Optional[float]) -> str:
    """02:15,4 (svenskt decimaltecken)."""
    if seconds is None:
        return "–"
    return format_time(seconds).replace(".", ",")


def fmt_long(seconds: float) -> str:
    """Längd i ord, t.ex. "45 min 30 s"."""
    s = int(round(seconds))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    if h:
        return f"{h} tim {m} min"
    if m:
        return f"{m} min {s} s"
    return f"{s} s"


def _open_file(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _open_folder(path: str) -> None:
    if os.name == "nt" and os.path.isfile(path):
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        except OSError:
            pass
    _open_file(os.path.dirname(os.path.abspath(path)))


# Hjälpsidorna: (fil i Windows-paketet, fil i repot). open_help öppnar en av dem.
HELP_PAGES = {"guide": ("Guide.html", "GUIDE.md"),
              "villkor": ("Villkor.html", "VILLKOR.md"),
              "installera": ("Installationsguide.html", "INSTALLERA.md")}


def _guide_path(page: str = "guide") -> Optional[str]:
    """Guiden (eller villkoren) som följer med programmet, om den finns."""
    html, md = HELP_PAGES.get(page, HELP_PAGES["guide"])
    roots = []
    if is_packaged():
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for root in roots:
        for name in (html, os.path.join("docs", md)):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                return p
    return None


def _remove_old_previews() -> None:
    """Städa bort provvideor från tidigare körningar (låsta filer hoppas över)."""
    folder = tempfile.gettempdir()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.startswith(PREVIEW_PREFIX) and name.endswith(".mp4"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def clip_names(tl: Optional[Timeline]) -> str:
    if not tl:
        return ""
    return " + ".join(os.path.basename(c.path) for c in tl.clips)


# ---------------------------------------------------------------------------
# Filväljare (Windows egna rutor, se filedialogs.py; alltid i huvudtråden)
# ---------------------------------------------------------------------------


class Dialogs:
    """Kör filväljarna i huvudtråden. Webbserverns trådar lämnar in en beställning och väntar."""

    def __init__(self) -> None:
        self.jobs: "queue.Queue[tuple]" = queue.Queue()
        self._root = None

    def call(self, fn: Callable[[Any], Any]) -> Any:
        done = threading.Event()
        box: Dict[str, Any] = {}
        self.jobs.put((fn, done, box))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def run_pending(self, timeout: float) -> None:
        try:
            fn, done, box = self.jobs.get(timeout=timeout)
        except queue.Empty:
            return
        try:
            box["result"] = fn(self.tk)
        except Exception as e:  # visas för användaren som ett fel
            box["error"] = e
        finally:
            done.set()

    def tk(self):
        import tkinter as tk
        if self._root is None:
            if os.name == "nt":
                _dpi_aware()
            self._root = tk.Tk()
            self._root.withdraw()
        root = self._root
        # Rutan ska hamna framför appfönstret.
        root.attributes("-topmost", True)
        root.lift()
        root.update()
        return root

    def close(self) -> None:
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None


def _dpi_aware() -> None:
    """Skarpa filväljare i Windows med skalning (125 %, 150 % …)."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Programmets läge
# ---------------------------------------------------------------------------


class State:
    """Allt användaren har valt. Sidan läser det med /api/state."""

    def __init__(self, dialogs: Dialogs) -> None:
        self.dialogs = dialogs
        self.lock = threading.RLock()
        self.hc_tl: Optional[Timeline] = None
        self.ll_tl: Optional[Timeline] = None
        self.hc_infos: List[MediaInfo] = []
        self.ll_info: Optional[MediaInfo] = None
        self.loading: Dict[str, bool] = {"headcam": False, "livelox": False, "fit": False}
        self.errors: Dict[str, Optional[str]] = {"headcam": None, "livelox": None, "fit": None}
        self.hc_sync: Optional[float] = None
        self.ll_sync: Optional[float] = None
        self.end_time: Optional[float] = None
        self.hr: Optional[HeartRate] = None
        self.hr_id = 0
        self.fit_start: Optional[float] = None
        # Fältet under videon räcker till en sak: "none", "pulse" eller "placing".
        self.under = "none"
        # Placeringen: inklistrade sträcktider (bara i minnet), vem du är och vilka som syns.
        self.splits_text = ""
        self.splits: Optional[splitmod.Splits] = None
        self.splits_id = 0
        self.me: Optional[str] = None       # ditt namn, "" = Ingen (visa bara linjen)
        self.shown: List[str] = []          # namnen på dem som syns (utom du)
        self.consent = False                # får inte vara förifylld (juristen)
        self.hc_flip = False
        self.livelox_width = DEFAULT_LIVELOX_WIDTH
        self.output = ""
        self.quality = 1
        # Hur snabbt datorn skapar video (sekunder video per sekund) i Full HD (1) och 4K (2),
        # för den sorts filmer som är valda. Tiden räknas fram ur längden, så att ändrad start
        # eller målgång inte kräver någon ny provvideo. Se speedcache.py.
        self.speeds: Dict[int, Optional[float]] = {1: None, 2: None}
        self.bench_key = None
        self.bench_running = False
        self.bench_cancel = False
        self.tools_error: Optional[str] = None
        # Pågående video (provvideo eller den riktiga)
        self.running = False
        self.cancel = False
        self.proc: Optional[subprocess.Popen] = None
        self.render: Dict[str, Any] = {"kind": None, "text": "", "progress": 0.0, "ok": None,
                                       "path": None, "error": None, "slept": 0, "seq": 0}
        self.current_output = ""
        # Villkoren (ansvarsfriskrivningen) måste godkännas innan programmet används.
        self.terms_accepted = villkor.is_accepted()
        self.quit_requested = False

    # --- läget som JSON ---
    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            def tl_info(tl: Optional[Timeline]):
                if tl is None:
                    return None
                return {"names": clip_names(tl), "duration": tl.duration, "fps": tl.fps,
                        "files": [os.path.basename(c.path) for c in tl.clips],
                        "length": fmt_long(tl.duration)}
            hr = self.hr
            hr_info = None
            if hr is not None:
                hr_info = {"id": self.hr_id, "name": hr.name, "start": hr.start, "end": hr.end,
                           "duration": hr.duration, "min": min(hr.bpm), "max": max(hr.bpm),
                           "minutes": max(1, round(hr.duration / 60))}
            return {
                "version": __version__,
                "toolsError": self.tools_error,
                "hc": tl_info(self.hc_tl), "ll": tl_info(self.ll_tl),
                "loading": dict(self.loading), "errors": dict(self.errors),
                "hcSync": self.hc_sync, "llSync": self.ll_sync, "endTime": self.end_time,
                "flip": self.hc_flip, "liveloxWidth": self.livelox_width,
                "lwMin": LW_MIN, "lwMax": LW_MAX, "outW": OUT_W,
                "hr": hr_info, "fitStart": self.fit_start,
                "pulse": self.pulse_track() is not None,
                "under": self.under, "placingOn": self.placing_track() is not None,
                "placing": self.placing_info(),
                "videoLength": self.video_length(), "leadIn": LEAD_IN,
                "previewSeconds": PREVIEW_SECONDS,
                "output": self.output, "quality": self.quality,
                "estimates": {str(k): self.estimate(k) for k in (1, 2)},
                "benchRunning": self.bench_running,
                "running": self.running, "render": dict(self.render),
                "termsAccepted": self.terms_accepted,
                "terms": None if self.terms_accepted else villkor.as_dict(),
            }

    def video_length(self) -> float:
        if not self.hc_tl or self.hc_sync is None:
            return 0.0
        end = self.end_time if self.end_time is not None else self.hc_tl.duration
        return max(0.0, end - max(0.0, self.hc_sync - LEAD_IN))

    def pulse_track(self) -> Optional[PulseTrack]:
        if self.under != "pulse" or self.hr is None or self.fit_start is None or self.hc_sync is None or not self.hc_tl:
            return None
        end = self.end_time if self.end_time is not None else self.hc_tl.duration
        return PulseTrack(self.hr, offset=self.fit_start - self.hc_sync,
                          graph_start=max(0.0, self.hc_sync - LEAD_IN), graph_end=end)

    # --- placeringen ---
    def _index(self, name: Optional[str]) -> Optional[int]:
        if not name or self.splits is None:
            return None
        for i, r in enumerate(self.splits.runners):
            if r.name == name:
                return i
        return None

    def _shown_idx(self) -> List[int]:
        out = [i for i in (self._index(n) for n in self.shown) if i is not None
               and self.splits.runners[i].selectable]
        me = self._index(self.me)
        return [i for i in out if i != me]

    def placing_track(self) -> Optional[PlacingTrack]:
        if self.under != "placing" or self.splits is None or self.hc_sync is None:
            return None
        me = self._index(self.me)
        shown = self._shown_idx()
        if me is None and not shown:
            return None
        if me is not None and not self.splits.runners[me].selectable:
            me = None
        return PlacingTrack(self.splits, shown, me, self.consent, race_start=self.hc_sync)

    def placing_info(self) -> Optional[Dict[str, Any]]:
        sp = self.splits
        if sp is None:
            return {"id": self.splits_id, "text": self.splits_text, "runners": [], "me": self.me,
                    "shown": [], "consent": self.consent, "summary": None,
                    "saved": splitmod.load_me() is not None, "max": splitmod.MAX_SHOWN}
        names = splitmod.short_names([r.name for r in sp.runners])
        runners = [{"name": r.name, "club": r.club, "place": r.place, "time": r.time,
                    "status": r.status, "selectable": r.selectable, "short": names[i]}
                   for i, r in enumerate(sp.runners)]
        return {"id": self.splits_id, "text": self.splits_text, "runners": runners,
                "me": self.me, "meIndex": self._index(self.me), "shown": self._shown_idx(),
                "consent": self.consent, "summary": splitmod.summary(sp),
                "saved": splitmod.load_me() is not None, "max": splitmod.MAX_SHOWN}

    def make_job(self, output: str) -> Job:
        return Job(headcam_files=[c.path for c in self.hc_tl.clips],
                   livelox_file=self.ll_tl.clips[0].path, output_file=output,
                   headcam_sync=self.hc_sync or 0.0, livelox_sync=self.ll_sync or 0.0,
                   lead_in=LEAD_IN, end_time=self.end_time, livelox_width=self.livelox_width,
                   headcam_flip=self.hc_flip, pulse=self.pulse_track(),
                   placing=self.placing_track())

    # --- åtgärder från sidan ---
    def check_tools(self) -> None:
        try:
            check_tools()
            self.tools_error = None
        except VideoMakerError as e:
            self.tools_error = str(e)

    def accept_terms(self, body) -> Dict[str, Any]:
        if (body or {}).get("version") != villkor.VERSION:
            return {"error": {"title": "Villkoren har ändrats",
                              "text": "Läs den nya texten och godkänn den igen."}}
        villkor.accept()
        with self.lock:
            self.terms_accepted = True
        return {}

    def decline_terms(self, _body) -> Dict[str, Any]:
        """Avsluta utan att godkänna: inget sparas och programmet stängs."""
        self.quit_requested = True
        return {}

    def pick_headcam(self, _body) -> Dict[str, Any]:
        files = self.dialogs.call(lambda tk: ask_open(
            tk, "Välj headcam-film (flera filer: Shift eller Ctrl)", VIDEO_TYPES, multiple=True))
        if not files:
            return {}
        ordered, mixed = sort_headcam_files(list(files))
        if mixed:
            return {"confirm": {
                "title": "Olika inspelningar?",
                "text": "Filerna verkar komma från olika inspelningar (de sista fyra siffrorna i "
                        "filnamnen skiljer sig). Välj bara filerna från ett lopp.",
                "list": [os.path.basename(f) for f in ordered],
                "question": "Vill du ändå använda alla filerna, i den här ordningen?",
                "yes": "Använd filerna", "action": "load",
                "body": {"which": "headcam", "files": ordered}}}
        self.load({"which": "headcam", "files": ordered})
        return {}

    def pick_livelox(self, _body) -> Dict[str, Any]:
        files = self.dialogs.call(lambda tk: ask_open(tk, "Välj Livelox-inspelning", VIDEO_TYPES))
        if files:
            self.load({"which": "livelox", "files": files[:1]})
        return {}

    def load(self, body) -> Dict[str, Any]:
        which = body["which"]
        paths = [str(p) for p in body["files"]]
        if which not in ("headcam", "livelox") or not paths:
            return {}
        with self.lock:
            self.loading[which] = True
            self.errors[which] = None

        def work():
            infos, tl, error = None, None, None
            try:
                infos = [probe(p) for p in paths]
                tl = Timeline.from_infos(infos)
            except VideoMakerError as e:
                error = str(e)
            except Exception as e:  # oväntat fel
                error = f"Oväntat fel: {e}"
            with self.lock:
                self.loading[which] = False
                self.errors[which] = error
                if error is None:
                    if which == "headcam":
                        self.hc_infos, self.hc_tl = infos, tl
                        self.hc_sync = None
                        self.end_time = None
                        folder = os.path.dirname(tl.clips[0].path)
                        self.output = os.path.join(folder, "OL-video.mp4")
                    else:
                        self.ll_info, self.ll_tl = infos[0], tl
                        self.ll_sync = None
        threading.Thread(target=work, daemon=True).start()
        return {}

    def pick_fit(self, _body) -> Dict[str, Any]:
        files = self.dialogs.call(lambda tk: ask_open(tk, "Välj pulsfil från klockan", FIT_TYPES))
        if files:
            self.load_fit({"file": files[0]})
        return {}

    def load_fit(self, body) -> Dict[str, Any]:
        path = str(body["file"])
        with self.lock:
            self.loading["fit"] = True
            self.errors["fit"] = None

        def work():
            hr, error = None, None
            try:
                hr = load_fit(path)
            except VideoMakerError as e:
                error = str(e)
            except Exception as e:
                error = f"Oväntat fel: {e}"
            with self.lock:
                self.loading["fit"] = False
                self.errors["fit"] = error
                if error is None:
                    self.hr = hr
                    self.hr_id += 1
                    self.fit_start = None
                    self.under = "pulse"   # en ny pulsfil: visa pulsen under videon
        threading.Thread(target=work, daemon=True).start()
        return {}

    def remove_fit(self, _body) -> Dict[str, Any]:
        with self.lock:
            self.hr = None
            self.fit_start = None
            self.errors["fit"] = None
        return {}

    def set_under(self, body) -> Dict[str, Any]:
        what = (body or {}).get("what")
        if what in ("none", "pulse", "placing"):
            with self.lock:
                self.under = what
        return {}

    def set_splits(self, body) -> Dict[str, Any]:
        text = str((body or {}).get("text") or "")
        sp = splitmod.parse(text) if text.strip() else None
        with self.lock:
            self.splits_text = text
            self.splits_id += 1
            if sp is None or not sp.runners:
                self.splits = None
                return {}
            self.splits = sp
            names = {r.name for r in sp.runners}
            if self.me and self.me not in names:
                self.me = None
            if self.me is None:
                saved = splitmod.load_me()
                if saved in names:
                    self.me = saved
            self.shown = [n for n in self.shown if n in names]
            if not self.shown and self.me is not None:
                self._preset("top")
        return {}

    def _preset(self, kind: str) -> None:
        me = self._index(self.me)
        idx = splitmod.preset(self.splits, kind, me)
        self.shown = [self.splits.runners[i].name for i in idx if i != me]

    def set_me(self, body) -> Dict[str, Any]:
        name = (body or {}).get("name")
        with self.lock:
            if self.splits is None:
                return {}
            if name == "":
                self.me = ""
            elif self._index(name) is not None:
                self.me = name
                splitmod.save_me(name)   # bara namnet, på den här datorn
                self.shown = [n for n in self.shown if n != name]
                if len(self.shown) > splitmod.MAX_SHOWN - 1:
                    self.shown = self.shown[:splitmod.MAX_SHOWN - 1]
            else:
                return {}
            if not self.shown:
                self._preset("top")
        return {}

    def forget_me(self, _body) -> Dict[str, Any]:
        splitmod.save_me(None)
        return {}

    def set_shown(self, body) -> Dict[str, Any]:
        with self.lock:
            if self.splits is None:
                return {}
            me = self._index(self.me)
            limit = splitmod.MAX_SHOWN - (1 if me is not None else 0)
            out: List[str] = []
            for i in (body or {}).get("shown") or []:
                i = int(i)
                if 0 <= i < len(self.splits.runners) and i != me \
                        and self.splits.runners[i].selectable:
                    name = self.splits.runners[i].name
                    if name not in out:
                        out.append(name)
            if len(out) > limit:
                return {"error": {"title": f"Högst {splitmod.MAX_SHOWN} löpare",
                                  "text": f"Det får plats högst {splitmod.MAX_SHOWN} löpare på "
                                          "linjen (du räknas med). Ta bort någon först."}}
            self.shown = out
        return {}

    def preset_shown(self, body) -> Dict[str, Any]:
        kind = (body or {}).get("kind")
        with self.lock:
            if self.splits is not None and kind in ("top", "near", "club", "clear"):
                self._preset(kind)
        return {}

    def set_consent(self, body) -> Dict[str, Any]:
        self.consent = bool((body or {}).get("on"))
        return {}

    def placing_svg(self, t: float, lw: int) -> Dict[str, Any]:
        with self.lock:
            tr = self.placing_track()
        if tr is None:
            return {"svg": ""}
        lw = max(LW_MIN, min(LW_MAX, lw))
        return {"svg": svg_at(tr, OUT_W - lw, t - tr.race_start)}

    def mark(self, body) -> Dict[str, Any]:
        what, t = body["what"], body.get("t")
        t = None if t is None else float(t)
        with self.lock:
            if what == "hc":
                self.hc_sync = t
                if t is not None and self.end_time is not None and self.end_time <= t:
                    self.end_time = None
            elif what == "ll":
                self.ll_sync = t
            elif what == "end":
                if t is not None and self.hc_sync is not None and t <= self.hc_sync:
                    return {"error": {"title": "För tidigt",
                                      "text": f"Slutet måste ligga efter starten ({fmt(self.hc_sync)})."
                                              " Dra reglaget längre fram."}}
                self.end_time = t
            elif what == "fit":
                self.fit_start = t
        return {}

    def set_flip(self, body) -> Dict[str, Any]:
        self.hc_flip = bool(body["on"])
        return {}

    def set_width(self, body) -> Dict[str, Any]:
        lw = int(round(float(body["width"]) / 20.0) * 20)
        self.livelox_width = max(LW_MIN, min(LW_MAX, lw))
        return {}

    def nudge(self, body) -> Dict[str, Any]:
        with self.lock:
            if self.ll_tl is not None and self.ll_sync is not None:
                self.ll_sync = max(0.0, min(self.ll_tl.duration, self.ll_sync + float(body["dt"])))
        return {}

    def set_quality(self, body) -> Dict[str, Any]:
        if not self.running:
            self.quality = 2 if int(body["q"]) == 2 else 1
        return {}

    def set_output(self, body) -> Dict[str, Any]:
        self.output = str(body["path"])
        return {}

    def pick_output(self, _body) -> Dict[str, Any]:
        cur = self.output
        f = self.dialogs.call(lambda tk: ask_save(tk, "Spara videon som", [("MP4-video", "*.mp4")],
                                                  cur))
        if f:
            self.output = ensure_mp4(f)
        return {}

    def open_help(self, body) -> Dict[str, Any]:
        page = (body or {}).get("page") or "guide"
        path = _guide_path(page)
        if path:
            _open_file(path)
        else:
            md = HELP_PAGES.get(page, HELP_PAGES["guide"])[1]
            webbrowser.open(GUIDE_URL.replace("GUIDE.md", md))
        return {}

    def open_output(self, body) -> Dict[str, Any]:
        path = self.render.get("path")
        if path:
            (_open_folder if body.get("folder") else _open_file)(path)
        return {}

    # --- provvideo och video ---
    def preview(self, body) -> Dict[str, Any]:
        kind = body.get("kind", "start")
        with self.lock:
            if self.running or not self._ready_for_video():
                return {}
            name = {"start": "starten", "end": "slutet", "here": "vald_bit"}.get(kind, "starten")
            stamp = time.strftime("%H%M%S")
            out = os.path.join(tempfile.gettempdir(), f"{PREVIEW_PREFIX}{name}_{stamp}.mp4")
            job = self.make_job(out)
            if kind == "end":
                job.tail_seconds = PREVIEW_SECONDS
            elif kind == "here":
                t = float(body.get("t", self.hc_sync))
                limit = self.end_time if self.end_time is not None else self.hc_tl.duration
                job.end_time = min(limit, t + PREVIEW_SECONDS / 2)
                job.tail_seconds = PREVIEW_SECONDS
            else:
                job.end_time = min(job.end_time or self.hc_tl.duration,
                                   self.hc_sync + PREVIEW_SECONDS - LEAD_IN)
        self._start_render("preview", job, "Gör provvideo…", open_when_done=True)
        return {}

    def make(self, body) -> Dict[str, Any]:
        out = ensure_mp4(str(body.get("path") or self.output))
        if not out:
            return {"error": {"title": "Var ska videon sparas?",
                              "text": "Välj var videon ska sparas."}}
        self.output = out
        if os.path.exists(out) and not body.get("overwrite"):
            return {"confirm": {"title": "Filen finns redan",
                                "text": f"{os.path.basename(out)} finns redan.",
                                "question": "Vill du ersätta den?", "yes": "Ersätt",
                                "action": "make", "body": {"path": out, "overwrite": True}}}
        with self.lock:
            if self.running or not self._ready_for_video():
                return {}
            self.bench_cancel = True
            job = self.make_job(out)
            job.scale = self.quality
            est = self.estimate(self.quality)
            speed_key = self._speed_key(job.scale)
            length = self.video_length()
        q = "4K" if job.scale == 2 else "Full HD"
        extra = f" Beräknad tid: {fmt_estimate(est)}." if est else ""
        self._start_render("make", job, f"Skapar videon i {q}…{extra}", open_when_done=False,
                           speed_key=speed_key, length=length)
        return {}

    def _ready_for_video(self) -> bool:
        return bool(self.hc_tl and self.ll_tl and self.hc_sync is not None
                    and self.ll_sync is not None)

    def _start_render(self, kind: str, job: Job, text: str, open_when_done: bool,
                      speed_key: Optional[str] = None, length: float = 0.0) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self.cancel = False
            self.current_output = job.output_file
            self.render = {"kind": kind, "text": text, "progress": 0.0, "ok": None, "path": None,
                           "error": None, "slept": 0, "seq": self.render["seq"] + 1}

        meter = vila.SleepMeter()

        def progress(p: float) -> None:
            self.render["progress"] = p
            self.render["slept"] = round(meter.slept())

        def work():
            path, error = None, None
            try:
                # Datorn får inte somna medan videon skapas (skärmen får släckas). Måste
                # börja och sluta i samma tråd som gör jobbet.
                with vila.keep_awake():
                    path = render(job, progress=progress, cancel_flag=lambda: self.cancel,
                                  on_process=lambda p: setattr(self, "proc", p))
            except VideoMakerError as e:
                error = str(e)
            except Exception as e:
                error = f"Oväntat fel: {e}"
            with self.lock:
                self.running = False
                self.proc = None
                r = dict(self.render)
                r["slept"] = round(meter.slept())
                if error:
                    r.update(ok=False, error=None if error == "Avbrutet." else error,
                             text="Avbrutet." if error == "Avbrutet."
                             else "Det gick inte att skapa videon.")
                else:
                    r.update(ok=True, path=path, progress=1.0,
                             text=f"✓ Klart! Sparad som {os.path.basename(path)}")
                self.render = r
            elapsed = meter.awake()  # tid i viloläge ska inte göra datorn "långsammare"
            if path and speed_key and length >= MIN_LEARN_SECONDS and elapsed > 1:
                # En riktig video är den bästa mätningen: spara den till nästa gång.
                speedcache.put(speed_key, length / elapsed, source="video")
                with self.lock:
                    if self._speed_key(job.scale) == speed_key:
                        self.speeds[job.scale] = speedcache.get(speed_key)
            if path and open_when_done:
                _open_file(path)
        threading.Thread(target=work, daemon=True).start()

    def cancel_render(self, _body) -> Dict[str, Any]:
        self.cancel = True
        self.render["text"] = "Avbryter…"
        return {}

    def shutdown(self) -> None:
        """Fönstret är stängt: avbryt en pågående video och ta bort den halvfärdiga filen."""
        self.bench_cancel = True
        if not self.running:
            return
        self.cancel = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if self.current_output:
            try:
                os.remove(self.current_output)
            except OSError:
                pass

    # --- tidsuppskattning i steg 5 ---
    def _speed_key(self, scale: int) -> Optional[str]:
        """Vilken sorts film det är, för minnet av datorns hastighet. Start, målgång,
        vändning och kartans bredd påverkar knappt hastigheten och ingår inte. Bandet under
        videon (puls eller placeringslinje) ingår, men inga namn eller sträcktider."""
        if not self.hc_infos or self.ll_info is None:
            return None
        return speedcache.profile(self.hc_infos[0], self.ll_info,
                                  self.pulse_track() is not None, scale,
                                  extra="placering" if self.placing_track() is not None else "")

    def estimate(self, scale: int) -> Optional[float]:
        speed = self.speeds.get(scale)
        if not speed:
            return None
        return self.video_length() / max(speed, 1e-3) + 5

    def bench(self, _body) -> Dict[str, Any]:
        """Ta fram datorns hastighet för de valda filmerna. Finns den sparad sedan tidigare
        (samma sorts filmer) används den direkt. Annars görs en kort provbit, bara för den
        upplösning som saknas, och resultatet sparas till nästa gång."""
        with self.lock:
            if not self._ready_for_video() or self.running:
                return {}
            keys = {scale: self._speed_key(scale) for scale in (1, 2)}
            if self.bench_key == keys and (self.bench_running or None not in self.speeds.values()):
                return {}
            self.bench_key = keys
            self.speeds = {scale: speedcache.get(k) if k else None for scale, k in keys.items()}
            missing = [scale for scale in (1, 2) if self.speeds[scale] is None]
            if not missing:
                return {}
            self.bench_running = True
            self.bench_cancel = False

        def measure(scale):
            out = os.path.join(tempfile.gettempdir(),
                               f"{PREVIEW_PREFIX}tidtest_{scale}_{time.strftime('%H%M%S')}.mp4")
            with self.lock:
                job = self.make_job(out)
                start = self.hc_sync or 0.0
                job.end_time = min(self.hc_tl.duration, start + BENCH_SECONDS)
            job.scale = scale
            job.lead_in = 0.0
            marks = []
            try:
                render(job, progress=lambda p: marks.append((time.monotonic(), p)),
                       cancel_flag=lambda: self.bench_cancel or self.running)
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
            moving = [m for m in marks if 0 < m[1] < 1]
            seg = job.end_time - start
            if len(moving) >= 2 and moving[-1][1] > moving[0][1]:
                return (moving[-1][1] - moving[0][1]) * seg / (moving[-1][0] - moving[0][0])
            return seg / max(0.5, marks[-1][0] - marks[0][0]) if len(marks) >= 2 else 1.0

        def work():
            try:
                for scale in missing:
                    with self.lock:
                        if self.bench_key != keys:
                            return  # filmerna har bytts, en ny mätning har tagit över
                    try:
                        speed = measure(scale)
                    except Exception:
                        with self.lock:
                            if self.bench_key == keys:
                                self.bench_key = None  # mät igen nästa gång steget visas
                        return
                    speedcache.put(keys[scale], speed, source="prov")
                    with self.lock:
                        if self.bench_key == keys:
                            self.speeds[scale] = speed
            finally:
                with self.lock:
                    if self.bench_key == keys or self.bench_key is None:
                        self.bench_running = False
        threading.Thread(target=work, daemon=True).start()
        return {}

    # --- pulskurvan ---
    def pulse_series(self) -> Dict[str, Any]:
        with self.lock:
            hr = self.hr
            if hr is None:
                return {}
            step = max(1.0, hr.duration / 1500)
            vals = hr.resample(hr.start, hr.end, step)
            return {"id": self.hr_id, "start": hr.start, "step": step,
                    "values": [None if v is None else round(v, 1) for v in vals],
                    "suggestion": hr.suggest_start()}


def fmt_estimate(seconds: Optional[float]) -> str:
    if seconds is None:
        return "beräknar tid…"
    if seconds < 60:
        return "under 1 minut"
    m = int(round(seconds / 60))
    if m < 60:
        return f"ca {m} min"
    return f"ca {m // 60} tim {m % 60} min"


# ---------------------------------------------------------------------------
# Webbservern
# ---------------------------------------------------------------------------


ACTIONS = ("pick_headcam", "pick_livelox", "load", "pick_fit", "load_fit", "remove_fit", "mark",
           "set_flip", "set_width", "nudge", "set_quality", "set_output", "pick_output",
           "open_help", "open_output", "preview", "make", "cancel_render", "bench",
           "accept_terms", "decline_terms", "set_under", "set_splits", "set_me", "forget_me",
           "set_shown", "preset_shown", "set_consent")
# Det enda som går att göra innan villkoren är godkända (Avsluta sköts av /api/bye).
BEFORE_TERMS = ("accept_terms", "decline_terms", "open_help")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: State) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state
        self.token = secrets.token_urlsafe(24)
        self.last_contact = 0.0
        self.closed = False  # sidan har sagt att fönstret stängs
        self.play_lock = threading.Lock()
        self.players: Dict[str, subprocess.Popen] = {}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?k={self.token}"


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # inga loggar i (det osynliga) konsolfönstret
        pass

    # --- skydd: bara sidan som programmet själv öppnat får prata med servern ---
    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") == f"127.0.0.1:{self.server.server_address[1]}"

    def _token_ok(self, query: Dict[str, List[str]]) -> bool:
        tok = self.headers.get("X-OLVM-Token") or (query.get("k") or [""])[0]
        return secrets.compare_digest(tok, self.server.token)

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"", "text/plain")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            if not self._token_ok(q):
                return self._send(403, "Öppna programmet med OL Video Maker.exe".encode("utf-8"),
                                  "text/plain; charset=utf-8")
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        if not self._token_ok(q):
            return self._send(403, b"", "text/plain")
        self.server.last_contact = vila.awake_time()
        if u.path == "/api/state":
            self.server.closed = False  # sidan lever (t.ex. laddades om)
            return self._json(self.server.state.snapshot())
        if u.path == "/api/pulse":
            return self._json(self.server.state.pulse_series())
        if u.path == "/api/placing_svg":
            try:
                t = float(q.get("t", ["0"])[0])
                lw = int(float(q.get("lw", [str(DEFAULT_LIVELOX_WIDTH)])[0]))
            except ValueError:
                return self._send(400, b"", "text/plain")
            return self._json(self.server.state.placing_svg(t, lw))
        if u.path == "/frame":
            return self._frame(q)
        if u.path == "/play":
            return self._play(q)
        return self._send(404, b"", "text/plain")

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._send(403, b"", "text/plain")
        u = urlparse(self.path)
        if not self._token_ok(parse_qs(u.query)) or not u.path.startswith("/api/"):
            return self._send(403, b"", "text/plain")
        self.server.last_contact = vila.awake_time()
        name = u.path[len("/api/"):]
        if name == "bye":
            self.server.closed = True
            return self._send(204, b"", "text/plain")
        if name not in ACTIONS:
            return self._send(404, b"", "text/plain")
        if not self.server.state.terms_accepted and name not in BEFORE_TERMS:
            return self._json({"error": {"title": "Godkänn villkoren först",
                                         "text": "Läs villkoren och godkänn dem för att "
                                                 "använda programmet."},
                               "state": self.server.state.snapshot()}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            result = getattr(self.server.state, name)(body) or {}
        except VideoMakerError as e:
            result = {"error": {"title": "Något gick fel", "text": str(e)}}
        except Exception as e:
            result = {"error": {"title": "Något gick fel", "text": f"Oväntat fel: {e}"}}
        result["state"] = self.server.state.snapshot()
        self._json(result)

    def _static(self, rel: str) -> None:
        path = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not path.startswith(WEB_DIR + os.sep) or not os.path.isfile(path):
            return self._send(404, b"", "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _source(self, q):
        st = self.server.state
        src = (q.get("src") or ["hc"])[0]
        tl = st.hc_tl if src == "hc" else st.ll_tl
        if tl is None:
            return None
        t = float((q.get("t") or ["0"])[0])
        w = int(float((q.get("w") or ["640"])[0]))
        h = int(float((q.get("h") or ["360"])[0]))
        w = max(32, min(MAX_FRAME[0], w))
        h = max(18, min(MAX_FRAME[1], h))
        clip, off = tl.locate(t)
        return src, clip, off, w, h, src == "hc" and st.hc_flip

    def _frame(self, q) -> None:
        s = self._source(q)
        if s is None:
            return self._send(404, b"", "text/plain")
        src, clip, off, w, h, flip = s
        try:
            data = grab_frame(clip.path, off, w, h, flip=flip, jpeg=True)
        except VideoMakerError as e:
            return self._send(500, str(e).encode("utf-8"), "text/plain; charset=utf-8")
        if not data:
            return self._send(404, b"", "text/plain")
        self._send(200, data, "image/jpeg")

    def _play(self, q) -> None:
        """Spela upp några sekunder som MJPEG-ström (visas direkt i en <img>)."""
        s = self._source(q)
        if s is None:
            return self._send(404, b"", "text/plain")
        src, clip, off, w, h, flip = s
        seconds = min(10.0, max(0.1, float((q.get("s") or ["3"])[0])))
        seconds = min(seconds, max(0.1, clip.duration - off))
        cmd = mjpeg_command(clip.path, off, seconds, 15, w, h, flip=flip)
        kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, **kw)
        except OSError:
            return self._send(500, b"", "text/plain")
        with self.server.play_lock:
            old = self.server.players.get(src)
            self.server.players[src] = proc
        if old is not None and old.poll() is None:
            old.kill()
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=ffmpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                chunk = proc.stdout.read1(65536)  # type: ignore[union-attr]
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass  # sidan slutade lyssna
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Appfönstret
# ---------------------------------------------------------------------------


def find_browser() -> Optional[str]:
    """Microsoft Edge (finns i Windows 10/11), annars Chrome eller Chromium."""
    if os.name == "nt":
        roots = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        rels = [os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join("Google", "Chrome", "Application", "chrome.exe")]
        for rel in rels:
            for root in roots:
                if root and os.path.isfile(os.path.join(root, rel)):
                    return os.path.join(root, rel)
        return None
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _profile_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "OL Video Maker", "fonster")


def open_window(url: str) -> Optional[subprocess.Popen]:
    """Öppna sidan i ett eget appfönster (utan adressfält och flikar)."""
    browser = find_browser()
    if browser:
        args = [browser, f"--app={url}", f"--user-data-dir={_profile_dir()}",
                "--no-first-run", "--no-default-browser-check", "--disable-sync",
                "--disable-extensions", "--disable-features=Translate,msEdgeSidebarV2",
                "--window-size=1280,860", "--lang=sv",
                # Fortsätt visa förloppet även när fönstret är minimerat eller dolt.
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows"]
        try:
            return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            pass
    webbrowser.open(url)
    return None


def main() -> None:
    if os.name == "nt":
        _dpi_aware()  # skarpa filväljare
    _remove_old_previews()
    dialogs = Dialogs()
    state = State(dialogs)
    state.check_tools()
    server = Server(state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if os.environ.get("OLVM_NO_WINDOW"):  # för test: skriv adressen och vänta
        print(server.url, flush=True)
        proc = None
    else:
        proc = open_window(server.url)
    # Klockan står still medan datorn sover, så en lång vila räknas inte som att fönstret
    # har varit tyst (då avbröts videon förut när datorn vaknade).
    started = vila.awake_time()
    try:
        while True:
            dialogs.run_pending(0.3)
            now = vila.awake_time()
            alive = proc is not None and proc.poll() is None
            if state.quit_requested:
                time.sleep(0.5)  # låt sidan få svaret och försöka stänga fönstret själv
                break  # "Avsluta" i villkoren
            if server.closed and not alive and now - server.last_contact > 2.0:
                break  # fönstret stängdes (en pågående video avbryts i shutdown)
            if server.last_contact:
                limit = HEARTBEAT_TIMEOUT_RUNNING if state.running else HEARTBEAT_TIMEOUT
                if not alive and now - server.last_contact > limit:
                    break  # fönstret är stängt
            elif not alive and now - started > FIRST_CONTACT_TIMEOUT:
                break  # sidan öppnades aldrig
    finally:
        state.shutdown()
        dialogs.close()
        server.shutdown()
        if state.quit_requested and proc is not None and proc.poll() is None:
            proc.terminate()  # fönstret gick inte att stänga från sidan


def selftest() -> str:
    """Kontroll av det paketerade programmet (används i bygget): filväljare, ffmpeg, sidan."""
    import tkinter as tk
    lines = []
    root = tk.Tk()
    root.withdraw()
    lines.append(f"Tk {root.tk.call('info', 'patchlevel')}")
    root.destroy()
    if os.name == "nt":
        from .filedialogs import check
        lines.append(check())
    for name in ("index.html", "app.css", "app.js"):
        if not os.path.isfile(os.path.join(WEB_DIR, name)):
            raise VideoMakerError(f"Sidan saknas: {name}")
    lines.append(f"Sidan: {WEB_DIR}")
    lines.append(f"Webbläsare: {find_browser()}")
    for tool in ("ffmpeg", "ffprobe"):
        lines.append(f"{tool}: {find_tool(tool)}")
    check_tools()
    lines.append("ffmpeg och ffprobe startar")
    lines.append(_pulse_selftest())
    lines.append(_placing_selftest())
    lines.append(f"Guide: {_guide_path()}")
    return "\n".join(lines)


def _pulse_selftest() -> str:
    """Gör en kort video med pulsband med den medföljande ffmpeg (sendcmd, drawtext m.m.)."""
    folder = tempfile.mkdtemp(prefix="olvm_selftest_")
    try:
        ffmpeg = find_tool("ffmpeg")
        clips = []
        for name, color in (("hc.mp4", "red"), ("ll.mp4", "blue")):
            path = os.path.join(folder, name)
            subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c={color}:s=640x360:d=3", "-pix_fmt", "yuv420p", path],
                           check=True, capture_output=True,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
            clips.append(path)
        # JPEG-bilder till fönstret
        if not grab_frame(clips[0], 1.0, 320, 180, jpeg=True):
            raise VideoMakerError("Kunde inte hämta en bild ur filmen")
        t0 = 1795888000.0
        hr = HeartRate([t0 + s for s in range(10)], [90, 120, 150, 160, 165, 170, 168, 166, 160, 150])
        out = os.path.join(folder, "puls.mp4")
        render(Job([clips[0]], clips[1], out, lead_in=0, pulse=PulseTrack(hr, offset=t0)))
        return f"Pulsvideo: {os.path.getsize(out)} byte"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# Påhittade löpare och tider för självtestet.
SELFTEST_SPLITS = (
    "Pl\tName\tTime\tTime behind\t1\t\t2\t\n"
    "\tClub\t\t\t300 m\t\t200 m\t\t100 m\t\n"
    "1\tAnna Exempel\t0:06\t\t0:02\t(1)\t0:02\t(2)\t0:02\t(1)\n"
    "\tTestklubben\t\t\t0:02\t(1)\t0:04\t(1)\t0:06\t(1)\n"
    "2\tBo Prov\t0:08\t+0:02\t0:03\t(2)\t0:01\t(1)\t0:04\t(2)\n"
    "\tTestklubben\t\t\t0:03\t(2)\t0:04\t(1)\t0:08\t(2)\n")


def _placing_selftest() -> str:
    """Gör en kort video med placeringslinjen (ass-filtret, figurerna och sendcmd)."""
    folder = tempfile.mkdtemp(prefix="olvm_selftest_")
    try:
        ffmpeg = find_tool("ffmpeg")
        clips = []
        for name, color in (("hc.mp4", "red"), ("ll.mp4", "blue")):
            path = os.path.join(folder, name)
            subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c={color}:s=640x360:d=3", "-pix_fmt", "yuv420p", path],
                           check=True, capture_output=True,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
            clips.append(path)
        sp = splitmod.parse(SELFTEST_SPLITS)
        track = PlacingTrack(sp, [1], 0, True, race_start=0.0)
        out = os.path.join(folder, "placering.mp4")
        render(Job([clips[0]], clips[1], out, lead_in=0, placing=track))
        return f"Placeringsvideo: {os.path.getsize(out)} byte"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _error_log_path() -> str:
    if is_packaged():
        return os.path.join(tempfile.gettempdir(), "ol_video_maker_fel.txt")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fel.txt")


def show_startup_error(exc: BaseException) -> None:
    """Visa fel vid start. Utan konsolfönster syns annars ingenting alls."""
    import traceback
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_path = _error_log_path()
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(details)
    except OSError:
        log_path = ""
    msg = "OL Video Maker kunde inte starta.\n\n" + str(exc)
    if log_path:
        msg += f"\n\nDetaljer finns i filen:\n{log_path}"
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("OL Video Maker", msg)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
