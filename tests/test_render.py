"""Tester med syntetiska klipp som skapas med ffmpeg (inga riktiga GoPro-filer behövs)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ol_video_maker.core import (Job, VideoMakerError, ensure_mp4, explain_ffmpeg_error,
                                 parse_time, probe, render, same_file, sort_headcam_files)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def make_clip(path, seconds, size, color_src="testsrc2", audio=True, rate=30):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"{color_src}{':' if '=' in color_src else '='}size={size}:rate={rate}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(path)
    subprocess.run(cmd, check=True)


def streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def pixel(path, t, x, y):
    """RGB för en pixel i en bildruta."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
                          "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


class TimeParsing(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("75"), 75)
        self.assertEqual(parse_time("02:15"), 135)
        self.assertEqual(parse_time("1:02:03"), 3723)
        self.assertEqual(parse_time("0:05,5"), 5.5)
        with self.assertRaises(VideoMakerError):
            parse_time("abc")
        with self.assertRaises(VideoMakerError):
            parse_time("1:75")
        with self.assertRaises(VideoMakerError):
            parse_time("1:75:00")


class Helpers(unittest.TestCase):
    def test_gopro_sorting(self):
        files = ["GH020123.MP4", "GH010124.MP4", "GH010123.MP4"]
        ordered, mixed = sort_headcam_files(files)
        self.assertEqual(ordered, ["GH010123.MP4", "GH020123.MP4", "GH010124.MP4"])
        self.assertTrue(mixed)
        ordered, mixed = sort_headcam_files(["d/GX030555.MP4", "d/GX010555.MP4", "d/GX020555.MP4"])
        self.assertEqual([os.path.basename(f) for f in ordered],
                         ["GX010555.MP4", "GX020555.MP4", "GX030555.MP4"])
        self.assertFalse(mixed)
        ordered, mixed = sort_headcam_files(["GP010777.MP4", "GOPR0777.MP4"])
        self.assertEqual(ordered, ["GOPR0777.MP4", "GP010777.MP4"])
        self.assertFalse(mixed)
        ordered, mixed = sort_headcam_files(["b.mp4", "A.mp4"])
        self.assertEqual(ordered, ["A.mp4", "b.mp4"])

    def test_ensure_mp4(self):
        self.assertEqual(ensure_mp4("ut"), "ut.mp4")
        self.assertEqual(ensure_mp4("ut.MP4"), "ut.MP4")
        self.assertEqual(ensure_mp4("ut.mov"), "ut.mov.mp4")

    def test_same_file_case(self):
        self.assertTrue(same_file("a/../GH010123.MP4", "GH010123.MP4"))
        if os.name == "nt":
            self.assertTrue(same_file("GH010123.MP4", "gh010123.mp4"))

    def test_swedish_errors(self):
        self.assertIn("Stäng videon", explain_ffmpeg_error(["x: Permission denied"]))
        self.assertIn("Disken är full", explain_ffmpeg_error(["No space left on device"]))
        self.assertIn(".mp4", explain_ffmpeg_error(["Unable to choose an output format for 'ut'"]))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="olvm_test_")
        d = cls.dir
        # Headcam: röd 16:9 (GoPro-liknande), två "kapitel"
        cls.hc1 = os.path.join(d, "GH010001.MP4")
        cls.hc2 = os.path.join(d, "GH020001.MP4")
        make_clip(cls.hc1, 6, "1920x1080", "color=c=red", rate=60)
        make_clip(cls.hc2, 4, "1920x1080", "color=c=red", rate=60)
        # Headcam 4:3 utan ljud
        cls.hc43 = os.path.join(d, "hc43.mp4")
        make_clip(cls.hc43, 5, "1440x1080", "color=c=red", audio=False)
        # Livelox: blå, 16:9 skärminspelning utan ljud
        cls.ll = os.path.join(d, "livelox.mp4")
        make_clip(cls.ll, 8, "1280x720", "color=c=blue", audio=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def check_output(self, out, expected_duration):
        info = streams(out)
        v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        a = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
        self.assertEqual((v["width"], v["height"]), (1920, 1080))
        self.assertEqual(v["codec_name"], "h264")
        self.assertEqual(v["pix_fmt"], "yuv420p")
        self.assertEqual(a["codec_name"], "aac")
        self.assertAlmostEqual(float(info["format"]["duration"]), expected_duration, delta=0.3)

    def assert_layout(self, out, t):
        # Mitten av headcam-rutan (vänster) ska vara röd, Livelox-kolumnen (höger) blå.
        r, g, b = pixel(out, t, 640, 540)
        self.assertTrue(r > 180 and b < 80, f"headcam-ytan är inte röd: {(r, g, b)}")
        r, g, b = pixel(out, t, 1600, 540)
        self.assertTrue(b > 180 and r < 80, f"Livelox-ytan är inte blå: {(r, g, b)}")
        # Kanten precis vänster om Livelox-kolumnen ska fortfarande vara headcam (röd) eller svart,
        # aldrig blå: Livelox får inte täcka headcam.
        r, g, b = pixel(out, t, 1279, 540)
        self.assertFalse(b > 180 and r < 80, "Livelox täcker headcam")

    def test_single_file(self):
        out = os.path.join(self.dir, "out1.mp4")
        render(Job([self.hc1], self.ll, out, headcam_sync=3, livelox_sync=1, lead_in=2))
        self.check_output(out, 5.0)  # 6 - (3-2)
        self.assert_layout(out, 2)

    def test_multiple_chapters_and_end(self):
        out = os.path.join(self.dir, "out2.mp4")
        render(Job([self.hc1, self.hc2], self.ll, out, headcam_sync=2, livelox_sync=0,
                   lead_in=0, end_time=9))
        self.check_output(out, 7.0)
        self.assert_layout(out, 5)  # efter kapitelbytet

    def test_livelox_starts_later_and_no_audio(self):
        # Livelox-synkpunkten är tidigare än lead-in: Livelox fördröjs (svart först).
        out = os.path.join(self.dir, "out3.mp4")
        render(Job([self.hc43], self.ll, out, headcam_sync=3, livelox_sync=0, lead_in=3))
        self.check_output(out, 5.0)
        r, g, b = pixel(out, 0.5, 1600, 540)
        self.assertTrue(b < 60, "Livelox ska inte synas före sin start")
        self.assert_layout(out, 4)

    def test_progress_and_bad_sync(self):
        out = os.path.join(self.dir, "out4.mp4")
        seen = []
        render(Job([self.hc1], self.ll, out, headcam_sync=0, lead_in=0), progress=seen.append)
        self.assertEqual(seen[-1], 1.0)
        with self.assertRaises(VideoMakerError):
            render(Job([self.hc1], self.ll, out, headcam_sync=99))

    def test_output_without_extension(self):
        out = os.path.join(self.dir, "utan_andelse")
        path = render(Job([self.hc1], self.ll, out, headcam_sync=0, lead_in=0, end_time=1))
        self.assertTrue(path.endswith(".mp4") and os.path.isfile(path))

    def test_tail_preview(self):
        out = os.path.join(self.dir, "tail.mp4")
        render(Job([self.hc1, self.hc2], self.ll, out, headcam_sync=1, livelox_sync=0,
                   tail_seconds=3))
        self.check_output(out, 3.0)

    def test_livelox_ended_shows_last_map(self):
        # Livelox (8 s) har tagit slut när videon börjar: sista kartbilden ska synas.
        out = os.path.join(self.dir, "ll_ended.mp4")
        render(Job([self.hc1, self.hc2], self.ll, out, headcam_sync=0, livelox_sync=0,
                   tail_seconds=1.5))
        self.assert_layout(out, 0.5)

    def test_seek_uses_only_needed_chapters(self):
        # Start i kapitel 2: bara kapitel 2 ska läsas, med -ss räknat från kapitlets början.
        from ol_video_maker.core import MediaInfo, build_plan
        infos = [MediaInfo(p, d, 1920, 1080, True) for p, d in
                 (("GH010001.MP4", 100.0), ("GH020001.MP4", 100.0), ("GH030001.MP4", 100.0))]
        ll = MediaInfo("livelox.mp4", 400.0, 1280, 720, False)
        plan = build_plan(Job([i.path for i in infos], ll.path, "ut.mp4", headcam_sync=150,
                              livelox_sync=150, lead_in=5, end_time=170), infos, ll, "libx264")
        inputs = [plan.cmd[i + 1] for i, a in enumerate(plan.cmd) if a == "-i"]
        self.assertEqual(inputs, ["GH020001.MP4", "livelox.mp4"])
        self.assertEqual(plan.cmd[plan.cmd.index("GH020001.MP4") - 2], "45.000")
        # Över en skarv: två kapitel, sammanfogade med concat-filtret.
        plan = build_plan(Job([i.path for i in infos], ll.path, "ut.mp4", headcam_sync=195,
                              livelox_sync=0, lead_in=5, end_time=210), infos, ll, "libx264")
        inputs = [plan.cmd[i + 1] for i, a in enumerate(plan.cmd) if a == "-i"]
        self.assertEqual(inputs, ["GH020001.MP4", "GH030001.MP4", "livelox.mp4"])
        self.assertIn("concat=n=2:v=1:a=1", plan.cmd[plan.cmd.index("-filter_complex") + 1])

    def test_chapter_join_shows_right_content(self):
        # Tre kapitel i olika färger: rött, grönt, rött. Klippet 5-9 s ska visa grönt 1-4 s.
        d = self.dir
        c1, c2, c3 = (os.path.join(d, f"GH0{i}0002.MP4") for i in (1, 2, 3))
        make_clip(c1, 4, "1920x1080", "color=c=red")
        make_clip(c2, 4, "1920x1080", "color=c=lime")
        make_clip(c3, 4, "1920x1080", "color=c=red")
        out = os.path.join(d, "join.mp4")
        render(Job([c1, c2, c3], self.ll, out, headcam_sync=5, livelox_sync=0, lead_in=2,
                   end_time=9))
        self.check_output(out, 6.0)  # 3-9 s
        self.assertGreater(pixel(out, 0.5, 640, 540)[0], 180)   # kapitel 1: rött
        self.assertGreater(pixel(out, 2.5, 640, 540)[1], 180)   # kapitel 2: grönt
        self.assertGreater(pixel(out, 5.5, 640, 540)[0], 180)   # kapitel 3: rött

    def test_join_files_with_different_formats(self):
        # Olika kameror: 1920x1080 60 fps 48 kHz stereo + 1280x720 30 fps 44,1 kHz mono.
        d = self.dir
        a = os.path.join(d, "kamera_a.mp4")
        b = os.path.join(d, "kamera_b.mp4")
        for path, size, rate, color, ar, layout in (
                (a, "1920x1080", 60, "red", 48000, "stereo"),
                (b, "1280x720", 30, "lime", 44100, "mono")):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate={rate}:duration=3",
                            "-f", "lavfi", "-i",
                            f"sine=frequency=440:sample_rate={ar}:duration=3,aformat=channel_layouts={layout}",
                            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", path], check=True)
        out = os.path.join(d, "mixed_formats.mp4")
        render(Job([a, b], self.ll, out, headcam_sync=1, livelox_sync=0, lead_in=0))
        self.check_output(out, 5.0)  # 1-6 s
        self.assertGreater(pixel(out, 0.5, 640, 540)[0], 180)  # kamera a: rött
        self.assertGreater(pixel(out, 3.5, 640, 540)[1], 180)  # kamera b: grönt
        self.assert_layout(out, 0.5)

    def test_refuses_to_overwrite_source(self):
        with self.assertRaises(VideoMakerError):
            render(Job([self.hc1], self.ll, self.hc1))

    def test_cancel_removes_partial_file(self):
        out = os.path.join(self.dir, "cancel.mp4")
        started = []
        with self.assertRaises(VideoMakerError) as cm:
            render(Job([self.hc1, self.hc2], self.ll, out), on_process=started.append,
                   cancel_flag=lambda: bool(started))
        self.assertEqual(str(cm.exception), "Avbrutet.")
        self.assertEqual(started[0].poll() is not None, True)
        self.assertFalse(os.path.exists(out))

    def test_credit_text_in_livelox_column(self):
        # Livelox fyller hela kolumnens höjd här, så texten hamnar över kartan längst ner.
        tall = os.path.join(self.dir, "tall.mp4")
        make_clip(tall, 3, "640x1080", "color=c=blue", audio=False)
        with_text = os.path.join(self.dir, "credit.mp4")
        render(Job([self.hc1], tall, with_text, end_time=2))
        if not __import__("ol_video_maker.core", fromlist=["x"]).find_font():
            self.skipTest("inget typsnitt")
        # Någonstans i textrutan längst ner i kolumnen ska det inte vara rent blått längre.
        changed = any(pixel(with_text, 1, x, 1080 - 40)[2] < 150 for x in range(1500, 1700, 10))
        self.assertTrue(changed, "källtexten syns inte")
        # Headcam-ytan bredvid är fortfarande röd.
        r, g, b = pixel(with_text, 1, 1200, 1080 - 40)
        self.assertFalse(r > 180 and g > 180, "texten hamnade över headcam")


    def test_flip_headcam_180(self):
        # Headcam: röd övre halva, grön nedre. Vänd 180° ska grönt hamna överst.
        src = os.path.join(self.dir, "upp_och_ner.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=1920x540:d=3,format=yuv420p[a];"
                        "color=c=green:s=1920x540:d=3,format=yuv420p[b];[a][b]vstack",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", src],
                       check=True)
        out = os.path.join(self.dir, "vand.mp4")
        render(Job([src], self.ll, out, lead_in=0, headcam_flip=True))
        r, g, b = pixel(out, 1, 640, 300)   # övre delen av headcam-rutan
        self.assertTrue(g > 100 and r < 80, f"övre delen är inte grön: {(r, g, b)}")
        r, g, b = pixel(out, 1, 640, 800)   # nedre delen
        self.assertTrue(r > 180 and g < 80, f"nedre delen är inte röd: {(r, g, b)}")

    def test_4k_output(self):
        out = os.path.join(self.dir, "ut4k.mp4")
        render(Job([self.hc43], self.ll, out, lead_in=0, end_time=1.5, scale=2))
        v = [x for x in streams(out)["streams"] if x["codec_type"] == "video"][0]
        self.assertEqual((v["width"], v["height"]), (3840, 2160))
        r, g, b = pixel(out, 0.5, 1280, 1080)  # headcam-rutan
        self.assertTrue(r > 180 and b < 80, f"headcam-ytan är inte röd: {(r, g, b)}")
        r, g, b = pixel(out, 0.5, 3200, 1080)  # Livelox-kolumnen
        self.assertTrue(b > 180 and r < 80, f"Livelox-ytan är inte blå: {(r, g, b)}")

    def test_hardware_failure_falls_back_to_cpu(self):
        # Om grafikkortet går fel mitt i körningen görs videon om med processorn.
        from unittest import mock
        from ol_video_maker import core
        out = os.path.join(self.dir, "reserv.mp4")
        logs = []
        with mock.patch.object(core, "choose_hwaccel", return_value="olvm_finns_inte"):
            render(Job([self.hc1], self.ll, out, lead_in=0, end_time=1.5), log=logs.append)
        self.check_output(out, 1.5)
        self.assertTrue(any("processorn" in l for l in logs), logs)
        self.assertTrue(any(l.startswith("Kodare: libx264") and "avkodning" not in l
                            for l in logs), logs)

    def test_no_hw_setting(self):
        from unittest import mock
        from ol_video_maker import core
        with mock.patch.dict(os.environ, {"OLVM_NO_HW": "1"}):
            self.assertIsNone(core.choose_hwaccel(probe(self.hc1)))
            self.assertNotIn(core.choose_encoder(), core.HW_ENCODERS)


class FrameRate(unittest.TestCase):
    """Halva bildfrekvensen (snabbare 4K): sätts direkt efter avkodningen."""

    def setUp(self):
        from ol_video_maker.core import MediaInfo
        self.infos = [MediaInfo(p, 100.0, 3840, 2160, True, "60000/1001", "hevc")
                      for p in ("GX010001.MP4", "GX020001.MP4")]
        self.ll = MediaInfo("livelox.mp4", 400.0, 640, 1080, False, "30/1")

    def plan(self, **kw):
        from ol_video_maker.core import build_plan
        job = Job([i.path for i in self.infos], self.ll.path, "ut.mp4", livelox_sync=0,
                  lead_in=0, scale=2, **kw)
        plan = build_plan(job, self.infos, self.ll, "libx264")
        return plan.cmd[plan.cmd.index("-filter_complex") + 1]

    def test_half_fps(self):
        from ol_video_maker.core import half_fps
        self.assertEqual(half_fps("60000/1001"), "30000/1001")
        self.assertEqual(half_fps("60/1"), "30/1")
        self.assertEqual(half_fps("50/1"), "25/1")
        self.assertEqual(half_fps("120000/1001"), "60000/1001")
        self.assertIsNone(half_fps("30000/1001"))
        self.assertIsNone(half_fps("25/1"))
        self.assertIsNone(half_fps("nonsens"))

    def test_one_file(self):
        fg = self.plan(headcam_sync=10, end_time=20, fps="30000/1001", headcam_flip=True)
        self.assertTrue(fg.startswith("[0:v:0]fps=30000/1001,hflip,vflip,scale="), fg[:80])
        self.assertNotIn("fps=", self.plan(headcam_sync=10, end_time=20))  # kamerans frekvens

    def test_chapters(self):
        fg = self.plan(headcam_sync=95, end_time=105, fps="30000/1001")
        self.assertIn("[0:v:0]fps=30000/1001,scale=", fg)
        self.assertIn("[1:v:0]fps=30000/1001,scale=", fg)
        self.assertIn("fps=30000/1001,format=yuv420p[hv1]", fg)

    def test_bad_fps(self):
        with self.assertRaises(VideoMakerError):
            self.plan(headcam_sync=10, end_time=20, fps="0/0")


class EncoderSettings(unittest.TestCase):
    def test_youtube_friendly(self):
        from ol_video_maker.core import HW_ENCODERS, encoder_args
        for enc in ("libx264",) + HW_ENCODERS:
            for k in (1, 2):
                args = encoder_args(enc, k)
                self.assertEqual(args[:2], ["-c:v", enc])
                self.assertIn("-pix_fmt", args)  # 8 bitar 4:2:0, som YouTube vill ha
                if enc != "libx264":
                    rate = args[args.index("-b:v") + 1]
                    self.assertEqual(rate, "12M" if k == 1 else "40M")


if __name__ == "__main__":
    unittest.main()
