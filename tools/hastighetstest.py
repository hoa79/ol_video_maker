"""Mät hur snabbt den här datorn skapar video (för utvecklare).

Gör en kort bit av en riktig film och skriver ut hur lång tid det tog, vilken kodare som
användes och hur många gånger snabbare än realtid det gick.

    python tools/hastighetstest.py HEADCAM.mp4 [HEADCAM2.mp4 …] --livelox LIVELOX.mp4
                                   [--start 600] [--sekunder 60] [--fit PULS.fit]
                                   [--4k] [--bara-processor]

Med flera headcam-filer räknas --start från början av den första.
"""
import argparse
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ol_video_maker.core import Job, render  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("headcam", nargs="+")
    p.add_argument("--livelox", required=True)
    p.add_argument("--fit", help="pulsfil från klockan: ritar pulsbandet som i riktiga videon")
    p.add_argument("--start", type=float, default=600.0, help="sekund i headcam-filmen")
    p.add_argument("--sekunder", type=float, default=60.0)
    p.add_argument("--4k", dest="fyrak", action="store_true")
    p.add_argument("--bara-processor", action="store_true",
                   help="använd inte grafikkortet (som tidigare versioner)")
    a = p.parse_args()
    if a.bara_processor:
        os.environ["OLVM_NO_HW"] = "1"
    out = os.path.join(tempfile.gettempdir(), "olvm_hastighetstest.mp4")
    pulse = None
    if a.fit:
        from ol_video_maker.core import probe
        from ol_video_maker.fitfile import load_fit
        from ol_video_maker.pulse_video import PulseTrack
        hr = load_fit(a.fit)
        total = sum(probe(f).duration for f in a.headcam)
        # Klockans start = headcam-filmens början; kurvan visar hela filmen.
        pulse = PulseTrack(hr, offset=hr.start, graph_start=0.0, graph_end=total)
    job = Job(list(a.headcam), a.livelox, out, pulse=pulse, headcam_sync=a.start, livelox_sync=0, lead_in=0,
              end_time=a.start + a.sekunder, scale=2 if a.fyrak else 1)
    logs = []
    t = time.monotonic()
    render(job, log=logs.append)
    secs = time.monotonic() - t
    print(next((l for l in logs if l.startswith("Kodare")), ""))
    print(f"{a.sekunder:.0f} s video tog {secs:.1f} s ({a.sekunder / secs:.2f} x realtid)")
    os.remove(out)


if __name__ == "__main__":
    main()
