"""Klockan med tid sedan start (clock_video.py)."""
import os
import shutil
import tempfile
import unittest

from ol_video_maker.clock_video import ClockTrack, build_clock, draw_watch, fmt_clock
from ol_video_maker.core import Job, _escape_filter_value, render

from test_render import HAVE_FFMPEG, make_clip, pixel


class ClockTimeTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(fmt_clock(0), "0:00")
        self.assertEqual(fmt_clock(2487.9), "41:27")
        self.assertEqual(fmt_clock(3733), "1:02:13")
        self.assertEqual(fmt_clock(-5), "0:00")

    def test_plain_film_time(self):
        tr = ClockTrack(race_start=100.0)
        self.assertEqual(tr.race_time(90.0), 0.0)
        self.assertEqual(tr.race_time(160.0), 60.0)
        self.assertIsNone(tr.finish_at())

    def test_stops_at_finish(self):
        tr = ClockTrack(race_start=100.0, finish=400.0)
        self.assertEqual(tr.race_time(500.0), 300.0)
        self.assertEqual(tr.final_time(), 300.0)

    def test_official_time_lands_at_finish(self):
        # Målgången i filmen ligger 4 s fel mot sträcktiderna: klockan sträcks jämnt.
        tr = ClockTrack(race_start=100.0, finish=2584.0, official=2488.0)
        self.assertAlmostEqual(tr.race_time(2584.0), 2488.0)
        self.assertAlmostEqual(tr.race_time(100.0 + 1242.0), 1244.0)
        self.assertEqual(tr.race_time(3000.0), 2488.0)

    def test_official_without_finish(self):
        tr = ClockTrack(race_start=10.0, official=60.0)
        self.assertEqual(tr.finish_at(), 70.0)
        self.assertEqual(tr.race_time(100.0), 60.0)

    def test_watch_image(self):
        data = draw_watch(40)
        self.assertTrue(data.startswith(b"P7\n"))
        self.assertGreater(sum(data[-40 * 40 * 4 + 3::4]), 0)  # något är ritat


