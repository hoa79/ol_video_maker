"""Tester för höjd och fart: läsningen ur .fit, färgskalan och bandet i videon."""
import os
import shutil
import struct
import tempfile
import unittest

from ol_video_maker.core import Job, MediaInfo, VideoMakerError, build_plan, render
from ol_video_maker.fitfile import (FIT_EPOCH, HeartRate, Terrain, load_fit, pace_text,
                                    read_samples, write_fit)
from ol_video_maker.pulse_video import PulseTrack
from ol_video_maker.terrain_video import (PERIODS, TerrainTrack, speed_color, speed_level)

from test_pulse import T0, fit_bytes, read
from test_render import HAVE_FFMPEG, make_clip, pixel


def hilly(seconds=600):
    """Upp för en backe (långsamt) och sedan ner (snabbt)."""
    hr, ter = [], []
    for s in range(seconds):
        up = s < seconds // 2
        alt = 20 + (s if up else seconds - s) * 0.1
        speed = 2.0 if up else 4.5
        hr.append((T0 + s, 150))
        ter.append((alt, speed))
    return hr, ter


class TerrainReading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="olvm_ter_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_round_trip(self):
        path = os.path.join(self.dir, "lopp.fit")
        hr, ter = hilly()
        write_fit(path, hr, ter)
        data = load_fit(path)
        self.assertIsNotNone(data.terrain)
        alt, speed = data.terrain.at(T0 + 100)
        self.assertAlmostEqual(alt, 30.0, delta=0.6)
        self.assertAlmostEqual(speed, 2.0, delta=0.01)
        alt, speed = data.terrain.at(T0 + 500)
        self.assertAlmostEqual(speed, 4.5, delta=0.01)
        self.assertIsNone(data.terrain.at(T0 - 5))

    def test_pulse_only_file_has_no_terrain(self):
        path = os.path.join(self.dir, "puls.fit")
        write_fit(path, [(T0 + s, 140) for s in range(60)])
        self.assertIsNone(load_fit(path).terrain)

    def test_plain_altitude_and_speed_fields(self):
        # Äldre klockor: altitude (fält 2) och speed (fält 6) som uint16.
        body = bytearray([0x40, 0, 0]) + struct.pack("<H", 20) + bytes([3])
        body += bytes([253, 4, 0x86, 2, 2, 0x84, 6, 2, 0x84])
        for s in range(20):
            body += bytes([0]) + struct.pack("<IHH", T0 + s - FIT_EPOCH, (45 + 500) * 5, 3250)
        smp = read_samples(fit_bytes(bytes(body)))
        self.assertEqual(len(smp), 20)
        self.assertAlmostEqual(smp[0].alt, 45.0)
        self.assertAlmostEqual(smp[0].speed, 3.25)
        self.assertIsNone(smp[0].hr)

    def test_window_ignores_stops(self):
        times = [float(T0 + s) for s in range(100)]
        speed = [3.0] * 100
        speed[50] = 0.0   # stannar vid en kontroll
        speed[51] = 9.0   # GPS-hopp
        ter = Terrain(times, [10.0] * 100, speed)
        _, _, lo, hi = ter.window(times[0], times[-1])
        self.assertEqual((lo, hi), (3.0, 3.0))

    def test_pace_text(self):
        self.assertEqual(pace_text(1000 / 318), "5:18")
        self.assertEqual(pace_text(1000 / 600), "10:00")
        self.assertEqual(pace_text(0.1), "–")
        self.assertEqual(pace_text(None), "–")


class Colors(unittest.TestCase):
    def test_blue_to_red(self):
        r, g, b = speed_color(1.0, 1.0, 5.0)
        self.assertTrue(b > 200 and r < 100, (r, g, b))      # långsamt = blått
        r, g, b = speed_color(5.0, 1.0, 5.0)
        self.assertTrue(r > 200 and b < 100, (r, g, b))      # snabbt = rött
        r, g, b = speed_color(3.0, 1.0, 5.0)
        self.assertTrue(g > 200, (r, g, b))                  # mitt emellan = grönt

    def test_levels(self):
        self.assertEqual(speed_level(0.5, 1.0, 5.0), 0)
        self.assertEqual(speed_level(5.0, 1.0, 5.0), len(PERIODS) - 1)
        self.assertGreater(PERIODS[0], PERIODS[-1])          # snabbare fart = kortare steg


