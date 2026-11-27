import os
import unittest

from ol_video_maker.filedialogs import parse_multi, win_filter


class FileDialogTest(unittest.TestCase):
    def test_filter(self):
        self.assertEqual(win_filter([("Video", "*.mp4 *.MOV"), ("Alla", "*.*")]),
                         "Video\0*.mp4;*.MOV\0Alla\0*.*\0\0")

    def test_one_file(self):
        self.assertEqual(parse_multi("C:\\film\\GS010001.MP4"), ["C:\\film\\GS010001.MP4"])

    def test_several_files(self):
        got = parse_multi("C:\\film\0GS010001.MP4\0GS020001.MP4")
        self.assertEqual(got, [os.path.join("C:\\film", "GS010001.MP4"),
                               os.path.join("C:\\film", "GS020001.MP4")])

    @unittest.skipUnless(os.name == "nt", "bara Windows (DWORD är 4 byte där)")
    def test_struct_size(self):
        import ctypes
        from ol_video_maker.filedialogs import _ofn_struct
        want = 152 if ctypes.sizeof(ctypes.c_void_p) == 8 else 88
        self.assertEqual(ctypes.sizeof(_ofn_struct()), want)

    def test_cancel(self):
        self.assertEqual(parse_multi(""), [])


if __name__ == "__main__":
    unittest.main()
