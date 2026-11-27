"""Tester för bildhämtningen som används när man bläddrar i filmerna i fönstret."""
import io
import os
import tempfile
import unittest

from ol_video_maker.core import probe
from ol_video_maker.frames import Clip, Timeline, _read_ppm, grab_frame, play_frames

from test_render import HAVE_FFMPEG, make_clip


class TimelineTest(unittest.TestCase):
    def test_locate_across_chapters(self):
        tl = Timeline([Clip("a", 0, 10, 30), Clip("b", 10, 5, 30)])
        self.assertEqual(tl.duration, 15)
        self.assertEqual(tl.locate(3)[0].path, "a")
        clip, off = tl.locate(12.5)
        self.assertEqual((clip.path, off), ("b", 2.5))
        clip, off = tl.locate(99)  # efter slutet: sista bildrutan
        self.assertEqual(clip.path, "b")
        self.assertLess(off, 5)

    def test_read_ppm_stream(self):
        img = b"P6\n2 1\n255\n" + bytes(6)
        s = io.BytesIO(img + img)
        self.assertEqual(_read_ppm(s), img)
        self.assertEqual(_read_ppm(s), img)
        self.assertIsNone(_read_ppm(s))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class GrabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.a = os.path.join(cls.tmp, "GS010001.MP4")
        cls.b = os.path.join(cls.tmp, "GS020001.MP4")
        make_clip(cls.a, 4, "320x240", rate=25)
        make_clip(cls.b, 3, "320x240", color_src="testsrc", rate=25)

    def test_timeline_from_files(self):
        tl = Timeline.from_infos([probe(self.a), probe(self.b)])
        self.assertAlmostEqual(tl.duration, 7, delta=0.2)
        self.assertAlmostEqual(tl.fps, 25, delta=0.01)
        self.assertEqual(tl.locate(5)[0].path, self.b)

    def test_grab_frame_fits_box(self):
        data = grab_frame(self.a, 1.0, 160, 160)
        self.assertTrue(data.startswith(b"P6"))
        w, h = map(int, data.split(b"\n")[1].split())
        self.assertEqual((w, h), (160, 120))

    def test_play_frames(self):
        frames = list(play_frames(self.a, 0.5, 2.0, 10, 160, 120))
        self.assertGreaterEqual(len(frames), 19)
        self.assertLessEqual(len(frames), 21)


if __name__ == "__main__":
    unittest.main()
