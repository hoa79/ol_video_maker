"""Tester för pulsen från klockan: .fit-läsaren, förslaget på starten och pulsbandet i videon."""
import os
import shutil
import struct
import tempfile
import unittest
import zipfile

from ol_video_maker.core import Job, MediaInfo, VideoMakerError, build_plan, render
from ol_video_maker.fitfile import FIT_EPOCH, HeartRate, load_fit, read_heart_rate, write_fit
from ol_video_maker.pulse_video import PulseTrack

from test_render import HAVE_FFMPEG, make_clip, pixel

T0 = 1795888000  # någon gång i november 2026


def read(path, mode="rb"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def fit_bytes(body: bytes) -> bytes:
    return struct.pack("<BBHI4s", 12, 0x10, 2132, len(body), b".FIT") + body + b"\0\0"


def race(seconds=1800, start=600, rest=75, run=155, step=1):
    """Vila i `start` sekunder och sedan löpning: pulsen stiger under en minut."""
    out = []
    for s in range(0, seconds, step):
        if s < start:
            hr = rest + (s % 7) - 3
        else:
            hr = min(run, rest + (s - start) * (run - rest) / 60) + (s % 5) - 2
        out.append((T0 + s, int(round(hr))))
    return out


class FitReading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="olvm_fit_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_round_trip(self):
        path = os.path.join(self.dir, "lopp.fit")
        write_fit(path, race(600, 300))
        hr = load_fit(path)
        self.assertEqual(hr.start, T0)
        self.assertEqual(len(hr.times), 600)
        self.assertEqual(hr.name, "lopp.fit")
        self.assertAlmostEqual(hr.at(T0 + 10.5), (hr.bpm[10] + hr.bpm[11]) / 2)
        self.assertIsNone(hr.at(T0 - 1))

    def test_zip_from_garmin_connect(self):
        fit = os.path.join(self.dir, "123_ACTIVITY.fit")
        write_fit(fit, race(300, 100))
        z = os.path.join(self.dir, "123.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.write(fit, "123_ACTIVITY.fit")
        hr = load_fit(z)
        self.assertEqual(hr.name, "123_ACTIVITY.fit")
        self.assertEqual(len(hr.bpm), 300)

    def test_garmin_details(self):
        """Komprimerade tidsstämplar, utvecklarfält, big endian och andra meddelanden."""
        body = bytearray()
        # Lokalt meddelande 1: file_id (0) med ett enum-fält, ska ignoreras.
        body += bytes([0x41, 0, 0]) + struct.pack("<H", 0) + bytes([1, 0, 1, 0x00])
        body += bytes([0x01, 4])
        # Lokalt meddelande 0: record, big endian, timestamp + heart_rate + ett utvecklarfält.
        body += bytes([0x60, 0, 1]) + struct.pack(">H", 20) + bytes([2])
        body += bytes([253, 4, 0x86, 3, 1, 0x02]) + bytes([1, 7, 2, 0])  # 1 utvecklarfält, 2 byte
        ts = T0 - FIT_EPOCH
        body += bytes([0x00]) + struct.pack(">IB", ts, 100) + b"\xAA\xBB"
        # Lokalt meddelande 2: record med bara puls, för komprimerade tidsstämplar.
        body += bytes([0x42, 0, 0]) + struct.pack("<H", 20) + bytes([1, 3, 1, 0x02])
        for i in range(1, 40):  # tidsförskjutningen (5 bitar) slår runt efter 32 s
            off = (ts + i) & 0x1F
            body += bytes([0x80 | (2 << 5) | off, 100 + i])
        body += bytes([0x02, 0xFF])  # ogiltig puls i ett vanligt meddelande hoppas över
        samples = read_heart_rate(fit_bytes(bytes(body)))
        self.assertEqual(samples[0], (T0, 100))
        self.assertEqual(samples[-1], (T0 + 39, 139))
        self.assertEqual(len(samples), 40)

    def test_chained_files_and_truncated_end(self):
        a = race(100, 50)[:50]
        b = race(100, 50)[50:]
        pa, pb = os.path.join(self.dir, "a.fit"), os.path.join(self.dir, "b.fit")
        write_fit(pa, a)
        write_fit(pb, b)
        data = read(pa) + read(pb)
        self.assertEqual(len(read_heart_rate(data)), 100)
        # Avklippt mitt i ett meddelande: det som finns läses ändå.
        self.assertEqual(len(read_heart_rate(data[:-20])), 97)  # 3 poster à 6 byte saknas

    def test_bad_files(self):
        bad = os.path.join(self.dir, "film.fit")
        with open(bad, "wb") as f:
            f.write(b"\0\0\0\x18ftypmp42" + b"x" * 100)
        with self.assertRaises(VideoMakerError) as cm:
            load_fit(bad)
        self.assertIn("film.fit", str(cm.exception))
        empty = os.path.join(self.dir, "utan_puls.fit")
        write_fit(empty, [])
        with self.assertRaises(VideoMakerError) as cm:
            load_fit(empty)
        self.assertIn("varken puls eller GPS", str(cm.exception))

    def test_gap_breaks_curve(self):
        hr = HeartRate([T0, T0 + 1, T0 + 200, T0 + 201], [100, 100, 150, 150])
        self.assertIsNone(hr.at(T0 + 100))
        self.assertEqual(hr.window(T0, T0 + 1), (100, 100))


class StartSuggestion(unittest.TestCase):
    def test_finds_where_pulse_rises(self):
        for start in (300, 600, 1000):
            hr = HeartRate(*map(list, zip(*race(1800, start))))
            self.assertLess(abs(hr.suggest_start() - (T0 + start)), 45, start)

    def test_ten_second_samples(self):
        # Klockor i "smart inspelning" sparar glesare, t.ex. var 10:e sekund.
        hr = HeartRate(*map(list, zip(*race(2400, 900, step=10))))
        self.assertLess(abs(hr.suggest_start() - (T0 + 900)), 60)


class PulsePlan(unittest.TestCase):
    def test_command_has_pulse_band(self):
        hc = [MediaInfo("GH010001.MP4", 600.0, 1920, 1080, True)]
        ll = MediaInfo("livelox.mp4", 600.0, 1280, 720, False)
        hr = HeartRate(*map(list, zip(*race(1800, 300))))
        tmp = tempfile.mkdtemp(prefix="olvm_test_")
        try:
            job = Job(["GH010001.MP4"], "livelox.mp4", "ut.mp4", headcam_sync=60, livelox_sync=0,
                      end_time=500, tail_seconds=20,
                      pulse=PulseTrack(hr, offset=T0 + 300 - 60, graph_start=55, graph_end=500))
            plan = build_plan(job, hc, ll, "libx264", font=None, tmpdir=tmp)
            fg = plan.cmd[plan.cmd.index("-filter_complex") + 1]
            self.assertTrue(plan.pulse_drawn)
            self.assertIn("sendcmd=f=", fg)
            # Headcam får plats ovanför bandet (1080 - 180 = 900).
            self.assertIn("scale=1280:900", fg)
            # Kurvan visar hela loppet även i en provbit från slutet: 480-55 s av 445 s.
            self.assertIn("clip((425.000+t)/445.000,0,1)", fg)
            cmds = read(os.path.join(tmp, "puls_kommandon.txt"), "r")
            self.assertIn("overlay@phb", cmds)    # hjärtslag
            self.assertIn("drawtext@pbpm", cmds)  # siffran byts
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class PulseRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="olvm_test_")
        cls.hc = os.path.join(cls.dir, "GH010001.MP4")
        make_clip(cls.hc, 8, "1920x1080", "color=c=red")
        cls.hc43 = os.path.join(cls.dir, "hc43.mp4")
        make_clip(cls.hc43, 4, "1440x1080", "color=c=red", audio=False)
        cls.ll = os.path.join(cls.dir, "livelox.mp4")
        make_clip(cls.ll, 8, "1280x720", "color=c=blue", audio=False)
        # Pulsen stiger kraftigt 2 s in i filmen och håller sig hög.
        cls.hr = HeartRate([T0 + s for s in range(30)], [80, 80] + [170] * 28)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_band_under_headcam(self):
        out = os.path.join(self.dir, "puls.mp4")
        job = Job([self.hc], self.ll, out, headcam_sync=1, livelox_sync=0, lead_in=1,
                  pulse=PulseTrack(self.hr, offset=T0))
        render(job)
        # Headcam och Livelox som vanligt, ingen svart rand över dem.
        r, g, b = pixel(out, 4, 640, 540)
        self.assertTrue(r > 180 and b < 80, f"headcam-ytan är inte röd: {(r, g, b)}")
        r, g, b = pixel(out, 4, 640, 890)
        self.assertTrue(r > 180 and b < 80, f"pulsbandet täcker headcam: {(r, g, b)}")
        r, g, b = pixel(out, 4, 1600, 540)
        self.assertTrue(b > 180 and r < 80, f"Livelox-ytan är inte blå: {(r, g, b)}")
        # Kurvan längst till höger i bandet: grå i början, röd (sprungen) i slutet.
        x = 1000
        col = [pixel(out, 7.8, x, y) for y in range(930, 1066, 4)]
        self.assertTrue(any(p[0] > 150 and p[1] < 120 for p in col), "röd kurva saknas i slutet")
        col = [pixel(out, 0.3, x, y) for y in range(930, 1066, 4)]
        self.assertFalse(any(p[0] > 150 and p[1] < 120 for p in col), "kurvan är röd för tidigt")
        self.assertTrue(any(p[0] > 60 and abs(p[0] - p[1]) < 30 for p in col), "grå kurva saknas")

    def test_4_3_headcam_moves_up(self):
        out = os.path.join(self.dir, "puls43.mp4")
        render(Job([self.hc43], self.ll, out, lead_in=0, pulse=PulseTrack(self.hr, offset=T0)))
        r, g, b = pixel(out, 1, 640, 20)
        self.assertTrue(r > 180, f"4:3-bilden ska börja högst upp: {(r, g, b)}")
        r, g, b = pixel(out, 1, 165, 1070)
        self.assertFalse(r > 180 and g < 80 and b < 80, "headcam ligger under pulsbandet")


if __name__ == "__main__":
    unittest.main()
