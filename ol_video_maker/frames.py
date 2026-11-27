"""Hämta enskilda bildrutor ur videor, så att man kan bläddra i dem i fönstret.

Bilderna hämtas med ffmpeg som PPM, ett format som tkinter kan visa utan
extra bibliotek.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence, Tuple

from .core import CREATE_NO_WINDOW, MediaInfo, _run_hidden, find_tool, tool_start_error

DEFAULT_FPS = 30.0


@dataclass
class Clip:
    """En fil i en tidslinje: börjar på `start` sekunder i den sammanfogade filmen."""
    path: str
    start: float
    duration: float
    fps: float


class Timeline:
    """En eller flera filer efter varandra (GoPro delar upp långa lopp i kapitel)."""

    def __init__(self, clips: Sequence[Clip]):
        self.clips = list(clips)

    @classmethod
    def from_infos(cls, infos: Sequence[MediaInfo]) -> "Timeline":
        clips, t = [], 0.0
        for info in infos:
            clips.append(Clip(info.path, t, info.duration, probe_fps(info.path)))
            t += info.duration
        return cls(clips)

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    @property
    def fps(self) -> float:
        return self.clips[0].fps if self.clips else DEFAULT_FPS

    def locate(self, t: float) -> Tuple[Clip, float]:
        """Vilken fil och vilken tid i den filen motsvarar tiden t i hela filmen."""
        if not self.clips:
            raise ValueError("Tom tidslinje")
        t = max(0.0, min(t, self.duration))
        for c in self.clips:
            if t < c.start + c.duration:
                return c, t - c.start
        last = self.clips[-1]
        return last, max(0.0, last.duration - 1.0 / last.fps)


def probe_fps(path: str) -> float:
    cmd = [find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "csv=p=0", path]
    try:
        res = _run_hidden(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=30)
    except subprocess.SubprocessError:
        return DEFAULT_FPS
    for part in (res.stdout or "").replace("\n", ",").split(","):
        part = part.strip()
        if "/" in part:
            num, _, den = part.partition("/")
            try:
                fps = float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                continue
            if 1.0 <= fps <= 240.0:
                return fps
    return DEFAULT_FPS


FLIP_FILTER = "hflip,vflip,"  # vänd bilden 180°


def _scale_filter(max_w: int, max_h: int, flip: bool = False) -> str:
    return ((FLIP_FILTER if flip else "") + f"scale={max_w}:{max_h}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2:flags=fast_bilinear")


def _popen_kw() -> dict:
    return {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}


def grab_frame(path: str, t: float, max_w: int, max_h: int,
               flip: bool = False, jpeg: bool = False) -> Optional[bytes]:
    """En bildruta vid tiden t (sekunder) som PPM-data (eller JPEG), eller None om det inte gick.

    Går ffmpeg inte att starta (t.ex. spärrat av Windows) kastas VideoMakerError.
    """
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-an",
           "-vf", _scale_filter(max_w, max_h, flip), "-f", "image2pipe"]
    cmd += ["-c:v", "mjpeg", "-q:v", "3", "-"] if jpeg else ["-c:v", "ppm", "-"]
    try:
        res = _run_hidden(cmd, capture_output=True, timeout=60)
    except subprocess.SubprocessError:
        return None  # t.ex. tidsgräns; spärrat ffmpeg ger VideoMakerError
    data = res.stdout or b""
    return data if data.startswith(b"\xff\xd8" if jpeg else b"P6") else None


def _read_ppm(stream) -> Optional[bytes]:
    """Läs en PPM-bild ur en ström. Rubriken är "P6\\n<b> <h>\\n255\\n"."""
    tokens: List[bytes] = []
    header = b""
    while len(tokens) < 4:
        ch = stream.read(1)
        if not ch:
            return None
        header += ch
        if ch.isspace():
            continue
        tok = ch
        while True:
            ch = stream.read(1)
            if not ch:
                return None
            header += ch
            if ch.isspace():
                break
            tok += ch
        tokens.append(tok)
    try:
        w, h, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    except ValueError:
        return None
    size = w * h * 3 * (2 if maxval > 255 else 1)
    body = b""
    while len(body) < size:
        chunk = stream.read(size - len(body))
        if not chunk:
            return None
        body += chunk
    return header + body


def play_frames(path: str, t: float, seconds: float, fps: float, max_w: int, max_h: int,
                on_process=None, flip: bool = False) -> Iterator[bytes]:
    """Bildrutor från tiden t och `seconds` sekunder framåt, `fps` bilder per sekund."""
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", f"{max(0.0, t):.3f}", "-i", path, "-t", f"{seconds:.3f}", "-an",
           "-vf", f"fps={fps:g}," + _scale_filter(max_w, max_h, flip),
           "-f", "image2pipe", "-c:v", "ppm", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, **_popen_kw())
    except OSError as e:
        raise tool_start_error(cmd, e) from e
    if on_process:
        on_process(proc)
    try:
        while True:
            frame = _read_ppm(proc.stdout)
            if frame is None:
                break
            yield frame
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()  # type: ignore[union-attr]


def mjpeg_command(path: str, t: float, seconds: float, fps: float, max_w: int, max_h: int,
                  flip: bool = False) -> List[str]:
    """ffmpeg-kommando som spelar upp en bit av filmen i realtid som MJPEG-ström (för fönstret).
    Strömmen har gränsen "ffmpeg" (multipart/x-mixed-replace;boundary=ffmpeg)."""
    return [find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-re",
            "-ss", f"{max(0.0, t):.3f}", "-i", path, "-t", f"{seconds:.3f}", "-an",
            "-vf", f"fps={fps:g}," + _scale_filter(max_w, max_h, flip),
            "-c:v", "mjpeg", "-q:v", "4", "-f", "mpjpeg", "-"]
