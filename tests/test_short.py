"""Kort video för mobilen (stående 9:16): förval, bitar och själva videon."""
import os
import shutil
import tempfile
import unittest

from ol_video_maker.core import VideoMakerError
from ol_video_maker.short_video import (MAX_SECONDS, Clip, ShortJob, add_clip,
                                        label, render_short, short_path, suggest, total)
from ol_video_maker.splits import Runner, Splits

from test_render import HAVE_FFMPEG, make_clip, pixel, streams


def runner(name, splits):
    return Runner(name=name, club="", place=1, time=splits[-1], status="ok", splits=splits,
                  split_places=[None] * len(splits))


class Suggest(unittest.TestCase):
    def test_without_splits_spreads_clips(self):
        clips = suggest(hc_sync=20, end=1800, duration=1900)
        self.assertEqual(clips[0].name, "Start")
        self.assertEqual(clips[-1].name, "Målgång")
        self.assertEqual(len(clips), 7)
        self.assertLessEqual(total(clips), MAX_SECONDS)
        self.assertAlmostEqual(clips[0].start, 19.0)
        self.assertAlmostEqual(clips[-1].start + clips[-1].length, 1800)
        starts = [c.start for c in clips]
        self.assertEqual(starts, sorted(starts))

    def test_with_splits_takes_interesting_controls(self):
        # Fyra kontroller + mål. Du tappar mest på sträcka 3 och är snabbast på sträcka 1.
        sp = Splits(controls=["31", "32", "33", "34", "Mål"], lengths=[1] * 5, runners=[
            runner("Du", [100, 300, 700, 900, 1000]),
            runner("Bäst", [110, 290, 450, 640, 700]),
        ])
        clips = suggest(hc_sync=10, end=1010, duration=2000, splits=sp, me=0, n_middle=2)
        names = [c.name for c in clips]
        self.assertEqual(names, ["Start", "Kontroll 1", "Kontroll 3", "Målgång"])
        k3 = clips[2]
        # Kontrollbiten slutar strax efter stämplingen (10 + 700 s i headcam).
        self.assertAlmostEqual(k3.start, 10 + 700 - 6)

    def test_short_race(self):
        clips = suggest(hc_sync=2, end=8, duration=8)
        self.assertEqual([c.name for c in clips], ["Start"])

    def test_add_clip_avoids_overlap_and_limit(self):
        clips = [Clip(10, 8, "Start")]
        self.assertTrue(add_clip(clips, Clip(14, 8), 0, 100))
        self.assertAlmostEqual(clips[1].start, 18)
        self.assertAlmostEqual(clips[1].length, 4)
        clips = [Clip(i * 10, 8) for i in range(7)]  # 56 s: bara 4 s plats kvar
        self.assertTrue(add_clip(clips, Clip(200, 8), 0, 300))
        self.assertAlmostEqual(clips[-1].length, 4)
        self.assertFalse(add_clip(clips, Clip(250, 8), 0, 300))   # nu är minuten full
        self.assertEqual(len(clips), 8)

    def test_label_and_path(self):
        self.assertEqual(label(Clip(100, 8, "Start"), 101), "Start")
        self.assertEqual(label(Clip(1400, 8, "Kontroll 7"), 6), "Kontroll 7 · 23:18")
        self.assertEqual(label(Clip(1400, 8, ""), 6), "23:18")
        self.assertTrue(short_path(os.path.join("a", "OL-video.mp4")).endswith("OL-video_kort.mp4"))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class RenderShort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hc1 = os.path.join(self.tmp, "GH010001.MP4")
        self.hc2 = os.path.join(self.tmp, "GH020001.MP4")
        self.ll = os.path.join(self.tmp, "livelox.mp4")
        make_clip(self.hc1, 6, "640x360", "color=c=red")
        make_clip(self.hc2, 6, "640x360", "color=c=blue")
        make_clip(self.ll, 20, "400x400", "color=c=green", audio=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vertical_video_over_chapter_border(self):
        out = os.path.join(self.tmp, "kort.mp4")
        job = ShortJob(headcam_files=[self.hc1, self.hc2], livelox_file=self.ll, output_file=out,
                       clips=[Clip(0.5, 2, "Start"), Clip(5, 2, "Kontroll 1"), Clip(9, 2, "Målgång")],
                       headcam_sync=1, livelox_sync=0)
        render_short(job)
        info = streams(out)
        v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        a = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
        self.assertEqual((v["width"], v["height"]), (1080, 1920))
        self.assertEqual(v["codec_name"], "h264")
        self.assertEqual(a["codec_name"], "aac")
        self.assertAlmostEqual(float(info["format"]["duration"]), 6.0, delta=0.4)
        # Headcam överst (röd i första biten), kartan under (grön), aldrig ovanpå varandra.
        r, g, b = pixel(out, 1.0, 540, 400)
        self.assertGreater(r, 180); self.assertLess(g, 80)
        r, g, b = pixel(out, 1.0, 540, 1200)
        self.assertGreater(g, 90); self.assertLess(r, 60)
        # Andra kapitlet (blå) syns i sista biten.
        r, g, b = pixel(out, 5.0, 540, 400)
        self.assertGreater(b, 180); self.assertLess(r, 60)

    def test_too_long(self):
        job = ShortJob(headcam_files=[self.hc1], livelox_file=self.ll,
                       output_file=os.path.join(self.tmp, "x.mp4"), clips=[Clip(0, 61)])
        with self.assertRaises(VideoMakerError):
            render_short(job)


if __name__ == "__main__":
    unittest.main()
