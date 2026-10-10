"""Quick checks that every part still loads and draws. Run with

    python -m unittest discover -s tests

They need no Spotify, no sound and no network, and run before every release
(a broken release would reach everyone through the auto-update).
"""
import io
import os
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                       # noqa: E402
import updater                    # noqa: E402
import version                    # noqa: E402
from nowplaying import Track      # noqa: E402

with open(os.path.join(ROOT, "assets", "screenshot.png"), "rb") as f:
    ART = f.read()                # any picture works as album art


class FakeNowPlaying:
    """Stands in for the Windows media session."""

    def __init__(self, any_player=False):
        self.track = Track()
        self.error = ""

    def start(self):
        pass

    def stop(self):
        pass


class VisualiserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = main.NowPlaying, main.SETTINGS, main.OVERRIDES
        main.NowPlaying = FakeNowPlaying
        main.SETTINGS = os.path.join(self.tmp.name, "app_settings.json")
        main.OVERRIDES = os.path.join(self.tmp.name, "track_scenes.json")
        self.vis = main.Visualiser(no_audio=True)

    def tearDown(self):
        self.vis.stop()
        main.NowPlaying, main.SETTINGS, main.OVERRIDES = self._saved
        self.tmp.cleanup()

    def play(self, title, art=ART):
        self.vis.npl.track = Track(title, "Artist", "Album", "playing", 30.0, 200.0, art, time.time())

    def frames(self, n=3, size=(80, 30)):
        for _ in range(n):
            fr = self.vis.frame(*size)
            self.assertEqual((fr.h, fr.w), (size[1], size[0]))
        return fr

    def test_idle(self):
        self.frames()

    def test_every_scene(self):
        for i, name in enumerate(main.SCENES):
            with self.subTest(scene=name):
                self.vis.forced_scene = name
                self.play("Song %d" % i)
                self.frames()
                self.assertEqual(self.vis.scene.name, name)

    def test_without_art_and_paused(self):
        self.play("No art", art=None)
        self.frames()
        self.vis.npl.track.status = "paused"
        self.frames()

    def test_keys(self):
        self.play("Keys")
        self.frames(1)
        for k in "nwi[]n?":
            self.vis.key(k)
            self.frames(1)

    def test_odd_sizes(self):
        self.play("Sizes")
        for size in ((20, 8), (200, 60), (81, 31)):
            self.frames(2, size)


class WallpaperTest(unittest.TestCase):
    def test_compose(self):
        import wallpaper as WP
        from render import Frame
        fr = Frame(40, 12, fine=False)
        fr.text(1, 1, "♫ テスト — abc ⣿▚━", (255, 255, 255))
        fr.box(0, 0, 40, 3, (10, 10, 10), (200, 200, 200))
        atlas = WP.Atlas(8, 16)
        img = WP.compose(fr, atlas)
        self.assertEqual(img.shape, (12 * 16, 40 * 8, 4))

    def test_grid(self):
        import wallpaper as WP
        for mw, mh, rows in ((1920, 1080, 100), (3840, 2160, 160), (1080, 1920, 24)):
            g = WP.Grid(mw, mh, rows, (0, 0, mw, mh - 48))
            self.assertLessEqual(g.cols * g.cell_w, mw)
            self.assertLessEqual(g.rows * g.cell_h, mh - 48)


class UpdaterTest(unittest.TestCase):
    def test_wanted(self):
        for rel in ("main.py", "VERSION", "requirements.txt", "assets/screenshot.png", "media/README.txt"):
            self.assertTrue(updater._wanted(rel), rel)
        for rel in ("run.bat", "setup.bat", "app_settings.json", "track_scenes.json", "media/clip.mp4",
                    ".venv/x.py", "../evil.py", "/abs.py", "C:/x.py", "tests/test_smoke.py", ""):
            self.assertFalse(updater._wanted(rel), rel)

    def test_apply(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("spotify-ascii/main.py", "# new\n")
            z.writestr("spotify-ascii/VERSION", "v9.9.9\n")
            z.writestr("spotify-ascii/run.bat", "rem never copied\n")
            z.writestr("spotify-ascii/requirements.txt", "numpy\n")
        with tempfile.TemporaryDirectory() as d:
            saved = updater.HERE
            updater.HERE = d
            try:
                self.assertTrue(updater._apply(buf.getvalue()))     # requirements changed
            finally:
                updater.HERE = saved
            self.assertEqual(sorted(os.listdir(d)), ["VERSION", "main.py", "requirements.txt"])

    def test_version(self):
        with tempfile.TemporaryDirectory() as d:
            saved = version.FILE
            version.FILE = os.path.join(d, "VERSION")
            try:
                self.assertEqual(version.label(), "dev")
                version.write("v1.2.3")
                self.assertEqual(version.get(), "v1.2.3")
            finally:
                version.FILE = saved


class TrayTest(unittest.TestCase):
    def test_menu(self):
        import tray
        if tray.pystray is None:
            self.skipTest("pystray not installed")
        self.assertEqual(tray.icon_image().size, (64, 64))

        class App:
            mode, wall = "terminal", None
        App.vis = type("Vis", (), {"npl": FakeNowPlaying(), "state": "idle", "show_cover": False,
                                   "paper": False, "hud": True})()
        t = tray.Tray.__new__(tray.Tray)
        t.app = App
        texts = [str(item) for item in t._items()]
        self.assertIn("終了", texts)


if __name__ == "__main__":
    unittest.main()
