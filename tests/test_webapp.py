"""Tester för fönstrets lokala webbserver (webapp.py): skydd, läge, bilder och markeringar."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from ol_video_maker import villkor
from ol_video_maker.webapp import Dialogs, Server, State

from test_render import HAVE_FFMPEG, make_clip


class WebAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_data_dir = os.environ.get("OLVM_DATA_DIR")
        os.environ["OLVM_DATA_DIR"] = tempfile.mkdtemp()  # aldrig användarens riktiga mapp
        cls.state = State(Dialogs())
        cls.server = Server(cls.state)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        if cls.accept_first:
            cls.post(cls, "accept_terms", {"version": villkor.VERSION})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        if cls.old_data_dir is None:
            os.environ.pop("OLVM_DATA_DIR", None)
        else:
            os.environ["OLVM_DATA_DIR"] = cls.old_data_dir

    accept_first = True

    def get(self, path, token=True):
        sep = "&" if "?" in path else "?"
        url = self.base + path + (f"{sep}k={self.server.token}" if token else "")
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def post(self, name, body, token=True):
        req = urllib.request.Request(self.base + "/api/" + name, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        if token:
            req.add_header("X-OLVM-Token", self.server.token)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def test_needs_token(self):
        for path in ("/", "/api/state", "/frame?src=hc&t=0"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path, token=False)
            self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError):
            self.post("mark", {"what": "hc", "t": 1}, token=False)

    def test_wrong_host_refused(self):
        req = urllib.request.Request(f"{self.base}/api/state?k={self.server.token}",
                                     headers={"Host": "evil.example:80"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)

    def test_page_and_static(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"OL Video Maker", body)
        for name in ("app.css", "app.js"):
            status, _, _ = self.get("/static/" + name, token=False)
            self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/static/../webapp.py", token=False)

    def test_state(self):
        _, ctype, body = self.get("/api/state")
        self.assertIn("application/json", ctype)
        s = json.loads(body)
        self.assertIn("hc", s)
        self.assertIn("liveloxWidth", s)

    def test_bye(self):
        req = urllib.request.Request(f"{self.base}/api/bye?k={self.server.token}", data=b"",
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 204)
        self.assertTrue(self.server.closed)
        self.get("/api/state")  # sidan hörs av igen (t.ex. omladdad)
        self.assertFalse(self.server.closed)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg saknas")
    def test_load_mark_and_frame(self):
        folder = tempfile.mkdtemp()
        hc, ll = os.path.join(folder, "hc.mp4"), os.path.join(folder, "ll.mp4")
        make_clip(hc, 4, "320x180")
        make_clip(ll, 4, "320x180")
        self.post("load", {"which": "headcam", "files": [hc]})
        self.post("load", {"which": "livelox", "files": [ll]})
        for _ in range(100):
            s = json.loads(self.get("/api/state")[2])
            if s["hc"] and s["ll"]:
                break
            threading.Event().wait(0.1)
        self.assertAlmostEqual(s["hc"]["duration"], 4, delta=0.2)
        status, ctype, data = self.get("/frame?src=hc&t=1.5&w=320&h=180")
        self.assertEqual(ctype, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8"))
        s = self.post("mark", {"what": "hc", "t": 2.0})["state"]
        self.assertEqual(s["hcSync"], 2.0)
        # Slutet får inte ligga före starten.
        r = self.post("mark", {"what": "end", "t": 1.0})
        self.assertIn("error", r)
        self.assertIsNone(r["state"]["endTime"])
        s = self.post("set_width", {"width": 707})["state"]
        self.assertEqual(s["liveloxWidth"], 700)

    def test_terrain_from_fit(self):
        from ol_video_maker.fitfile import write_fit
        folder = tempfile.mkdtemp()
        fit = os.path.join(folder, "lopp.fit")
        t0 = 1795888000
        write_fit(fit, [(t0 + s, 150) for s in range(120)],
                  [(30 + s * 0.2, 2.0 + (s % 60) / 30) for s in range(120)])
        self.post("set_under", {"what": "terrain"})
        self.post("load_fit", {"file": fit})
        for _ in range(100):
            s = json.loads(self.get("/api/state")[2])
            if s["hr"]:
                break
            threading.Event().wait(0.1)
        self.assertEqual(s["under"], "terrain")   # valet står kvar när filen har höjd och fart
        ter = s["hr"]["terrain"]
        self.assertEqual(ter["altMin"], 30)
        self.assertAlmostEqual(ter["altMax"], 54, delta=1.5)   # utjämnad
        self.assertEqual(len(ter["profile"]), 120)
        self.assertTrue(ter["profile"][0][1].startswith("#"))
        data = json.loads(self.get("/api/terrain")[2])
        self.assertEqual(len(data["alt"]), len(data["speed"]))
        self.assertFalse(s["terrainOn"])          # inte synkat än
        # Synken följer valet: farten vid höjd och fart, pulsen vid puls.
        sync = json.loads(self.get("/api/sync")[2])
        self.assertEqual(sync["kind"], "speed")
        self.assertLessEqual(sync["slow"], sync["fast"])
        self.post("set_under", {"what": "pulse"})
        sync = json.loads(self.get("/api/sync")[2])
        self.assertEqual(sync["kind"], "pulse")
        self.assertEqual(sync["values"][0], 150)
        self.post("remove_fit", {})
        self.post("set_under", {"what": "none"})

    def test_terrain_when_fit_has_no_pulse(self):
        from ol_video_maker.fitfile import write_fit
        folder = tempfile.mkdtemp()
        fit = os.path.join(folder, "gps.fit")
        t0 = 1795888000
        write_fit(fit, [(t0 + s, 0) for s in range(120)],
                  [(30 + s * 0.2, 2.0 + (s % 60) / 30) for s in range(120)])
        self.post("set_under", {"what": "pulse"})
        self.post("load_fit", {"file": fit})
        for _ in range(100):
            s = json.loads(self.get("/api/state")[2])
            if s["hr"]:
                break
            threading.Event().wait(0.1)
        self.assertFalse(s["hr"]["hasPulse"])
        self.assertIsNone(s["hr"]["min"])
        self.assertEqual(s["under"], "terrain")   # filen saknar puls: höjd och fart väljs
        self.assertEqual(json.loads(self.get("/api/sync")[2])["kind"], "speed")
        self.post("set_under", {"what": "pulse"})
        self.post("mark", {"what": "fit", "t": t0 + 10})
        s = json.loads(self.get("/api/state")[2])
        self.assertFalse(s["pulse"])              # ingen puls att visa
        self.assertEqual(json.loads(self.get("/api/sync")[2]), {})
        self.post("remove_fit", {})
        self.post("set_under", {"what": "none"})


class VillkorTest(WebAppTest):
    """Första start: villkoren måste godkännas innan något annat går att göra."""
    accept_first = False

    def post_status(self, name, body):
        try:
            return 200, self.post(name, body)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_terms_flow(self):
        s = json.loads(self.get("/api/state")[2])
        self.assertFalse(s["termsAccepted"])
        self.assertEqual(s["terms"]["version"], villkor.VERSION)
        self.assertTrue(s["terms"]["points"])
        # Inget annat går att göra innan godkännandet.
        code, r = self.post_status("set_width", {"width": 700})
        self.assertEqual(code, 403)
        self.assertIn("error", r)
        # Fel version (gammal sida) godkänner inte.
        code, r = self.post_status("accept_terms", {"version": villkor.VERSION - 1})
        self.assertIn("error", r)
        self.assertFalse(r["state"]["termsAccepted"])
        r = self.post("accept_terms", {"version": villkor.VERSION})
        self.assertTrue(r["state"]["termsAccepted"])
        self.assertIsNone(r["state"]["terms"])
        self.assertTrue(villkor.is_accepted())
        self.assertNotIn("error", self.post("set_width", {"width": 700}))
        # Nästa start minns godkännandet ...
        self.assertTrue(State(Dialogs()).terms_accepted)
        # ... men en ny version av texten måste godkännas igen.
        old = villkor.VERSION
        try:
            villkor.VERSION = old + 1
            self.assertFalse(State(Dialogs()).terms_accepted)
        finally:
            villkor.VERSION = old

    def test_decline_saves_nothing(self):
        folder = tempfile.mkdtemp()
        old = os.environ["OLVM_DATA_DIR"]
        os.environ["OLVM_DATA_DIR"] = folder
        try:
            state = State(Dialogs())
            state.decline_terms({})
            self.assertTrue(state.quit_requested)
            self.assertFalse(state.terms_accepted)
            self.assertEqual(os.listdir(folder), [])
        finally:
            os.environ["OLVM_DATA_DIR"] = old

    def test_broken_file_means_not_accepted(self):
        path = os.path.join(villkor.data_dir(), villkor.FILE_NAME)
        os.makedirs(villkor.data_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[trasig")
        self.assertFalse(villkor.is_accepted())

    def test_villkor_md_up_to_date(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "VILLKOR.md")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), villkor.as_markdown(),
                             "Kör: python -m ol_video_maker.villkor > docs/VILLKOR.md")

    # Testerna i WebAppTest kräver godkännande; de körs redan där.
    test_load_mark_and_frame = None


if __name__ == "__main__":
    unittest.main()
