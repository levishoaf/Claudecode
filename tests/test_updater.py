import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from nfl_edge import updater

RELEASE = {"tag_name": "build-12", "body": "New tab",
           "assets": [{"name": "BetBuilder-Windows.exe", "size": 4,
                       "browser_download_url": "https://example.com/win.exe"},
                      {"name": "BetBuilder-Mac-App.zip", "size": 3,
                       "browser_download_url": "https://example.com/mac.zip"}]}


def opener_for(payload):
    def opener(req, timeout=0):
        if isinstance(payload, Exception):
            raise payload
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return io.BytesIO(data)
    return opener


class LatestTest(unittest.TestCase):
    def test_finds_this_platforms_file(self):
        win = updater.latest("win32", opener_for(RELEASE))
        self.assertEqual((win.build, win.url, win.size), (12, "https://example.com/win.exe", 4))
        self.assertEqual(updater.latest("darwin", opener_for(RELEASE)).url,
                         "https://example.com/mac.zip")
        self.assertIsNone(updater.latest("linux", opener_for(RELEASE)))

    def test_no_release_yet_or_other_tags(self):
        missing = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        self.assertIsNone(updater.latest("win32", opener_for(missing)))
        self.assertIsNone(updater.latest("win32", opener_for({**RELEASE, "tag_name": "v2"})))

    def test_network_trouble_is_an_update_error(self):
        with self.assertRaises(updater.UpdateError):
            updater.latest("win32", opener_for(urllib.error.URLError("offline")))

    def test_only_newer_builds_of_the_downloaded_app(self):
        with mock.patch.object(updater, "can_update", return_value=True), \
                mock.patch.object(updater.sys, "platform", "win32"):
            with mock.patch.object(updater, "BUILD", 10):
                self.assertEqual(updater.available(opener_for(RELEASE)).build, 12)
            with mock.patch.object(updater, "BUILD", 12):
                self.assertIsNone(updater.available(opener_for(RELEASE)))
        with mock.patch.object(updater, "can_update", return_value=False):
            self.assertIsNone(updater.available(opener_for(RELEASE)))

    def test_running_from_source_never_updates(self):
        self.assertFalse(updater.can_update("win32"))  # not a built program


class DownloadTest(unittest.TestCase):
    def test_saves_and_checks_size(self):
        with tempfile.TemporaryDirectory() as d:
            u = updater.Update(12, "https://example.com/win.exe", 4)
            seen = []
            path = updater.download(u, Path(d) / "a", lambda a, b: seen.append(a),
                                    opener_for(b"MZok"))
            self.assertEqual(path.read_bytes(), b"MZok")
            self.assertEqual(seen[-1], 4)
            with self.assertRaises(updater.UpdateError):
                updater.download(u, Path(d) / "b", opener=opener_for(b"MZ"))
            self.assertFalse((Path(d) / "b").exists())  # no half-downloaded file left


class SwapTest(unittest.TestCase):
    """Updating replaces the app in place: one copy, same name, nothing left showing."""

    def test_swaps_a_program_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "Bet Builder by Levi Shoaf.exe"
            new = Path(d) / "Bet Builder by Levi Shoaf.download"
            app.write_text("old")
            new.write_text("new")
            with mock.patch.object(updater.sys, "platform", "win32"):
                old = updater.swap_in(app, new)
            self.assertEqual(app.read_text(), "new")
            self.assertFalse(new.exists())
            self.assertEqual(old.name, "Bet Builder by Levi Shoaf.old")  # not a second .exe
            self.assertEqual(sorted(p.name for p in Path(d).glob("*.exe")),
                             ["Bet Builder by Levi Shoaf.exe"])

    def test_swaps_a_mac_app_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "Bet Builder.app"
            new = Path(d) / "new" / "Bet Builder.app"
            (app / "Contents").mkdir(parents=True)
            (app / "Contents" / "v").write_text("old")
            (new / "Contents").mkdir(parents=True)
            (new / "Contents" / "v").write_text("new")
            old = updater.swap_in(app, new)
            self.assertEqual((app / "Contents" / "v").read_text(), "new")
            self.assertEqual(old.name, ".Bet Builder.app.old")  # hidden in Finder
            self.assertEqual([p.name for p in Path(d).glob("*.app")], ["Bet Builder.app"])

    def test_failed_swap_puts_the_app_back(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "Bet Builder.exe"
            app.write_text("old")
            with mock.patch.object(updater.sys, "platform", "win32"), \
                    self.assertRaises(OSError):
                updater.swap_in(app, Path(d) / "missing.download")
            self.assertEqual(app.read_text(), "old")

    def test_cleanup_removes_leftovers(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "Bet Builder.exe"
            app.write_text("app")
            for name in ("Bet Builder.old", "Bet Builder.old.123", "Bet Builder.download"):
                (Path(d) / name).write_text("x")
            with mock.patch.object(updater, "can_update", return_value=True), \
                    mock.patch.object(updater, "app_path", return_value=app), \
                    mock.patch.object(updater.sys, "platform", "win32"):
                updater.cleanup()
            self.assertEqual([p.name for p in Path(d).iterdir()], ["Bet Builder.exe"])

    def test_new_version_starts_with_a_clean_environment(self):
        with mock.patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": "C:/temp/_MEI1",
                                          "_MEIPASS2": "C:/temp/_MEI1", "PATH": "x"}):
            env = updater.clean_env()
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)
        self.assertNotIn("_MEIPASS2", env)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(env["PATH"], "x")

    def test_mac_app_path(self):
        exe = "/Applications/Bet Builder.app/Contents/MacOS/Bet Builder"
        self.assertEqual(updater.mac_app_path(exe).name, "Bet Builder.app")
        with self.assertRaises(updater.UpdateError):
            updater.mac_app_path("/private/var/folders/x/AppTranslocation/1/d/"
                                 "Bet Builder.app/Contents/MacOS/Bet Builder")