class ClockFilterTest(unittest.TestCase):
    def test_parts(self):
        tmp = tempfile.mkdtemp()
        try:
            tr = ClockTrack(race_start=5.0, finish=20.0, official=3700.0)
            c = build_clock(tr, tmp, 1, 0.0, 25.0, 3, ("font.ttf", "bold.ttf"),
                            _escape_filter_value)
            self.assertEqual(c.inputs[0], "-i")
            self.assertIn("[3:v]", c.chains[0])
            ov = c.overlays
            self.assertIn("0\\:00", ov)                 # grå före starten
            self.assertIn("eif", ov)                    # räknar upp
            self.assertIn("1\\:01\\:40", ov)            # officiell tid i mål
            self.assertIn("officiell tid från Livelox", ov)
            self.assertEqual(ov.count("fontsize=96"), 5)  # före, m:ss, mm:ss, h:mm:ss, mål
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class ClockRenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hc = os.path.join(self.tmp, "hc.mp4")
        self.ll = os.path.join(self.tmp, "ll.mp4")
        make_clip(self.hc, 6, "1280x720", color_src="color=c=black")
        make_clip(self.ll, 6, "640x360", audio=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_with_clock(self):
        out = os.path.join(self.tmp, "ut.mp4")
        job = Job([self.hc], self.ll, out, headcam_sync=1.0, lead_in=1.0, end_time=5.0,
                  clock=ClockTrack(race_start=1.0, finish=4.0))
        render(job)
        # Stoppuret (grått) ritas i fältet ovanför filmen; filmen själv är svart.
        r, g, b = pixel(out, 2.0, 52, 102)  # ringens vänstra kant
        self.assertGreater(r, 80)
        # Grön sluttid efter målgången (någon grön pixel i tiden).
        greens = [pixel(out, 4.5, x, 120) for x in range(140, 380, 6)]
        self.assertTrue(any(gg > 150 and rr < 120 for rr, gg, bb in greens), greens)


if __name__ == "__main__":
    unittest.main()


class ClockStateTest(unittest.TestCase):
    """Fönstrets läge: klockan i jobbet och videon 5 s efter målgången."""

    def setUp(self):
        from types import SimpleNamespace
        from ol_video_maker.webapp import Dialogs, State
        self.old = os.environ.get("OLVM_DATA_DIR")
        os.environ["OLVM_DATA_DIR"] = tempfile.mkdtemp()
        st = State(Dialogs())
        clip = SimpleNamespace(path="hc.mp4")
        st.hc_tl = SimpleNamespace(clips=[clip], duration=1000.0)
        st.ll_tl = SimpleNamespace(clips=[SimpleNamespace(path="ll.mp4")], duration=1000.0)
        st.hc_sync, st.ll_sync = 100.0, 10.0
        self.st = st

    def tearDown(self):
        if self.old is None:
            os.environ.pop("OLVM_DATA_DIR", None)
        else:
            os.environ["OLVM_DATA_DIR"] = self.old

    def test_clock_on_by_default(self):
        job = self.st.make_job("ut.mp4")
        self.assertIsNotNone(job.clock)
        self.assertEqual(job.clock.race_start, 100.0)
        self.assertIsNone(job.end_time)
        self.st.set_clock({"on": False})
        self.assertIsNone(self.st.make_job("ut.mp4").clock)

    def test_five_seconds_after_finish(self):
        self.st.end_time = 600.0
        job = self.st.make_job("ut.mp4")
        self.assertEqual(job.end_time, 605.0)       # videon
        self.assertEqual(job.clock.finish, 600.0)   # klockan stannar vid målgången
        self.assertAlmostEqual(self.st.video_length(), 605.0 - 95.0)
        self.st.end_time = 998.0                    # aldrig längre än filmen
        self.assertEqual(self.st.make_job("ut.mp4").end_time, 1000.0)


class FontWidthTest(unittest.TestCase):
    def test_width(self):
        from ol_video_maker.core import find_font
        from ol_video_maker.fontwidth import text_width
        self.assertAlmostEqual(text_width(None, "abc", 10), 18.0)   # uppskattning
        font = find_font(True)
        if font:
            w4, w5 = text_width(font, "0:00", 96), text_width(font, "00:00", 96)
            self.assertGreater(w4, 100)
            self.assertGreater(w5, w4)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class ClockNeverCoversFilmTest(unittest.TestCase):
    """Fältet överst hålls fritt från filmen när klockan är på: med och utan band under
    videon och med smal, vanlig och bred kartkolumn."""

    def test_field_free(self):
        from ol_video_maker.clock_video import FIELD_H
        from ol_video_maker.fitfile import HeartRate
        from ol_video_maker.pulse_video import PulseTrack
        tmp = tempfile.mkdtemp()
        try:
            hc = os.path.join(tmp, "hc.mp4")
            ll = os.path.join(tmp, "ll.mp4")
            make_clip(hc, 2, "1280x720", color_src="color=c=white", audio=False)
            make_clip(ll, 2, "640x360", color_src="color=c=black", audio=False)
            hr = HeartRate([float(s) for s in range(5)], [150] * 5)
            for band in (False, True):
                for lw in (320, 640, 960):
                    out = os.path.join(tmp, f"ut_{band}_{lw}.mp4")
                    render(Job([hc], ll, out, lead_in=0, end_time=1.0, livelox_width=lw,
                               pulse=PulseTrack(hr, offset=0) if band else None,
                               clock=ClockTrack(race_start=0.0)))
                    box_w = 1920 - lw
                    # Raden precis ovanför fältets kant: svart (ingen film) …
                    for x in range(box_w // 2, box_w - 4, 97):
                        self.assertLess(sum(pixel(out, 0.5, x, FIELD_H - 3)), 120,
                                        (band, lw, x))
                    # … och filmen syns under fältet.
                    self.assertGreater(sum(pixel(out, 0.5, box_w // 4, FIELD_H + 120)), 600,
                                       (band, lw))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_scaled_down_when_narrow(self):
        tmp = tempfile.mkdtemp()
        try:
            from ol_video_maker.core import find_font
            tr = ClockTrack(race_start=0.0, finish=4000.0, official=4000.0)
            wide = build_clock(tr, tmp, 1, 0.0, 4010.0, 3, (find_font(), find_font(True)),
                               _escape_filter_value, box_w=1280).overlays
            narrow = build_clock(tr, tmp, 1, 0.0, 4010.0, 3, (find_font(), find_font(True)),
                                 _escape_filter_value, box_w=640).overlays
            self.assertIn("fontsize=96", wide)
            self.assertNotIn("fontsize=96", narrow)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
