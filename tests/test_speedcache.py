"""Tester för minnet av datorns hastighet (speedcache.py) och tidsuppskattningen i steg 5."""
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from ol_video_maker import speedcache, webapp
from ol_video_maker.core import MediaInfo
from ol_video_maker.webapp import Dialogs, State

from test_render import HAVE_FFMPEG, make_clip

HC = MediaInfo(path=r"C:\Users\någon\Videos\GS010021.MP4", duration=600, width=3840,
               height=2160, has_audio=True, fps="60000/1001", codec="hevc")
LL = MediaInfo(path=r"C:\Users\någon\Videos\livelox.mp4", duration=900, width=1920,
               height=1080, has_audio=False, fps="30", codec="h264")


class DataDirTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("OLVM_DATA_DIR")
        os.environ["OLVM_DATA_DIR"] = tempfile.mkdtemp()  # aldrig användarens riktiga mapp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("OLVM_DATA_DIR", None)
        else:
            os.environ["OLVM_DATA_DIR"] = self.old


class SpeedCacheTest(DataDirTest):
    def test_put_get_and_weighting(self):
        key = speedcache.profile(HC, LL, False, 2)
        self.assertIsNone(speedcache.get(key))
        speedcache.put(key, 0.5, source="prov")
        self.assertAlmostEqual(speedcache.get(key), 0.5)
        speedcache.put(key, 1.0, source="video")  # riktig video väger tyngst
        self.assertAlmostEqual(speedcache.get(key), 0.85)

    def test_key_depends_on_format_pulse_and_scale(self):
        keys = {speedcache.profile(HC, LL, False, 1), speedcache.profile(HC, LL, False, 2),
                speedcache.profile(HC, LL, True, 1), speedcache.profile(LL, LL, False, 1)}
        self.assertEqual(len(keys), 4)

    def test_no_file_names_saved(self):
        speedcache.put(speedcache.profile(HC, LL, True, 1), 2.0)
        with open(os.path.join(os.environ["OLVM_DATA_DIR"], speedcache.FILE_NAME),
                  encoding="utf-8") as f:
            text = f.read()
        for word in ("GS010021", "livelox.mp4", "Users", "någon"):
            self.assertNotIn(word, text)

    def test_broken_file_and_limit(self):
        path = os.path.join(os.environ["OLVM_DATA_DIR"], speedcache.FILE_NAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{inte json")
        self.assertIsNone(speedcache.get("x"))
        for i in range(speedcache.MAX_ENTRIES + 5):
            speedcache.put(f"k{i}", 1.0)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), speedcache.MAX_ENTRIES)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class EstimateTest(DataDirTest):
    def load(self, state):
        folder = tempfile.mkdtemp()
        hc, ll = os.path.join(folder, "hc.mp4"), os.path.join(folder, "ll.mp4")
        make_clip(hc, 30, "320x180")
        make_clip(ll, 30, "320x180")
        state.load({"which": "headcam", "files": [hc]})
        state.load({"which": "livelox", "files": [ll]})
        for _ in range(200):
            if state.hc_tl and state.ll_tl:
                break
            threading.Event().wait(0.05)
        state.mark({"what": "hc", "t": 10.0})
        state.mark({"what": "ll", "t": 2.0})

    def wait_bench(self, state):
        for _ in range(600):
            if not state.bench_running:
                return
            threading.Event().wait(0.05)
        self.fail("mätningen blev inte klar")

    def test_measures_once_then_uses_memory(self):
        state = State(Dialogs())
        self.load(state)
        calls = []
        real = webapp.render

        def counting_render(job, **kw):
            calls.append(job.scale)
            return real(job, **kw)

        with mock.patch.object(webapp, "render", counting_render):
            state.bench({})
            self.wait_bench(state)
            self.assertEqual(sorted(calls), [1, 2])
            first = state.snapshot()["estimates"]
            self.assertIsNotNone(first["1"])
            self.assertIsNotNone(first["2"])

            # Ändrad målgång: ingen ny provvideo, tiden räknas om ur längden.
            state.mark({"what": "end", "t": 20.0})
            state.bench({})
            self.wait_bench(state)
            self.assertEqual(len(calls), 2)
            shorter = state.snapshot()["estimates"]
            self.assertLess(shorter["1"], first["1"])

            # Programmet startas om med samma sorts filmer: minnet används direkt.
            state2 = State(Dialogs())
            self.load(state2)
            state2.bench({})
            self.assertFalse(state2.bench_running)
            self.assertEqual(len(calls), 2)
            self.assertIsNotNone(state2.snapshot()["estimates"]["2"])

    def test_real_video_is_remembered(self):
        state = State(Dialogs())
        self.load(state)
        out = os.path.join(tempfile.mkdtemp(), "klar.mp4")
        state.make({"path": out})
        for _ in range(1200):
            if not state.running and state.render.get("ok") is not None:
                break
            threading.Event().wait(0.05)
        self.assertTrue(state.render["ok"], state.render)
        key = state._speed_key(1)
        with open(os.path.join(os.environ["OLVM_DATA_DIR"], speedcache.FILE_NAME),
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)[key]["kalla"], "video")
        self.assertIsNotNone(state.snapshot()["estimates"]["1"])


if __name__ == "__main__":
    unittest.main()
