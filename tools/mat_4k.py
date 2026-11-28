"""Mät hur snabbt 4K-videon skapas med olika inställningar (för utvecklare).

    python mat_4k.py --repo MAPP HEADCAM1 [HEADCAM2] --livelox LL.mp4 [--fit F.fit]
                     [--start 600] [--sekunder 60] [--band ingen|puls|hojd] [--fps30] [--amf-speed]

Skriver ut: kodare, avkodning, "provbit" (hastighet mätt som i steg 5, 6 s) och "hel"
(hastighet för hela biten inklusive start). Hastighet = sekunder video per sekund.
"""
import argparse, json, os, re, sys, tempfile, time

p = argparse.ArgumentParser()
p.add_argument("headcam", nargs="+")
p.add_argument("--repo", required=True)
p.add_argument("--livelox", required=True)
p.add_argument("--fit")
p.add_argument("--start", type=float, default=600.0)
p.add_argument("--sekunder", type=float, default=60.0)
p.add_argument("--band", default="ingen", choices=["ingen", "puls", "hojd", "placering"])
p.add_argument("--fps30", action="store_true")
p.add_argument("--amf-speed", action="store_true")
p.add_argument("--fullhd", action="store_true")
a = p.parse_args()
sys.path.insert(0, a.repo)
from ol_video_maker import core  # noqa: E402

if a.fps30:
    hc_paths = set(os.path.abspath(h) for h in a.headcam)
    _probe = core.probe
    def probe(path):
        info = _probe(path)
        if os.path.abspath(path) in hc_paths:
            info.fps = "30000/1001"
        return info
    core.probe = probe
    _bp = core.build_plan
    def build_plan(*args, **kw):
        plan = _bp(*args, **kw)
        i = plan.cmd.index("-filter_complex") + 1
        plan.cmd[i] = re.sub(r"\[(\d+):v:0\](?=scale|hflip)", r"[\1:v:0]fps=30000/1001,", plan.cmd[i])
        return plan
    core.build_plan = build_plan
if a.amf_speed:
    _ea = core.encoder_args
    def encoder_args(enc, scale=1):
        args = _ea(enc, scale)
        if enc == "h264_amf":
            args[args.index("-quality") + 1] = "speed"
        return args
    core.encoder_args = encoder_args

extra = {}
if a.band != "ingen":
    from ol_video_maker.fitfile import load_fit
    hr = load_fit(a.fit) if a.fit else None
    total = sum(core.probe(f).duration for f in a.headcam)
    if a.band == "placering":
        sys.path.insert(0, os.path.join(a.repo, "tests"))
        from ol_video_maker import splits as S
        from ol_video_maker.placing_video import PlacingTrack
        from test_placing import EXEMPEL  # påhittade namn, riktiga tider (ca 30-60 min)
        extra["placing"] = PlacingTrack(S.parse(EXEMPEL), [0, 1, 2, 4], me=3, consent=True,
                                        race_start=a.start - 900)
    elif a.band == "puls":
        from ol_video_maker.pulse_video import PulseTrack
        extra["pulse"] = PulseTrack(hr, offset=hr.start - a.start + 30, graph_start=0.0, graph_end=total)
    else:
        from ol_video_maker.terrain_video import TerrainTrack
        if hr.terrain is None:
            sys.exit("fit-filen saknar höjd/fart")
        extra["terrain"] = TerrainTrack(hr, offset=hr.terrain.times[0] - a.start + 30,
                                        graph_start=0.0, graph_end=total)


def run(seconds):
    out = os.path.join(tempfile.gettempdir(), "olvm_mat_4k.mp4")
    job = core.Job(list(a.headcam), a.livelox, out, headcam_sync=a.start, livelox_sync=0,
                   lead_in=0, end_time=a.start + seconds, scale=1 if a.fullhd else 2, **extra)
    logs, marks = [], []
    t = time.monotonic()
    core.render(job, log=logs.append, progress=lambda q: marks.append((time.monotonic(), q)))
    tot = time.monotonic() - t
    os.remove(out)
    mv = [m for m in marks if 0 < m[1] < 1]
    if seconds > 60 and len(mv) > 10:  # fart per tiondel, för att se om den sjunker med tiden
        parts, last = [], mv[0]
        for m in mv[1:]:
            if m[1] - last[1] >= 0.1:
                parts.append(round((m[1] - last[1]) * seconds / (m[0] - last[0]), 2))
                last = m
        print(json.dumps({"fart_per_tiondel": parts}), flush=True)
    steady = ((mv[-1][1] - mv[0][1]) * seconds / (mv[-1][0] - mv[0][0])) if len(mv) >= 2 else 0
    return steady, seconds / tot, tot, logs


name = f"band={a.band}{' fps30' if a.fps30 else ''}{' amf-speed' if a.amf_speed else ''}{' fullhd' if a.fullhd else ''}"
s6, _, _, logs = run(6.0)
sf, total, tot, _ = run(a.sekunder)
kod = next((l for l in logs if l.startswith("Kodare")), "")
print(json.dumps({"variant": name, "repo": a.repo, "kodare": kod,
                  "provbit_6s": round(s6, 3), "hel_steady": round(sf, 3),
                  "hel_inkl_start": round(total, 3), "sekunder": a.sekunder,
                  "tid_s": round(tot, 1)}, ensure_ascii=False), flush=True)
