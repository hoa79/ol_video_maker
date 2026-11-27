"""Starta programmet.

Utan argument öppnas fönstret. Med argument körs kommandoradsläget, t.ex.:
    python -m ol_video_maker --headcam GH010123.MP4 --livelox livelox.mp4 \
        --headcam-sync 02:15 --livelox-sync 00:04 --out video.mp4
"""
import argparse
import os
import sys

from .core import (DEFAULT_LIVELOX_WIDTH, Job, VideoMakerError, parse_time, render,
                   sort_headcam_files)


def cli(argv):
    p = argparse.ArgumentParser(prog="ol_video_maker", description="Headcam + Livelox till YouTube-video")
    p.add_argument("--headcam", nargs="+", required=True, help="En eller flera headcam-filer i ordning")
    p.add_argument("--livelox", required=True, help="Livelox-inspelningen")
    p.add_argument("--out", required=True, help="Färdig video (.mp4)")
    p.add_argument("--headcam-sync", default="0", help="Synkögonblick i headcam (mm:ss)")
    p.add_argument("--livelox-sync", default="0", help="Samma ögonblick i Livelox (mm:ss)")
    p.add_argument("--lead-in", type=float, default=5.0, help="Sekunder före synkögonblicket")
    p.add_argument("--end", default=None, help="Sluttid i headcam (mm:ss)")
    p.add_argument("--livelox-width", type=int, default=DEFAULT_LIVELOX_WIDTH)
    p.add_argument("--encoder", default=None, help="t.ex. libx264, libopenh264, h264_mf")
    p.add_argument("--flip", action="store_true", help="Vänd headcam-bilden 180°")
    p.add_argument("--4k", dest="uhd", action="store_true", help="Skapa i 4K (3840x2160)")
    a = p.parse_args(argv)
    try:
        headcam, mixed = sort_headcam_files(a.headcam)  # samma ordning som i fönstret
        if mixed:
            print("Varning: filerna verkar komma från olika inspelningar. Ordning: "
                  + ", ".join(os.path.basename(f) for f in headcam), file=sys.stderr)
        job = Job(headcam_files=headcam, livelox_file=a.livelox, output_file=a.out,
                  headcam_sync=parse_time(a.headcam_sync), livelox_sync=parse_time(a.livelox_sync),
                  lead_in=a.lead_in, end_time=parse_time(a.end) if a.end else None,
                  livelox_width=a.livelox_width, encoder=a.encoder,
                  headcam_flip=a.flip, scale=2 if a.uhd else 1)
        last = [-1]

        def prog(x):
            pct = int(x * 100)
            if pct != last[0]:
                last[0] = pct
                print(f"\r{pct:3d} %", end="", flush=True)

        path = render(job, progress=prog)
        print(f"\nKlart: {path}")
        return 0
    except VideoMakerError as e:
        print(f"\nFel: {e}", file=sys.stderr)
        return 1


def _selftest():
    """Dold kontroll för bygget: "OL Video Maker.exe --selftest". Utan konsolfönster finns ingen
    stdout, så resultatet skrivs till en fil i temp-mappen och syns i returkoden."""
    import os
    import tempfile
    import traceback
    path = os.path.join(tempfile.gettempdir(), "ol_video_maker_selftest.txt")
    try:
        from .webapp import selftest
        text, code = "OK\n" + selftest(), 0
    except Exception as e:
        text, code = "FEL\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__)), 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return code


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(_selftest())
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    try:
        from .webapp import main as gui_main
        gui_main()
    except Exception as e:  # pythonw visar inga fel, så skriv fel.txt och visa en ruta
        _startup_error(e)


def _startup_error(e):
    import os
    import traceback
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        from .webapp import show_startup_error
        show_startup_error(e)
        return
    except Exception:
        pass
    # tkinter saknas: skriv bara fel.txt
    try:
        with open(os.path.join(root, "fel.txt"), "w", encoding="utf-8") as f:
            f.write("OL Video Maker kunde inte starta.\n\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    except OSError:
        pass


if __name__ == "__main__":
    main()
