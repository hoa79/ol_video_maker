"""Tester för placeringslinjen: tolkning av sträcktider, läget under loppet, bilden och fönstret.

Alla namn och klubbar här är påhittade (juristens krav: aldrig riktiga namn i tester).
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request

from ol_video_maker import splits as S
from ol_video_maker import villkor
from ol_video_maker.core import Job, build_plan, has_filter, probe, render
from ol_video_maker.placing_video import (PlacingTrack, Scene, Shape, Text, build_band,
                                          figure_shapes, raster, svg_at)

from test_render import HAVE_FFMPEG, make_clip

T = "\t"


def row(*cells):
    return T.join(cells)


# Livelox-format: rubrik utan målkolumn, två rader per löpare, mp och dns längst ned.
EXEMPEL = "\r\n".join([
    row("Pl", "Name", "Time", "Time behind", "31", "", "32", "", "33", "", ""),
    row("", "Club", "", "", "500 m", "", "300 m", "", "200 m", "", "100 m", ""),
    row("1", "Anna Berg", "30:00", "−0:20", "10:00", "(2)", "8:00", "(1)", "7:00", "(1)",
        "5:00", "(1)"),
    row("", "OK Exempel", "", "", "10:00", "(2)", "18:00", "(1)", "25:00", "(1)", "30:00", "(1)"),
    row("2", "Elias Holm", "30:20", "+0:20", "9:00", "(1)", "10:00", "(3)", "7:00", "(1)",
        "4:20", "(1)"),
    row("", "SK Påhitt", "", "", "9:00", "(1)", "19:00", "(2)", "26:00", "(2)", "30:20", "(2)"),
    row("3", "Elias Hall", "34:00", "+4:00", "12:00", "(4)", "8:00", "(1)", "8:00", "(3)",
        "6:00", "(3)"),
    row("", "OK Exempel", "", "", "12:00", "(4)", "20:00", "(3)", "28:00", "(3)", "34:00", "(3)"),
    row("4", "Cecilia Dahl", "1:02:05", "+32:05", "11:00", "(3)", "24:00", "(4)", "15:00", "(4)",
        "12:05", "(4)"),
    row("", "SK Påhitt", "", "", "11:00", "(3)", "35:00", "(4)", "50:00", "(4)", "1:02:05", "(4)"),
    row(" ", "Gustav Lind", "mp", "", "8:00", "(1)", "", " ", "", " ", "5:00", "(1)"),
    row("", "OK Exempel", "", "", "8:00", "(1)", "", " ", "24:00", " ", "29:00", " "),
    row(" ", "Hanna Ny", "dns", "", "", " ", "", " ", "", " ", "", " "),
    row("", "SK Påhitt", "", "", "", " ", "", " ", "", " ", "", " "),
]) + "\r\n"


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.sp = S.parse(EXEMPEL)

    def test_header_and_runners(self):
        sp = self.sp
        self.assertEqual(sp.controls, ["31", "32", "33", "Mål"])
        self.assertEqual(sp.lengths, [500, 300, 200, 100])
        self.assertEqual([r.name for r in sp.runners],
                         ["Anna Berg", "Elias Holm", "Elias Hall", "Cecilia Dahl", "Gustav Lind",
                          "Hanna Ny"])
        a = sp.runners[0]
        self.assertEqual((a.place, a.time, a.club, a.status), (1, 1800, "OK Exempel", "ok"))
        self.assertEqual(a.splits, [600, 1080, 1500, 1800])
        self.assertEqual(a.split_places, [2, 1, 1, 1])
        self.assertEqual(sp.runners[3].time, 3725)   # h:mm:ss

    def test_mp_and_dns(self):
        mp, dns = self.sp.runners[4], self.sp.runners[5]
        self.assertEqual(mp.status, "mp")
        self.assertIsNone(mp.place)
        self.assertEqual(mp.splits, [480, None, 1440, 1740])
        self.assertTrue(mp.selectable)
        self.assertEqual(dns.status, "dns")
        self.assertFalse(dns.started)
        self.assertFalse(dns.selectable)
        self.assertEqual(S.summary(self.sp), {"runners": 6, "controls": 4, "mp": 1, "dns": 1})

    def test_garbage_gives_nothing(self):
        self.assertEqual(S.parse("hej\nhopp").runners, [])
        self.assertEqual(S.parse("").runners, [])

    def test_unknown_lengths_are_equal(self):
        text = "\n".join(l for l in EXEMPEL.split("\r\n") if "500 m" not in l)
        sp = S.parse(text)
        self.assertEqual(sp.lengths, [1.0, 1.0, 1.0, 1.0])

    def test_short_names(self):
        self.assertEqual(S.short_names(["Anna Berg", "Elias Holm", "Elias Hall", "Kim"]),
                         ["Anna B.", "Elias Ho.", "Elias Ha.", "Kim"])

    def test_ordinal(self):
        self.assertEqual([S.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 21, 22, 101, 111)],
                         ["1:a", "2:a", "3:e", "4:e", "11:e", "12:e", "21:a", "22:a", "101:a",
                          "111:e"])


class MomentTest(unittest.TestCase):
    def setUp(self):
        self.sp = S.parse(EXEMPEL)

    def test_position_weighted_by_length(self):
        a = self.sp.runners[0]
        self.assertAlmostEqual(S.moment(self.sp, a, 0).pos, 0.0)
        self.assertAlmostEqual(S.moment(self.sp, a, 300).pos, 250 / 1100)   # halva sträcka 1
        self.assertAlmostEqual(S.moment(self.sp, a, 600).pos, 500 / 1100)
        m = S.moment(self.sp, a, 5000)
        self.assertTrue(m.finished)
        self.assertEqual((m.pos, m.place), (1.0, 1))

    def test_place_and_trend_on_current_leg(self):
        e = self.sp.runners[2]   # 4:a vid 31, 3:a vid 32
        m = S.moment(self.sp, e, 800)
        self.assertEqual((m.passed, m.place, m.trend), (0, 4, 1))
        m = S.moment(self.sp, e, 100)
        self.assertEqual((m.passed, m.place, m.trend), (-1, None, 0))

    def test_mp_fades_at_missing_control(self):
        mp = self.sp.runners[4]
        self.assertEqual(S.moment(self.sp, mp, 400).visible, 1.0)
        # Kontroll 32 saknas: nås (interpolerat mot 33) vid 480 + (1440-480)*300/500 = 1056 s.
        self.assertEqual(S.moment(self.sp, mp, 1000).visible, 1.0)
        self.assertEqual(S.moment(self.sp, mp, 1056 + S.FADE_SECONDS + 1).visible, 0.0)
        self.assertEqual(S.moment(self.sp, mp, 1800).visible, 0.0)

    def test_presets(self):
        sp = self.sp
        self.assertEqual(S.preset(sp, "top", 3), [0, 1, 2, 3, 4])
        self.assertEqual(S.preset(sp, "top", None), [0, 1, 2, 3, 4])   # topp 8 utan du (5 finns)
        self.assertEqual(S.preset(sp, "club", 0), [0, 2, 4])
        self.assertEqual(S.preset(sp, "clear", 2), [2])
        self.assertEqual(S.preset(sp, "near", None), [])


class SceneTest(unittest.TestCase):
    def setUp(self):
        self.sp = S.parse(EXEMPEL)

    def texts(self, items):
        return ["".join(p[0] for p in it.obj.parts) for it in items if isinstance(it.obj, Text)]

    def test_names_only_with_consent(self):
        tr = PlacingTrack(self.sp, [0, 1, 2], me=3, consent=False)
        texts = self.texts(Scene(tr, 1280).at(900))
        self.assertIn("3 Cecilia D.", texts)               # du visas alltid med namn
        self.assertFalse(any("Anna" in t or "Elias" in t for t in texts))
        self.assertIn("vid kontroll 31", texts)
        tr.consent = True
        texts = self.texts(Scene(tr, 1280).at(900))
        self.assertTrue(any("Anna B." in t for t in texts))
        self.assertTrue(any("Elias Ho." in t for t in texts))

    def test_without_me_no_panel(self):
        tr = PlacingTrack(self.sp, [0, 1, 2], me=None, consent=False)
        sc = Scene(tr, 1280)
        items = sc.at(900)
        texts = self.texts(items)
        self.assertEqual(sc.x0, 60)
        self.assertFalse(any(t.endswith(":a") or t.endswith(":e") for t in texts))
        self.assertFalse(any(it.slot == "gul" for it in items))
        self.assertFalse(any("Anna" in t or "Elias" in t for t in texts))
        self.assertIn("lopptid 15:00", texts)

    def test_never_mp_text_and_max_8(self):
        tr = PlacingTrack(self.sp, [0, 1, 2, 3, 4, 5], me=None, consent=True)
        for t in (0, 600, 1200, 2000):
            for txt in self.texts(Scene(tr, 1280).at(t)):
                self.assertNotIn("mp", txt.lower().split())
                self.assertNotIn("felst", txt.lower())
                self.assertNotIn("Hanna", txt)               # ej startad syns aldrig
        sc = Scene(PlacingTrack(self.sp, list(range(6)) * 3, me=0, consent=True), 1280)
        self.assertLessEqual(len(sc.shown), S.MAX_SHOWN)

    def test_svg(self):
        tr = PlacingTrack(self.sp, [0, 1], me=2, consent=True)
        svg = svg_at(tr, 1280, 900)
        self.assertIn("<circle", svg)
        self.assertIn("Elias Ha.", svg)

    def test_figures_raster(self):
        for kind in ("fast", "tired"):
            shapes = figure_shapes(kind, 5)
            self.assertTrue(all(isinstance(s, Shape) for s in shapes))
            data = raster(shapes, 52, 42, 1.0, -31, -21)
            self.assertTrue(data.startswith(b"P7\n"))
            self.assertGreater(sum(data[-52 * 42 * 4:][3::4]), 0)

    def test_ass_file(self):
        tmp = tempfile.mkdtemp()
        try:
            tr = PlacingTrack(self.sp, [0, 1, 2, 4], me=3, consent=True, race_start=5.0)
            band = build_band(tr, tmp, 1, 1280, 0.0, 120.0, 3, [], lambda v: v)
            with open(band.files[0], encoding="utf-8") as f:
                ass = f.read()
            self.assertIn("PlayResX: 1920", ass)
            self.assertIn("Dialogue:", ass)
            self.assertIn("Cecilia D.", ass)
            self.assertIn("ass=filename=", band.overlays)
            self.assertGreater(band.events, 10)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LiveloxTimeTest(unittest.TestCase):
    """Alla tider i placeringslinjen kommer från sträcktiderna (simons felrapport: i mål i
    filmen men pricken stod mellan två kontroller)."""

    def setUp(self):
        self.sp = S.parse(EXEMPEL)

    def test_finish_in_film_is_finish_in_line(self):
        # Elias Hall: 34:00 i Livelox. I filmen går start -> mål bara 32:00 (t.ex. starten
        # synkad lite sent). Vid målgången i filmen ska han ändå stå i mål.
        tr = PlacingTrack(self.sp, [0, 1], me=2, consent=True, race_start=100.0,
                          race_end=100.0 + 32 * 60)
        self.assertAlmostEqual(tr.race_time(100.0 + 32 * 60), 34 * 60)
        self.assertAlmostEqual(tr.race_time(100.0), 0.0)
        m = S.moment(self.sp, self.sp.runners[2], tr.race_time(100.0 + 32 * 60))
        self.assertTrue(m.finished)
        texts = " ".join(t for it in Scene(tr, 1280).at(tr.race_time(100.0 + 32 * 60))
                         if isinstance(it.obj, Text) for t, _, _ in it.obj.parts)
        self.assertIn("i mål", texts)

    def test_without_finish_mark_film_time_is_used(self):
        tr = PlacingTrack(self.sp, [0, 1], me=2, consent=True, race_start=100.0)
        self.assertAlmostEqual(tr.race_time(700.0), 600.0)

    def test_without_me_film_time_is_used(self):
        tr = PlacingTrack(self.sp, [0, 1], me=None, consent=True, race_start=100.0,
                          race_end=2000.0)
        self.assertAlmostEqual(tr.race_time(700.0), 600.0)

    def test_band_follows_livelox_time(self):
        tmp = tempfile.mkdtemp()
        try:
            # Filmen: start vid 0 s, målgång vid 1000 s. Elias Hall har 34:00 = 2040 s.
            tr = PlacingTrack(self.sp, [0], me=2, consent=True, race_start=0.0,
                              race_end=1000.0)
            build_band(tr, tmp, 1, 1280, 990.0, 10.0, 3, [], lambda v: v)
            with open(os.path.join(tmp, "placering.ass"), encoding="utf-8") as f:
                ass = f.read()
            self.assertIn("i mål", ass)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.hc = os.path.join(cls.tmp, "hc.mp4")
        cls.ll = os.path.join(cls.tmp, "ll.mp4")
        make_clip(cls.hc, 4, "1280x720")
        make_clip(cls.ll, 4, "640x1080", color_src="color=c=green", audio=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pulse_and_placing_not_both(self):
        from ol_video_maker.core import VideoMakerError
        from ol_video_maker.fitfile import HeartRate
        from ol_video_maker.pulse_video import PulseTrack
        sp = S.parse(EXEMPEL)
        job = Job([self.hc], self.ll, os.path.join(self.tmp, "x.mp4"),
                  pulse=PulseTrack(HeartRate([0.0, 1.0], [100, 110]), 0.0),
                  placing=PlacingTrack(sp, [0], None, True))
        with self.assertRaises(VideoMakerError):
            build_plan(job, [probe(self.hc)], probe(self.ll), "libx264")

    @unittest.skipUnless(HAVE_FFMPEG and has_filter("ass"), "ffmpeg utan ass-filter")
    def test_render_with_placing(self):
        sp = S.parse(EXEMPEL)
        out = os.path.join(self.tmp, "placering.mp4")
        # Lopptid 11:40 när videon börjar: Elias Hall springer sträcka 2 (4:a -> 3:a).
        tr = PlacingTrack(sp, [0, 1, 3, 4], me=2, consent=True, race_start=-700.0)
        render(Job([self.hc], self.ll, out, lead_in=0, placing=tr))
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", out],
            capture_output=True, text=True, check=True).stdout)
        v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["width"], v["height"], v["codec_name"]), (1920, 1080, "h264"))
        # Gul text (din placering) längst ner till vänster.
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", out, "-frames:v", "1",
                              "-vf", "crop=180:80:20:910,format=rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        yellow = sum(1 for i in range(0, len(raw), 3)
                     if raw[i] > 200 and raw[i + 1] > 170 and raw[i + 2] < 80)
        self.assertGreater(yellow, 100)


class WebPlacingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ol_video_maker.webapp import Dialogs, Server, State
        cls.old = os.environ.get("OLVM_DATA_DIR")
        os.environ["OLVM_DATA_DIR"] = tempfile.mkdtemp()
        cls.state = State(Dialogs())
        cls.server = Server(cls.state)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.post(cls, "accept_terms", {"version": villkor.VERSION})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        if cls.old is None:
            os.environ.pop("OLVM_DATA_DIR", None)
        else:
            os.environ["OLVM_DATA_DIR"] = cls.old

    def post(self, name, body):
        req = urllib.request.Request(self.base + "/api/" + name, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-OLVM-Token": self.server.token}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def snap(self):
        return self.state.snapshot()

    def test_flow(self):
        self.post("set_under", {"what": "placing"})
        self.assertEqual(self.snap()["under"], "placing")
        self.post("set_splits", {"text": EXEMPEL})
        pl = self.snap()["placing"]
        self.assertEqual(len(pl["runners"]), 6)
        self.assertFalse(pl["consent"])                 # aldrig förifylld
        self.assertIsNone(pl["me"])
        self.assertFalse(pl["runners"][5]["selectable"])
        # Vem är du? -> topp 5 + du väljs och namnet sparas till nästa gång
        self.post("set_me", {"name": "Cecilia Dahl"})
        pl = self.snap()["placing"]
        self.assertEqual(pl["meIndex"], 3)
        self.assertEqual(pl["shown"], [0, 1, 2, 4])
        self.assertEqual(S.load_me(), "Cecilia Dahl")
        self.assertTrue(pl["saved"])
        # Högst 8 (här fel om man försöker välja den som inte startat)
        self.post("set_shown", {"shown": [0, 5]})
        self.assertEqual(self.snap()["placing"]["shown"], [0])
        # Ingen: din placering försvinner
        self.post("set_me", {"name": ""})
        pl = self.snap()["placing"]
        self.assertEqual(pl["me"], "")
        self.assertIsNone(pl["meIndex"])
        # Ta bort det sparade namnet
        self.post("forget_me", {})
        self.assertIsNone(S.load_me())
        self.assertFalse(self.snap()["placing"]["saved"])
        self.post("set_consent", {"on": True})
        self.assertTrue(self.snap()["placing"]["consent"])
        # Utan synkad start finns ingen linje i videon än
        self.assertFalse(self.snap()["placingOn"])
        self.state.hc_sync = 10.0
        self.assertTrue(self.snap()["placingOn"])
        with urllib.request.urlopen(f"{self.base}/api/placing_svg?t=700&lw=640&k="
                                    f"{self.server.token}", timeout=30) as r:
            svg = json.loads(r.read())["svg"]
        self.assertIn("Anna B.", svg)
        self.post("set_under", {"what": "none"})
        self.assertFalse(self.snap()["placingOn"])

    def test_too_many(self):
        self.post("set_splits", {"text": EXEMPEL})
        self.post("set_me", {"name": "Anna Berg"})
        r = self.post("set_shown", {"shown": [1, 2, 3, 4]})
        self.assertNotIn("error", r)
        self.assertEqual(len(self.snap()["placing"]["shown"]), 4)


class TermsTest(unittest.TestCase):
    def test_version_2_has_names_point(self):
        self.assertEqual(villkor.VERSION, 2)
        titles = [t for t, _ in villkor.POINTS]
        self.assertEqual(titles[2], "Du ansvarar för namnen och resultaten du visar.")
        md = villkor.as_markdown()
        self.assertIn("Karta och sträcktider: Livelox", md)
        self.assertIn("sträcktiderna du klistrar in", md)


if __name__ == "__main__":
    unittest.main()
