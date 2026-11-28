"""Kort video för mobilen: ca 1 minut i stående format (9:16) för TikTok, Instagram och Reels.

Användaren väljer själv bitarna ur loppet (steg 6). Programmet föreslår ett förval: starten,
några kontroller och målgången. Varje bit skapas för sig och bitarna fogas sedan ihop utan
att kodas om. Då behöver ffmpeg bara öppna en headcam-fil i taget (4K-avkodning på
grafikkortet tål inte många filer samtidigt), och förloppet blir jämnt.

Layout (1080 x 1920):
    +-----------------------+  0
    | Kontroll 7 · 23:14    |      rad med bitens namn
    +-----------------------+  160
    |   Headcam (16:9)      |      hela headcam-bilden, inget beskärs
    +-----------------------+  768
    |                       |
    |   Livelox-kartan      |      kartan under, täcker aldrig headcam
    | Karta: Livelox        |
    +-----------------------+  1660
    | ===== förlopp ====    |      appens egen text hamnar här
    +-----------------------+  1920
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple

from .core import (DEFAULT_CREDIT, MediaInfo, Plan, ProgressCallback, VideoMakerError,
                   _escape_filter_value, _remove, _run_plan, _write_log, choose_encoder,
                   choose_hwaccel, encoder_args, ensure_mp4, explain_ffmpeg_error, find_font,
                   find_tool, has_filter, is_hardware_encoder, probe, same_file)

if TYPE_CHECKING:  # pragma: no cover
    from .splits import Splits

W, H = 1080, 1920
TOP_H = 160                 # raden med bitens namn
HC_Y, HC_H = TOP_H, 608     # headcam 16:9 i full bredd
MAP_Y, MAP_H = HC_Y + HC_H, 892
BOTTOM_Y = MAP_Y + MAP_H    # 1660: mörkt band med förloppet (appens text täcker det)
BAR_H = 10
FPS = "30"                  # mobilerna visar 30 bilder/s, och det går snabbt att skapa
CLIP_SECONDS = 8.0          # en bit som användaren lägger till
MAX_SECONDS = 60.0          # Reels, TikTok och Instagram: högst ca 1 minut
MAX_CLIPS = 15
PRIMARY = "0xb0127f"        # samma färg som knapparna i programmet
BEFORE_PUNCH = 6.0          # en kontrollbit börjar så långt före stämplingen
START_BEFORE = 1.0          # startbiten börjar en sekund före starten


@dataclass
class Clip:
    start: float            # tid i headcam-filmen (alla filer efter varandra)
    length: float
    name: str = ""          # "Start", "Kontroll 7", "Målgång" eller "" (en bit du valde)


@dataclass
class ShortJob:
    headcam_files: List[str]
    livelox_file: str
    output_file: str
    clips: List[Clip]
    headcam_sync: float = 0.0
    livelox_sync: float = 0.0
    headcam_flip: bool = False
    credit_text: Optional[str] = DEFAULT_CREDIT
    encoder: Optional[str] = None


def short_path(output: str) -> str:
    """OL-video.mp4 -> OL-video_kort.mp4 (bredvid den vanliga videon)."""
    out = ensure_mp4(output or "OL-video.mp4")
    base, ext = os.path.splitext(out)
    return base + "_kort" + ext


def race_clock(seconds: float) -> str:
    s = int(max(0.0, seconds) + 0.5)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def label(clip: Clip, hc_sync: float) -> str:
    """Texten överst i videon, t.ex. "Kontroll 7 · 23:14"."""
    if clip.name == "Start":
        return "Start"
    t = race_clock(clip.start + clip.length / 2 - hc_sync)
    return f"{clip.name} · {t}" if clip.name else t


def total(clips: Sequence[Clip]) -> float:
    return sum(c.length for c in clips)


# ---------------------------------------------------------------------------
# Förval
# ---------------------------------------------------------------------------


def _fit(start: float, length: float, lo: float, hi: float) -> Optional[Tuple[float, float]]:
    """Flytta biten så att den ligger inom [lo, hi]. None om det inte går."""
    length = min(length, hi - lo)
    if length < 1.0:
        return None
    start = max(lo, min(start, hi - length))
    return start, length


def add_clip(clips: List[Clip], clip: Clip, lo: float, hi: float) -> bool:
    """Lägg till en bit i tidsordning. En bit som överlappar en annan kapas så att samma sak
    inte visas två gånger, och biten kortas om det bara finns lite plats kvar av minuten.
    False om biten inte får plats."""
    room = MAX_SECONDS - total(clips)          # finns det bara lite plats kvar blir biten kortare
    if room < 2.0 or len(clips) >= MAX_CLIPS:
        return False
    f = _fit(clip.start, min(clip.length, room), lo, hi)
    if f is None:
        return False
    start, length = f
    end = start + length
    for c in clips:
        c_end = c.start + c.length
        if start < c_end and end > c.start:   # överlapp: kapa mot grannen
            if start >= c.start:
                start = c_end
            else:
                end = c.start
    if end - start < 2.0:
        return False
    if total(clips) + (end - start) > MAX_SECONDS + 0.01:
        return False
    clips.append(Clip(start, end - start, clip.name))
    clips.sort(key=lambda c: c.start)
    return True


def suggest(hc_sync: float, end: float, duration: float, splits: Optional["Splits"] = None,
            me: Optional[int] = None, n_middle: int = 5) -> List[Clip]:
    """Förvalda bitar: starten, n_middle bitar mitt i loppet och målgången.

    Har användaren klistrat in sträcktider och valt sig själv tas de kontroller där man var
    snabbast och där man tappade mest mot den bästa på sträckan. Kontrolltiderna kommer
    från sträcktiderna (inte från videons eller klockans tid). Annars sprids bitarna jämnt.
    """
    end = min(end, duration)
    clips: List[Clip] = []
    if end - hc_sync < 10:
        add_clip(clips, Clip(max(0.0, hc_sync - START_BEFORE), CLIP_SECONDS, "Start"), 0.0, end)
        return clips
    add_clip(clips, Clip(hc_sync - START_BEFORE, CLIP_SECONDS, "Start"), 0.0, end)
    finish = Clip(end - CLIP_SECONDS, CLIP_SECONDS, "Målgång")
    punches = _punches(splits, me, n_middle) if splits is not None and me is not None else None
    if punches:
        for idx, t in punches:
            add_clip(clips, Clip(hc_sync + t - BEFORE_PUNCH, CLIP_SECONDS, f"Kontroll {idx + 1}"),
                     0.0, finish.start)
    else:
        span = finish.start - (hc_sync + CLIP_SECONDS)
        for i in range(n_middle):
            mid = hc_sync + CLIP_SECONDS + span * (i + 0.5) / n_middle
            add_clip(clips, Clip(mid - CLIP_SECONDS / 2, CLIP_SECONDS, ""), 0.0, finish.start)
    add_clip(clips, finish, 0.0, end)
    return clips


def _punches(sp: "Splits", me: int, n: int) -> List[Tuple[int, float]]:
    """(kontrollens ordningsnummer, tid från start) för de n mest intressanta kontrollerna:
    de sträckor där du tappade mest mot den bästa och de där du var snabbast."""
    r = sp.runners[me]
    n_ctrl = len(sp.controls) - 1          # sista är målet
    legs = []
    for i in range(min(n_ctrl, len(r.splits))):
        t = r.splits[i]
        prev = 0 if i == 0 else r.splits[i - 1]
        if t is None or prev is None:
            continue
        mine = t - prev
        best = None
        for o in sp.runners:
            if len(o.splits) <= i or o.splits[i] is None:
                continue
            op = 0 if i == 0 else o.splits[i - 1]
            if op is None:
                continue
            leg = o.splits[i] - op
            if leg > 0 and (best is None or leg < best):
                best = leg
        if best is None or mine <= 0:
            continue
        legs.append((i, float(t), mine / best))
    if not legs:
        return []
    by_loss = sorted(legs, key=lambda x: -x[2])
    by_fast = sorted(legs, key=lambda x: x[2])
    picked: List[Tuple[int, float, float]] = []
    n_loss = (n + 1) // 2
    for x in by_loss[:n_loss] + by_fast:
        if len(picked) >= n:
            break
        if x not in picked:
            picked.append(x)
    return sorted([(i, t) for i, t, _ in picked], key=lambda p: p[1])


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------


def _pieces(clip: Clip, infos: Sequence[MediaInfo]) -> List[Tuple[MediaInfo, float, float]]:
    """(fil, start i filen, längd) för biten. En bit kan gå över en kapitelgräns."""
    out = []
    offset = 0.0
    end = clip.start + clip.length
    for info in infos:
        f_end = offset + info.duration
        a, b = max(clip.start, offset), min(end, f_end)
        if b - a > 0.05:
            out.append((info, a - offset, b - a))
        offset = f_end
    return out


def build_clip_plan(job: ShortJob, index: int, infos: Sequence[MediaInfo], ll_info: MediaInfo,
                    encoder: str, out: str, font: Optional[str], bold_font: Optional[str],
                    hwaccel: Optional[str], progress_from: float, progress_to: float) -> Plan:
    clip = job.clips[index]
    pieces = _pieces(clip, infos)
    if not pieces:
        raise VideoMakerError(f"Bit {index + 1} ligger utanför filmen.")
    length = sum(p[2] for p in pieces)
    cmd: List[str] = [find_tool("ffmpeg"), "-hide_banner", "-y", "-nostdin"]
    for info, local, dur in pieces:
        if hwaccel:
            cmd += ["-hwaccel", hwaccel]
        cmd += ["-ss", f"{local:.3f}", "-t", f"{dur:.3f}", "-i", info.path]
    n = len(pieces)
    ll_t = clip.start - job.headcam_sync + job.livelox_sync
    ll_delay = 0.0
    if ll_t < 0:
        ll_delay, ll_t = -ll_t, 0.0
    ll_t = min(ll_t, max(0.0, ll_info.duration - 1.0))
    cmd += ["-ss", f"{ll_t:.3f}", "-i", ll_info.path]
    ll_idx = n
    has_audio = all(p[0].has_audio for p in pieces)
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{length:.3f}", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000"]
    bar_idx = ll_idx + (1 if has_audio else 2)
    cmd += ["-f", "lavfi", "-i", f"color=c={PRIMARY}:s={W}x{BAR_H}:r={FPS}"]

    chains: List[str] = []
    norm = (f"fps={FPS},scale={W}:{HC_H}:force_original_aspect_ratio=decrease"
            f":force_divisible_by=2,pad={W}:{HC_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
            f"format=yuv420p")
    afmt = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if n == 1:
        chains.append(f"[0:v:0]{norm}[hcv]")
        if has_audio:
            chains.append(f"[0:a:0]{afmt}[hca]")
    else:
        pads = ""
        for i in range(n):
            chains.append(f"[{i}:v:0]{norm}[hv{i}]")
            pads += f"[hv{i}]"
            if has_audio:
                chains.append(f"[{i}:a:0]{afmt}[ha{i}]")
                pads += f"[ha{i}]"
        chains.append(f"{pads}concat=n={n}:v=1:a={1 if has_audio else 0}[hcv]"
                      + ("[hca]" if has_audio else ""))
    flip = "hflip,vflip," if job.headcam_flip else ""
    chains.append(f"[hcv]{flip}pad={W}:{H}:0:{HC_Y}:color=0x000000,"
                  f"drawbox=x=0:y={BOTTOM_Y}:w={W}:h={H - BOTTOM_Y}:color=0x151515:t=fill[base]")
    ll = (f"[{ll_idx}:v]fps={FPS},scale={W}:{MAP_H}:force_original_aspect_ratio=decrease"
          f":force_divisible_by=2,setsar=1,format=yuv420p")
    if ll_delay > 0:
        ll += f",tpad=start_duration={ll_delay:.3f}:start_mode=add:color=black"
    chains.append(ll + "[ll]")
    v = (f"[base][ll]overlay=x=({W}-w)/2:y={MAP_Y}+({MAP_H}-h)/2:eof_action=repeat[m0];"
         f"[m0]drawbox=x=0:y={BOTTOM_Y}:w={W}:h={BAR_H}:color=0x333333:t=fill[m1];"
         # Förloppet för hela den korta videon: fylls från progress_from till progress_to.
         f"[m1][{bar_idx}:v]overlay=eval=frame:y={BOTTOM_Y}:shortest=1"
         f":x='-{W}+{W}*({progress_from:.5f}+{progress_to - progress_from:.5f}*min(t/{length:.3f},1))'")
    if font:
        ff = _escape_filter_value(bold_font or font)
        text = _escape_filter_value(label(clip, job.headcam_sync))
        v += (f",drawtext=fontfile='{ff}':text='{text}':expansion=none:fontsize=46"
              f":fontcolor=white:box=1:boxcolor={PRIMARY}:boxborderw=16:x=48:y=56")
        if job.credit_text:
            f2 = _escape_filter_value(font)
            v += (f",drawtext=fontfile='{f2}':text='{_escape_filter_value(job.credit_text)}'"
                  f":expansion=none:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.55"
                  f":boxborderw=8:x=32:y={BOTTOM_Y}-text_h-32")
    v += ",format=yuv420p[v]"
    chains.append(v)
    if has_audio:
        fade = min(0.3, length / 4)
        chains.append(f"[hca]afade=t=in:d={fade:.2f},afade=t=out:st={length - fade:.3f}"
                      f":d={fade:.2f}[a]")
        amap = "[a]"
    else:
        amap = f"{ll_idx + 1}:a:0"
    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", amap,
            "-t", f"{length:.3f}"]
    cmd += encoder_args(encoder, 1)
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-progress", "pipe:1", "-nostats", out]
    return Plan(cmd=cmd, duration=length, encoder=encoder, hwaccel=hwaccel,
                credit_drawn=bool(font and job.credit_text))


def build_join_cmd(list_file: str, out: str) -> List[str]:
    return [find_tool("ffmpeg"), "-hide_banner", "-y", "-nostdin", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", "-movflags", "+faststart", out]


def _concat_line(path: str) -> str:
    return "file '" + path.replace("\\", "/").replace("'", "'\\''") + "'\n"


def render_short(job: ShortJob, progress: Optional[ProgressCallback] = None,
                 cancel_flag: Optional[Callable[[], bool]] = None,
                 on_process: Optional[Callable] = None,
                 log: Optional[Callable[[str], None]] = None) -> str:
    """Skapa den korta videon. Returnerar sökvägen till filen."""
    if not job.clips:
        raise VideoMakerError("Välj minst en bit till den korta videon.")
    if total(job.clips) > MAX_SECONDS + 0.5:
        raise VideoMakerError("Den korta videon får vara högst 60 sekunder. Ta bort en bit.")
    job.output_file = ensure_mp4(job.output_file)
    for src in list(job.headcam_files) + [job.livelox_file]:
        if same_file(src, job.output_file):
            raise VideoMakerError("Den korta videon får inte skriva över en av dina originalfiler.")
    out_dir = os.path.dirname(os.path.abspath(job.output_file))
    if not os.path.isdir(out_dir):
        raise VideoMakerError(f"Mappen finns inte: {out_dir}")
    infos = [probe(p) for p in job.headcam_files]
    ll_info = probe(job.livelox_file)
    font = bold_font = None
    if has_filter("drawtext"):
        font, bold_font = find_font(), find_font(bold=True)

    encoder = choose_encoder(job.encoder)
    hwaccel = choose_hwaccel(infos[0])
    attempts = [(encoder, hwaccel)]
    if hwaccel or is_hardware_encoder(encoder):
        attempts.append((choose_encoder(job.encoder, hardware=False), None))

    lengths = [sum(p[2] for p in _pieces(c, infos)) for c in job.clips]
    whole = max(0.1, sum(lengths))
    tmp = tempfile.mkdtemp(prefix="olvm_kort_")
    try:
        for n_try, (enc, accel) in enumerate(attempts):
            last_try = n_try == len(attempts) - 1
            parts: List[str] = []
            failed: Optional[Tuple[Plan, List[str]]] = None
            done = 0.0
            for i in range(len(job.clips)):
                part = os.path.join(tmp, f"bit_{i + 1:02d}.mp4")
                a, b = done / whole, (done + lengths[i]) / whole
                plan = build_clip_plan(job, i, infos, ll_info, enc, part, font, bold_font,
                                       accel, a, b)
                if log:
                    log("Kommando: " + " ".join(plan.cmd))
                cb = None
                if progress:
                    # 95 % till bitarna, sista 5 % till ihopfogningen.
                    cb = (lambda p, a=a, b=b: progress(0.95 * (a + (b - a) * p)))
                code, lines, cancelled = _run_plan(plan, cb, cancel_flag, on_process)
                if cancelled:
                    _remove(job.output_file)
                    raise VideoMakerError("Avbrutet.")
                if code != 0:
                    failed = (plan, lines)
                    break
                parts.append(part)
                done += lengths[i]
            if failed is None:
                break
            if not last_try:
                if progress:
                    progress(0.0)
                continue
            plan, lines = failed
            msg = explain_ffmpeg_error(lines)
            log_path = _write_log(plan.cmd, lines)
            if log_path:
                msg += f"\n\nTekniska detaljer finns i filen:\n{log_path}"
            raise VideoMakerError(msg + "\n\nSista raderna från ffmpeg:\n" + "\n".join(lines[-8:]))
        list_file = os.path.join(tmp, "bitar.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            f.writelines(_concat_line(p) for p in parts)
        join = Plan(cmd=build_join_cmd(list_file, job.output_file), duration=0.0, encoder="copy")
        code, lines, cancelled = _run_plan(join, None, cancel_flag, on_process)
        if cancelled:
            _remove(job.output_file)
            raise VideoMakerError("Avbrutet.")
        if code != 0:
            _remove(job.output_file)
            raise VideoMakerError(explain_ffmpeg_error(lines) + "\n\nSista raderna från ffmpeg:\n"
                                  + "\n".join(lines[-8:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if progress:
        progress(1.0)
    return job.output_file
