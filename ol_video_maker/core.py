"""Kärnan i OL Video Maker: bygger och kör ffmpeg-kommandot.

Layout (1920x1080):
    +--------------------------------+----------+
    |                                |          |
    |   Headcam (huvudbild)          | Livelox  |
    |   skalad för att passa         | (höger   |
    |   i vänstra delen              |  kolumn) |
    |                                |  Karta:  |
    |                                |  Livelox |
    +--------------------------------+----------+

Headcam och Livelox placeras i två separata rutor bredvid varandra,
så Livelox kan aldrig täcka headcam-bilden. Källtexten "Karta: Livelox"
ritas längst ner i Livelox-kolumnen.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .pulse_video import PulseTrack

OUT_W = 1920
OUT_H = 1080
DEFAULT_LIVELOX_WIDTH = 640
DEFAULT_CREDIT = "Karta: Livelox"

# Ordning vi provar H.264-kodare i. libx264 ger bäst kvalitet men är GPL;
# libopenh264 (BSD) och h264_mf (Windows inbyggd) är alternativ.
ENCODER_PREFERENCE = ("libx264", "libopenh264", "h264_mf")
# Grafikkortets egna H.264-kodare (NVIDIA, Intel, AMD). De ingår i ffmpeg och använder
# drivrutinen som redan finns i datorn. Varje kodare provas med en kort testkodning innan
# den används; fungerar ingen används processorn (ENCODER_PREFERENCE) som förut.
HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
# Hårdvaruavkodning av headcam-filmen (4K HEVC är det tyngsta steget för processorn).
if os.name == "nt":
    HWACCEL_CANDIDATES: Tuple[str, ...] = ("d3d11va", "dxva2")
elif sys.platform == "darwin":
    HWACCEL_CANDIDATES = ("videotoolbox",)
else:
    HWACCEL_CANDIDATES = ()
# Sätt OLVM_NO_HW=1 för att bara använda processorn (felsökning).

# Typsnitt för källtexten. Windows först, sedan vanliga Linux/macOS-sökvägar.
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
# Fet stil för pulssiffran.
BOLD_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

CREATE_NO_WINDOW = 0x08000000


class VideoMakerError(Exception):
    """Fel som ska visas för användaren på begriplig svenska."""


# ---------------------------------------------------------------------------
# Tider
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*$")


def parse_time(text: str) -> float:
    """Tolka "1:02:03", "12:34", "75" eller "12:34,5" till sekunder."""
    if text is None or str(text).strip() == "":
        raise VideoMakerError("Tiden är tom. Skriv till exempel 02:15.")
    m = _TIME_RE.match(str(text))
    if not m:
        raise VideoMakerError(
            f"Kunde inte förstå tiden \"{text}\". Skriv minuter:sekunder, till exempel 02:15."
        )
    a, b, c = m.groups()
    secs = float(c.replace(",", "."))
    if a is not None and secs >= 60:
        raise VideoMakerError(f"Sekunderna i \"{text}\" måste vara mindre än 60.")
    if a is not None and b is not None:  # h:m:s
        if int(b) >= 60:
            raise VideoMakerError(f"Minuterna i \"{text}\" måste vara mindre än 60.")
        return int(a) * 3600 + int(b) * 60 + secs
    if a is not None:  # m:s
        return int(a) * 60 + secs
    return secs


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m:02d}:{s:04.1f}"


# ---------------------------------------------------------------------------
# Filer
# ---------------------------------------------------------------------------

# GoPro-namn: GH010123.MP4 (kapitel 01, inspelning 0123), GX/GS/GL likadant,
# äldre kameror: GOPR0123.MP4 (första kapitlet) och GP010123.MP4.
_GOPRO_RE = re.compile(r"^G(?:OPR|[A-Z](\d{2}))(\d{4})\.", re.IGNORECASE)


def gopro_key(path: str) -> Optional[Tuple[str, int]]:
    """(inspelningsnummer, kapitel) för ett GoPro-filnamn, annars None."""
    m = _GOPRO_RE.match(os.path.basename(path))
    if not m:
        return None
    chapter = int(m.group(1)) if m.group(1) else 0
    return m.group(2), chapter


def sort_headcam_files(paths: Sequence[str]) -> Tuple[List[str], bool]:
    """Sortera headcam-filer i rätt ordning.

    GoPro-filer sorteras på inspelningsnummer och sedan kapitel. Returnerar
    (sorterad lista, True om filerna verkar komma från flera inspelningar).
    """
    keys = [gopro_key(p) for p in paths]
    if paths and all(k is not None for k in keys):
        pairs = sorted(zip(keys, paths), key=lambda kp: (kp[0][0], kp[0][1]))
        recordings = {k[0] for k in keys}  # type: ignore[index]
        return [p for _, p in pairs], len(recordings) > 1
    return sorted(paths, key=lambda p: os.path.basename(p).upper()), False


def ensure_mp4(path: str) -> str:
    """Lägg till .mp4 om användaren skrivit ett filnamn utan ändelse."""
    path = path.strip()
    if path and os.path.splitext(path)[1].lower() != ".mp4":
        path += ".mp4"
    return path


def same_file(a: str, b: str) -> bool:
    def norm(p: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(p)))
    return norm(a) == norm(b)


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe
# ---------------------------------------------------------------------------


def is_packaged() -> bool:
    """Sant i Windows-paketet (PyInstaller, eller den portabla versionen med signerad Python)."""
    return bool(getattr(sys, "frozen", False) or getattr(sys, "olvm_portable", False))


def _tool_dirs() -> List[str]:
    """Mappar där vi letar efter ffmpeg utöver PATH (t.ex. en medföljande ffmpeg-mapp)."""
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [os.path.dirname(here)]
    if is_packaged():  # paketerad version: ffmpeg ligger bredvid programfilen
        roots.insert(0, os.path.dirname(os.path.abspath(sys.executable)))
    dirs = []
    for root in roots:
        dirs += [os.path.join(root, "ffmpeg", "bin"), os.path.join(root, "ffmpeg")]
    return dirs


def find_tool(name: str) -> str:
    for d in _tool_dirs():
        for candidate in (os.path.join(d, name + ".exe"), os.path.join(d, name)):
            if os.path.isfile(candidate):
                return candidate
    found = shutil.which(name)
    if not found:
        raise VideoMakerError(
            f"Hittar inte programmet {name}. Installera ffmpeg enligt guiden (Del A) "
            "och starta om datorn."
        )
    return found


BLOCKED_MSG = (
    "Windows spärrade programmet {name} (Smart appkontroll eller ett annat skydd), så "
    "OL Video Maker kan inte läsa eller skapa video. Läs avsnittet \"Om Windows spärrar "
    "ffmpeg\" i guiden (Hjälp och guide)."
)


def tool_start_error(cmd: Sequence[str], err: OSError) -> VideoMakerError:
    """Gör om ett fel när ffmpeg/ffprobe skulle startas till ett begripligt fel på svenska."""
    name = os.path.basename(str(cmd[0])) if cmd else "ffmpeg"
    # 4551: "An Application Control policy has blocked this file" (Smart appkontroll/WDAC),
    # 1260: blockerad av grupprincip, 225: virus/oönskat program.
    if getattr(err, "winerror", None) in (4551, 1260, 225) or isinstance(err, PermissionError):
        return VideoMakerError(BLOCKED_MSG.format(name=name))
    return VideoMakerError(f"Kunde inte starta {name}: {err}")


def _run_hidden(cmd: Sequence[str], **kw) -> subprocess.CompletedProcess:
    if os.name == "nt":  # dölj svarta konsolfönster i Windows
        kw.setdefault("creationflags", CREATE_NO_WINDOW)
    try:
        return subprocess.run(cmd, **kw)
    except OSError as e:
        raise tool_start_error(cmd, e) from e


def check_tools() -> None:
    """Kontrollera att ffmpeg och ffprobe finns och faktiskt går att starta."""
    for tool in ("ffmpeg", "ffprobe"):
        res = _run_hidden([find_tool(tool), "-hide_banner", "-version"], capture_output=True,
                          timeout=30)
        if res.returncode != 0:
            raise VideoMakerError(f"{tool} startade men fungerade inte (felkod {res.returncode}).")


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    has_audio: bool
    fps: str = "30"  # bildfrekvens som ffmpeg-bråk, t.ex. "60000/1001"
    codec: str = ""  # t.ex. "hevc" eller "h264"


def _to_float(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def probe(path: str) -> MediaInfo:
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise VideoMakerError(f"Filen finns inte: {path}")
    cmd = [
        find_tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    res = _run_hidden(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise VideoMakerError(f"Kunde inte läsa filen {name}. Är det en video?")
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError:
        raise VideoMakerError(f"Kunde inte läsa filen {name}. Är det en video?")
    streams = data.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise VideoMakerError(f"Filen {name} innehåller ingen video.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = _to_float((data.get("format") or {}).get("duration")) or _to_float(video.get("duration"))
    if dur is None:
        raise VideoMakerError(
            f"Kunde inte läsa hur lång filen {name} är. Filen kan vara skadad eller ofullständig."
        )
    try:
        w, h = int(video.get("width", 0)), int(video.get("height", 0))
        # Rotering: byt plats på bredd och höjd om videon är roterad 90°.
        rot = 0
        for sd in video.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        rot = int(float((video.get("tags") or {}).get("rotate", rot)))
    except (TypeError, ValueError):
        raise VideoMakerError(f"Kunde inte läsa bildstorleken i filen {name}.")
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = "30"
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = str(video.get(key) or "")
        m = re.match(r"^(\d+)/(\d+)$", val)
        if m and int(m.group(1)) > 0 and int(m.group(2)) > 0 and 1 <= int(m.group(1)) / int(m.group(2)) <= 240:
            fps = val
            break
    return MediaInfo(path=path, duration=dur, width=w, height=h, has_audio=has_audio, fps=fps,
                     codec=str(video.get("codec_name") or ""))


def available_encoders() -> List[str]:
    res = _run_hidden([find_tool("ffmpeg"), "-hide_banner", "-encoders"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
    names = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V") and parts[1] != "=":
            names.append(parts[1])
    return names


def hardware_allowed() -> bool:
    return os.environ.get("OLVM_NO_HW", "").strip() not in ("1", "true", "ja")


# Resultat av testkodningar/-avkodningar, så att de bara görs en gång per programstart.
_hw_cache: dict = {}


def _encoder_works(encoder: str) -> bool:
    """Gör en kort testkodning (en sekund svart 4K-bild) med samma inställningar som videon."""
    key = ("enc", find_tool("ffmpeg"), encoder)
    if key not in _hw_cache:
        cmd = [find_tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin",
               "-f", "lavfi", "-i", "color=c=black:s=3840x2160:r=30:d=0.3",
               *encoder_args(encoder, 2), "-f", "null", "-"]
        try:
            res = _run_hidden(cmd, capture_output=True, timeout=30)
            _hw_cache[key] = res.returncode == 0
        except (subprocess.TimeoutExpired, VideoMakerError):
            _hw_cache[key] = False
    return _hw_cache[key]


def choose_encoder(preferred: Optional[str] = None, hardware: bool = True) -> str:
    """Välj H.264-kodare: grafikkortets om det fungerar (snabbast), annars processorns."""
    encs = available_encoders()
    if preferred:
        if preferred not in encs:
            raise VideoMakerError(f"Din ffmpeg saknar kodaren {preferred}.")
        return preferred
    if hardware and hardware_allowed():
        for e in HW_ENCODERS:
            if e in encs and _encoder_works(e):
                return e
    for e in ENCODER_PREFERENCE:
        if e in encs:
            return e
    raise VideoMakerError("Din ffmpeg saknar H.264-kodare. Installera ffmpeg enligt guiden.")


def is_hardware_encoder(encoder: str) -> bool:
    return encoder in HW_ENCODERS


def choose_hwaccel(info: "MediaInfo") -> Optional[str]:
    """Hårdvaruavkodning som fungerar för den här filen, eller None (processorn avkodar).

    Provas med en kort avkodning av filens början, så att en drivrutin som inte klarar
    formatet (t.ex. äldre grafik och HEVC) aldrig används i den riktiga körningen.
    """
    if not hardware_allowed():
        return None
    for accel in HWACCEL_CANDIDATES:
        key = ("dec", find_tool("ffmpeg"), accel, info.codec, info.width, info.height)
        if key not in _hw_cache:
            cmd = [find_tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin",
                   "-hwaccel", accel, "-t", "0.5", "-i", info.path, "-map", "0:v:0",
                   "-f", "null", "-"]
            try:
                res = _run_hidden(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=30)
                # Om grafikkortet inte klarar filen skriver ffmpeg "Failed setup …" och avkodar
                # med processorn i stället; då vinner vi inget och använder inte hårdvaran.
                err = (res.stderr or "").lower()
                _hw_cache[key] = res.returncode == 0 and "fail" not in err and "hwaccel" not in err
            except (subprocess.TimeoutExpired, VideoMakerError):
                _hw_cache[key] = False
        if _hw_cache[key]:
            return accel
    return None


# Bithastighet för kodare utan CRF (grafikkortets kodare m.fl.). YouTube rekommenderar
# 8-12 Mbit/s för 1080p och 35-45 Mbit/s för 4K; vi ligger i överkant.
def _bitrate_args(scale: int) -> List[str]:
    if scale == 1:
        return ["-b:v", "12M", "-maxrate", "20M", "-bufsize", "24M"]
    return ["-b:v", "40M", "-maxrate", "60M", "-bufsize", "80M"]


def encoder_args(encoder: str, scale: int = 1) -> List[str]:
    if encoder == "libx264":
        # "veryfast": klart snabbare än "faster" och skillnaden syns inte efter YouTubes
        # omkodning (CRF 20 håller kvaliteten, filen blir bara lite större).
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high",
                "-pix_fmt", "yuv420p"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21",
                *_bitrate_args(scale), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", *_bitrate_args(scale),
                "-profile:v", "high", "-pix_fmt", "nv12"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "vbr_peak",
                *_bitrate_args(scale), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    # Övriga kodare utan CRF: en fast bithastighet som räcker gott för YouTube.
    return ["-c:v", encoder, "-b:v", "12M" if scale == 1 else "40M", "-pix_fmt", "yuv420p"]


def has_filter(name: str) -> bool:
    res = _run_hidden([find_tool("ffmpeg"), "-hide_banner", "-filters"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
    return any(len(p) >= 2 and p[1] == name for p in (l.split() for l in res.stdout.splitlines()))


def find_font(bold: bool = False) -> Optional[str]:
    for f in (BOLD_FONT_CANDIDATES if bold else ()) + FONT_CANDIDATES:
        if os.path.isfile(f):
            return f
    return None


def _escape_filter_value(value: str) -> str:
    """Skydda ett värde så att det går att använda inom '...' i en ffmpeg-filtergraf."""
    v = value.replace("\\", "/") if os.name == "nt" else value
    v = v.replace("\\", "\\\\").replace("'", "'\\\\\\''").replace(":", "\\:")
    return v


# ---------------------------------------------------------------------------
# Projektinställningar och kommandobygge
# ---------------------------------------------------------------------------


@dataclass
class Job:
    headcam_files: List[str]          # en eller flera GoPro-filer i ordning
    livelox_file: str
    output_file: str
    headcam_sync: float = 0.0         # tid i headcam när "synkögonblicket" sker (t.ex. starten)
    livelox_sync: float = 0.0         # samma ögonblick i Livelox-inspelningen
    lead_in: float = 5.0              # sekunder att ta med före synkögonblicket
    end_time: Optional[float] = None  # tid i headcam där videon ska sluta (None = till slutet)
    tail_seconds: Optional[float] = None  # bara de sista N sekunderna (provvideo vid slutet)
    livelox_width: int = DEFAULT_LIVELOX_WIDTH
    encoder: Optional[str] = None
    credit_text: Optional[str] = DEFAULT_CREDIT  # None eller "" = ingen text
    headcam_flip: bool = False        # vänd headcam-bilden 180° (kameran satt upp och ner)
    scale: int = 1                    # 1 = Full HD (1920x1080), 2 = 4K (3840x2160)
    pulse: Optional["PulseTrack"] = None  # puls från klockan (frivilligt), se pulse_video.py


@dataclass
class Plan:
    cmd: List[str]
    duration: float
    encoder: str
    hwaccel: Optional[str] = None
    temp_files: List[str] = field(default_factory=list)
    credit_drawn: bool = False
    pulse_drawn: bool = False


def _even(x: float) -> int:
    return int(x) // 2 * 2


def build_plan(job: Job, headcam_infos: Sequence[MediaInfo], livelox_info: MediaInfo,
               encoder: str, font: Optional[str] = None, bold_font: Optional[str] = None,
               tmpdir: Optional[str] = None, hwaccel: Optional[str] = None) -> Plan:
    if not headcam_infos:
        raise VideoMakerError("Välj minst en headcam-fil.")
    total_hc = sum(i.duration for i in headcam_infos)

    if not (0 <= job.headcam_sync <= total_hc):
        raise VideoMakerError(
            f"Synktiden i headcam ({format_time(job.headcam_sync)}) ligger utanför videon "
            f"(som är {format_time(total_hc)} lång)."
        )
    if not (0 <= job.livelox_sync <= livelox_info.duration):
        raise VideoMakerError(
            f"Synktiden i Livelox ({format_time(job.livelox_sync)}) ligger utanför inspelningen "
            f"(som är {format_time(livelox_info.duration)} lång)."
        )
    if not (160 <= job.livelox_width <= OUT_W - 640):
        raise VideoMakerError("Livelox-kolumnens bredd måste vara mellan 160 och 1280 pixlar.")
    if job.scale not in (1, 2):
        raise VideoMakerError("Upplösningen måste vara Full HD eller 4K.")
    # Full HD (1920x1080) eller 4K (3840x2160): allt mäts i Full HD-pixlar och skalas upp.
    k = job.scale
    W, H = OUT_W * k, OUT_H * k
    lw = _even(job.livelox_width * k)

    start = max(0.0, job.headcam_sync - max(0.0, job.lead_in))
    end = total_hc if job.end_time is None else min(job.end_time, total_hc)
    if job.tail_seconds:
        start = max(start, end - job.tail_seconds)
    if end <= start:
        raise VideoMakerError("Sluttiden måste ligga efter starttiden.")
    duration = end - start

    # Livelox-tid = headcam-tid - headcam_sync + livelox_sync
    ll_seek = start - job.headcam_sync + job.livelox_sync
    ll_delay = 0.0
    if ll_seek < 0:
        ll_delay, ll_seek = -ll_seek, 0.0
    # Börjar videon efter att Livelox-inspelningen tagit slut: visa sista kartbilden.
    ll_seek = min(ll_seek, max(0.0, livelox_info.duration - 1.0))

    ffmpeg = find_tool("ffmpeg")
    cmd: List[str] = [ffmpeg, "-hide_banner", "-y", "-nostdin"]
    temp_files: List[str] = []

    # Headcam-inputs: bara de kapitelfiler som behövs för [start, end]. Varje fil får en
    # egen -i med -ss, så att ffmpeg kan hoppa direkt till rätt ställe. (concat-demuxern
    # avkodar i stället allt från filens början, vilket tar minuter med 4K-film.)
    segments: List[Tuple[MediaInfo, float]] = []  # (fil, starttid i filen)
    offset = 0.0
    for info in headcam_infos:
        file_end = offset + info.duration
        if file_end > start and offset < end:
            segments.append((info, max(0.0, start - offset)))
        offset = file_end
    if not segments:  # avrundning: ta sista filen
        segments.append((headcam_infos[-1], max(0.0, headcam_infos[-1].duration - duration)))
    for info, local_start in segments:
        if hwaccel:
            # Grafikkortet avkodar 4K-filmen; bilderna kopieras sedan till processorn
            # för skalning och montering (det är billigt jämfört med avkodningen).
            cmd += ["-hwaccel", hwaccel]
        cmd += ["-ss", f"{local_start:.3f}", "-i", info.path]
    n_hc = len(segments)
    ll_idx = n_hc

    # Livelox
    cmd += ["-ss", f"{ll_seek:.3f}", "-i", livelox_info.path]

    # Tyst ljud om headcam saknar ljud
    hc_audio = all(info.has_audio for info, _ in segments)
    if not hc_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    hc_box_w = W - lw
    ll_x = hc_box_w
    # Med puls: headcam-bilden får plats ovanför pulsbandet längst ner, så bandet aldrig
    # täcker filmen. En 16:9-film hamnar på samma ställe som utan puls.
    band = None
    hc_box_h = H
    if job.pulse is not None:
        from .pulse_video import BAND_H, build_band
        hc_box_h = H - BAND_H * k
        if tmpdir is None:
            tmpdir = tempfile.mkdtemp(prefix="olvm_puls_")
            temp_files.append(tmpdir)
        first = ll_idx + 1 + (0 if hc_audio else 1)
        band = build_band(job.pulse, tmpdir, k, hc_box_w, H, start, duration, first,
                          fps=segments[0][0].fps)
        cmd += band.inputs

    chains: List[str] = []
    if n_hc == 1:
        hc_v = "[0:v:0]"
        audio_map = "0:a:0" if hc_audio else f"{ll_idx + 1}:a:0"
    else:
        # concat-filtret kräver samma bildstorlek, SAR, bildfrekvens och ljudformat i alla
        # delar. Normalisera därför varje del till headcam-rutans storlek och första
        # filens bildfrekvens, så att även filer från olika kameror går att foga ihop.
        fps = segments[0][0].fps
        pads = ""
        for i in range(n_hc):
            chains.append(
                f"[{i}:v:0]scale={hc_box_w}:{hc_box_h}:force_original_aspect_ratio=decrease"
                f":force_divisible_by=2,pad={hc_box_w}:{hc_box_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={fps},format=yuv420p[hv{i}]"
            )
            pads += f"[hv{i}]"
            if hc_audio:
                chains.append(
                    f"[{i}:a:0]aresample=48000,"
                    f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[ha{i}]"
                )
                pads += f"[ha{i}]"
        chains.append(f"{pads}concat=n={n_hc}:v=1:a={1 if hc_audio else 0}[hcv]"
                      + ("[hca]" if hc_audio else ""))
        hc_v = "[hcv]"
        audio_map = "[hca]" if hc_audio else f"{ll_idx + 1}:a:0"

    flip = "hflip,vflip," if job.headcam_flip else ""  # 180° = spegla både vågrätt och lodrätt
    hc_chain = (
        f"{hc_v}{flip}scale={hc_box_w}:{hc_box_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"setsar=1,"
        f"pad={W}:{H}:0:'min((oh-ih)/2,{hc_box_h}-ih)':color=black,"
        f"format=yuv420p[base]"
    )
    ll_chain = (
        f"[{ll_idx}:v]scale={lw}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"setsar=1,format=yuv420p"
    )
    if ll_delay > 0:
        ll_chain += f",tpad=start_duration={ll_delay:.3f}:start_mode=add:color=black"
    ll_chain += "[ll]"
    # Livelox centreras i höger kolumn. eof_action=repeat: sista kartbilden står kvar
    # om Livelox-inspelningen tar slut före headcam-videon.
    overlay = f"[base][ll]overlay=x={ll_x}+({lw}-w)/2:y=({H}-h)/2:eof_action=repeat"
    credit_drawn = False
    if job.credit_text and font:
        # Källtexten ritas längst ner i Livelox-kolumnen, aldrig över headcam.
        overlay += (
            f",drawtext=fontfile='{_escape_filter_value(font)}'"
            f":text='{_escape_filter_value(job.credit_text)}'"
            f":expansion=none:fontsize={28 * k}:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw={8 * k}"
            f":x={ll_x}+({lw}-text_w)/2:y=h-text_h-{24 * k}"
        )
        credit_drawn = True
    pulse_drawn = False
    if band is not None:
        b = band
        overlay += (
            f"[pm];[pm]sendcmd=f='{_escape_filter_value(b.cmd_file)}'[pm0];"
            f"[pm0][pband]overlay=x={b.gx}:y={b.gy}[pm1];"
            f"[pm1][phs]overlay@phs=x='{b.x_expr}-{b.heart // 2}':y=-1000[pm2];"
            f"[pm2][phb]overlay@phb=x='{b.x_expr}-{b.heart_big // 2}':y=-1000"
        )
        text_font = bold_font or font
        if text_font:
            band_y = H - BAND_H * k
            ff = _escape_filter_value(text_font)
            overlay += (
                f",drawtext@pbpm=fontfile='{ff}':text='{b.first_bpm}':fontsize={66 * k}"
                f":fontcolor=0xff5a6a:x={30 * k}:y={band_y + 30 * k}"
            )
            if font:
                f2 = _escape_filter_value(font)
                overlay += (
                    f",drawtext=fontfile='{f2}':text='slag/min':expansion=none:fontsize={22 * k}"
                    f":fontcolor=white@0.85:x={32 * k}:y={band_y + 102 * k}"
                )
                if b.max_bpm:
                    overlay += (
                        f",drawtext=fontfile='{f2}':text='max {b.max_bpm}':expansion=none"
                        f":fontsize={18 * k}:fontcolor=0x8a8a8a:x={32 * k}:y={band_y + 134 * k}"
                    )
        chains += band.chains
        pulse_drawn = True
    overlay += ",format=yuv420p[v]"
    filtergraph = ";".join(chains + [hc_chain, ll_chain, overlay])

    cmd += [
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", audio_map,
        "-t", f"{duration:.3f}",
    ]
    cmd += encoder_args(encoder, k)
    cmd += [
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        job.output_file,
    ]
    return Plan(cmd=cmd, duration=duration, encoder=encoder, hwaccel=hwaccel,
                temp_files=temp_files, credit_drawn=credit_drawn, pulse_drawn=pulse_drawn)


# ---------------------------------------------------------------------------
# Felmeddelanden
# ---------------------------------------------------------------------------

_ERROR_HINTS = (
    (("permission denied",),
     "Programmet fick inte skriva eller läsa en fil. Stäng videon i andra program "
     "(till exempel en videospelare) och försök igen."),
    (("no space left",),
     "Disken är full. Frigör utrymme eller spara videon på en annan disk."),
    (("unable to choose an output format", "unable to find a suitable output format"),
     "Filnamnet för den färdiga videon måste sluta på .mp4."),
    (("invalid data found when processing input", "moov atom not found"),
     "En av videofilerna verkar vara skadad eller ofullständig. Kopiera den från kameran igen."),
    (("no such file or directory",),
     "En fil eller mapp hittades inte. Kontrollera att filerna finns kvar och att mappen "
     "du sparar i finns."),
    (("error while opening encoder", "could not open encoder", "cannot load", "no capable devices"),
     "Videokodaren kunde inte starta. Installera ffmpeg enligt guiden (Del A)."),
)


def explain_ffmpeg_error(lines: Sequence[str]) -> str:
    text = "\n".join(lines).lower()
    for needles, hint in _ERROR_HINTS:
        if any(n in text for n in needles):
            return hint
    return "Något gick fel när videon skulle skapas."


def _write_log(cmd: Sequence[str], lines: Sequence[str]) -> Optional[str]:
    try:
        path = os.path.join(tempfile.gettempdir(), "ol_video_maker_logg.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Kommando:\n" + " ".join(cmd) + "\n\nUtskrift från ffmpeg:\n")
            f.write("\n".join(lines) + "\n")
        return path
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Körning
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[float], None]


def render(job: Job, progress: Optional[ProgressCallback] = None,
           log: Optional[Callable[[str], None]] = None,
           cancel_flag: Optional[Callable[[], bool]] = None,
           on_process: Optional[Callable[[subprocess.Popen], None]] = None) -> str:
    """Skapa videon. progress får ett värde 0.0-1.0. Returnerar sökvägen till filen.

    on_process får ffmpeg-processen när den startat, så att anroparen kan avsluta den
    (till exempel när fönstret stängs).
    """
    if not job.headcam_files:
        raise VideoMakerError("Välj minst en headcam-fil (steg 1 i programmet).")
    if not job.livelox_file:
        raise VideoMakerError("Välj Livelox-inspelningen (steg 2 i programmet).")
    if not job.output_file or not job.output_file.strip():
        raise VideoMakerError("Välj var den färdiga videon ska sparas.")
    job.output_file = ensure_mp4(job.output_file)
    for src in list(job.headcam_files) + [job.livelox_file]:
        if same_file(src, job.output_file):
            raise VideoMakerError("Den färdiga videon får inte skriva över en av dina originalfiler.")
    out_dir = os.path.dirname(os.path.abspath(job.output_file))
    if not os.path.isdir(out_dir):
        raise VideoMakerError(f"Mappen finns inte: {out_dir}")

    hc_infos = [probe(p) for p in job.headcam_files]
    ll_info = probe(job.livelox_file)
    font = bold_font = None
    if (job.credit_text or job.pulse is not None) and has_filter("drawtext"):
        font = find_font()
        bold_font = find_font(bold=True)

    # Först med grafikkortet (avkodning och/eller kodning) om det fungerar, annars eller om
    # det går fel: en gång till med bara processorn, precis som tidigare versioner.
    encoder = choose_encoder(job.encoder)
    hwaccel = choose_hwaccel(hc_infos[0])
    attempts = [(encoder, hwaccel)]
    if hwaccel or is_hardware_encoder(encoder):
        attempts.append((choose_encoder(job.encoder, hardware=False), None))

    for n, (enc, accel) in enumerate(attempts):
        last = n == len(attempts) - 1
        plan = build_plan(job, hc_infos, ll_info, enc, font=font, bold_font=bold_font,
                          hwaccel=accel)
        if log:
            log("Kodare: " + enc + (f", avkodning: {accel}" if accel else ""))
            if job.credit_text and not plan.credit_drawn:
                log("Källtexten kunde inte ritas (typsnitt eller drawtext saknas).")
            log("Kommando: " + " ".join(plan.cmd))
        returncode, all_lines, cancelled = _run_plan(plan, progress, cancel_flag, on_process)
        if cancelled:
            _remove(job.output_file)
            raise VideoMakerError("Avbrutet.")
        if returncode == 0:
            break
        _remove(job.output_file)  # ta bort halvfärdig fil
        if not last:
            if log:
                log("Grafikkortet klarade inte videon. Försöker igen med processorn.")
            if progress:
                progress(0.0)
            continue
        log_path = _write_log(plan.cmd, all_lines)
        msg = explain_ffmpeg_error(all_lines)
        details = "\n".join(all_lines[-8:])
        if log_path:
            msg += f"\n\nTekniska detaljer finns i filen:\n{log_path}"
        msg += "\n\nSista raderna från ffmpeg:\n" + details
        raise VideoMakerError(msg)
    if progress:
        progress(1.0)
    return job.output_file


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _run_plan(plan: Plan, progress: Optional[ProgressCallback],
              cancel_flag: Optional[Callable[[], bool]],
              on_process: Optional[Callable[[subprocess.Popen], None]]
              ) -> Tuple[int, List[str], bool]:
    """Kör ffmpeg-kommandot. Returnerar (felkod, ffmpegs sista rader, avbrutet)."""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    all_lines: List[str] = []
    cancelled = False
    try:
        try:
            proc = subprocess.Popen(plan.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", **kw)
        except OSError as e:
            raise tool_start_error(plan.cmd, e) from e
        if on_process:
            on_process(proc)

        def _drain_stderr():
            for line in proc.stderr:  # type: ignore[union-attr]
                all_lines.append(line.rstrip())
                if len(all_lines) > 400:
                    del all_lines[:100]

        def _watch_cancel():
            while proc.poll() is None:
                if cancel_flag and cancel_flag():
                    proc.terminate()
                    return
                time.sleep(0.2)

        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_err.start()
        if cancel_flag:
            threading.Thread(target=_watch_cancel, daemon=True).start()
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if progress and plan.duration > 0:
                    progress(min(1.0, us / 1_000_000 / plan.duration))
        proc.wait()
        t_err.join(timeout=2)
        proc.stdout.close()  # type: ignore[union-attr]
        proc.stderr.close()  # type: ignore[union-attr]
        cancelled = bool(cancel_flag and cancel_flag())
    finally:
        for f in plan.temp_files:
            if os.path.isdir(f):
                shutil.rmtree(f, ignore_errors=True)
                continue
            _remove(f)
    return proc.returncode, all_lines, cancelled
