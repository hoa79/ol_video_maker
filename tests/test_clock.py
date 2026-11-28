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
            self.assertIn("OFFICIELL TID FRÅN LIVELOX", ov)
            self.assertEqual(ov.count("fontsize=96"), 4)  # före, m:ss, h:mm:ss, mål
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
        r, g, b = pixel(out, 2.0, 52, 112)  # ringens vänstra kant
        self.assertGreater(r, 80)
        # Grön sluttid efter målgången (någon grön pixel i tiden).
        greens = [pixel(out, 4.5, x, 120) for x in range(140, 380, 6)]
        self.assertTrue(any(gg > 150 and rr < 120 for rr, gg, bb in greens), greens)


if __name__ == "__main__":
    unittest.main()
