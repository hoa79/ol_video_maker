"""Tester för viloläge (vila.py): hålla datorn vaken och märka sovtid."""
import unittest
from unittest import mock

from ol_video_maker import vila


class FakeKernel:
    def __init__(self):
        self.calls = []

        def set_state(flags):
            self.calls.append(flags)
            return 0x80000000  # förra läget, inte 0 = lyckades
        self.SetThreadExecutionState = set_state


class KeepAwakeTest(unittest.TestCase):
    def test_sets_and_restores_on_windows(self):
        k = FakeKernel()
        with mock.patch.object(vila, "_kernel32", return_value=k):
            with vila.keep_awake() as ok:
                self.assertTrue(ok)
                self.assertEqual(k.calls, [vila.ES_CONTINUOUS | vila.ES_SYSTEM_REQUIRED])
        self.assertEqual(k.calls[-1], vila.ES_CONTINUOUS)

    def test_restores_after_error(self):
        k = FakeKernel()
        with mock.patch.object(vila, "_kernel32", return_value=k):
            with self.assertRaises(RuntimeError):
                with vila.keep_awake():
                    raise RuntimeError("ffmpeg gick fel")
        self.assertEqual(k.calls[-1], vila.ES_CONTINUOUS)

    def test_noop_elsewhere(self):
        with mock.patch.object(vila, "_kernel32", return_value=None):
            with vila.keep_awake() as ok:
                self.assertFalse(ok)


class SleepMeterTest(unittest.TestCase):
    def test_counts_only_sleep(self):
        clock = {"awake": 100.0, "total": 1000.0}
        with mock.patch.object(vila, "awake_time", lambda: clock["awake"]), \
                mock.patch.object(vila, "total_time", lambda: clock["total"]):
            m = vila.SleepMeter()
            clock["awake"] += 30
            clock["total"] += 30
            self.assertEqual(m.slept(), 0.0)  # vaken hela tiden
            clock["awake"] += 60
            clock["total"] += 60 + 840  # 14 minuter i viloläge
            self.assertAlmostEqual(m.slept(), 840)
            self.assertAlmostEqual(m.awake(), 90)

    def test_real_clocks_run(self):
        m = vila.SleepMeter()
        self.assertGreaterEqual(m.awake(), 0)
        self.assertEqual(m.slept(), 0.0)


if __name__ == "__main__":
    unittest.main()