class TerrainPlan(unittest.TestCase):
    def setUp(self):
        hr, ter = hilly(1800)
        times = [float(t) for t, _ in hr]
        self.hr = HeartRate(times, [v for _, v in hr], "lopp.fit",
                            Terrain(times, [a for a, _ in ter], [s for _, s in ter]))
        self.hc = [MediaInfo("GH010001.MP4", 600.0, 1920, 1080, True)]
        self.ll = MediaInfo("livelox.mp4", 600.0, 1280, 720, False)

    def test_command_has_terrain_band(self):
        tmp = tempfile.mkdtemp(prefix="olvm_test_")
        try:
            job = Job(["GH010001.MP4"], "livelox.mp4", "ut.mp4", headcam_sync=60, end_time=500,
                      tail_seconds=20,
                      terrain=TerrainTrack(self.hr, offset=T0 - 60, graph_start=55, graph_end=500))
            plan = build_plan(job, self.hc, self.ll, "libx264", font="/f.ttf", tmpdir=tmp)
            fg = plan.cmd[plan.cmd.index("-filter_complex") + 1]
            self.assertTrue(plan.terrain_drawn)
            self.assertIn("scale=1280:900", fg)            # headcam ovanför bandet
            self.assertIn("clip((425.000+t)/445.000,0,1)", fg)
            for lv in range(len(PERIODS)):
                self.assertIn(f"[trun{lv}]", fg)
            self.assertIn(f"streamselect@tsel=inputs={len(PERIODS)}", fg)
            self.assertEqual(fg.count("overlay@trun"), 1)  # en enda löpare i videon
            self.assertIn("min/km", fg)
            self.assertIn("långsamt", fg)
            cmds = read(os.path.join(tmp, "hojd_kommandon.txt"), "r")
            self.assertIn("drawtext@tpace reinit text=", cmds)
            self.assertIn("\\\\:", cmds)                   # kolonet i 5:18 är skyddat
            self.assertIn("overlay@tptr x", cmds)
            self.assertEqual(len([f for f in os.listdir(os.path.join(tmp, "lopare"))]), 12)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_only_one_thing_under_video(self):
        job = Job(["GH010001.MP4"], "livelox.mp4", "ut.mp4",
                  pulse=PulseTrack(self.hr, offset=T0), terrain=TerrainTrack(self.hr, offset=T0))
        with self.assertRaises(VideoMakerError):
            build_plan(job, self.hc, self.ll, "libx264")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class TerrainRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="olvm_test_")
        cls.hc = os.path.join(cls.dir, "GH010001.MP4")
        make_clip(cls.hc, 8, "1920x1080", "color=c=red")
        cls.ll = os.path.join(cls.dir, "livelox.mp4")
        make_clip(cls.ll, 8, "1280x720", "color=c=blue", audio=False)
        times = [float(T0 + s) for s in range(30)]
        # Första 4 s långsamt uppför, sedan snabbt nedför.
        alts = [20 + (s if s < 4 else 8 - s) * 2.0 for s in range(30)]
        speeds = [1.8 if s < 4 else 4.8 for s in range(30)]
        cls.hr = HeartRate(times, [150] * 30, "t.fit", Terrain(times, alts, speeds))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_band_colours(self):
        out = os.path.join(self.dir, "hojd.mp4")
        render(Job([self.hc], self.ll, out, lead_in=0, terrain=TerrainTrack(self.hr, offset=T0)))
        r, g, b = pixel(out, 4, 640, 540)
        self.assertTrue(r > 180 and b < 80, f"headcam-ytan är inte röd: {(r, g, b)}")
        r, g, b = pixel(out, 4, 640, 890)
        self.assertTrue(r > 180 and b < 80, f"bandet täcker headcam: {(r, g, b)}")
        # Längst ner i kurvan: tidigt i filmen grått, i slutet färgat. Vänster del (långsam)
        # är blå, högra delen (snabb) röd.
        y = 1055
        early = pixel(out, 0.3, 1000, y)
        self.assertTrue(abs(early[0] - early[2]) < 25 and early[0] < 90, f"inte grått: {early}")
        slow = pixel(out, 7.8, 260, y)
        self.assertTrue(slow[2] > 150 and slow[0] < 100, f"långsam del inte blå: {slow}")
        fast = pixel(out, 7.8, 1000, y)
        self.assertTrue(fast[0] > 150 and fast[2] < 100, f"snabb del inte röd: {fast}")


if __name__ == "__main__":
    unittest.main()
