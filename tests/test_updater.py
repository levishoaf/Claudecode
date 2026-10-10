import io
import json
import os
import subprocess
import sys
import tempfile
import time
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
    def test_windows_script_waits_swaps_and_reopens(self):
        text = updater.windows_script(Path(r"C:\Apps\Bet Builder.exe"),
                                      Path(r"C:\Apps\Bet Builder.new.exe"), 4242)
        self.assertIn('tasklist /FI "PID eq 4242"', text)
        self.assertIn(r'move /y "C:\Apps\Bet Builder.new.exe" "C:\Apps\Bet Builder.exe"', text)
        self.assertIn(r'start "" "C:\Apps\Bet Builder.exe"', text)

    @unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
    def test_mac_script_swaps_the_app_after_it_closes(self):
        with tempfile.TemporaryDirectory() as d:
            app, new = Path(d) / "Bet Builder.app", Path(d) / "new" / "Bet Builder.app"
            (app / "Contents").mkdir(parents=True)
            (app / "Contents" / "v").write_text("old")
            (new / "Contents").mkdir(parents=True)
            (new / "Contents" / "v").write_text("new")
            # A stand-in for the app: a process that ends in a second (and is reaped by
            # the system, like a real app that closed).
            pid = int(subprocess.run(["/bin/sh", "-c", "sleep 1 >/dev/null 2>&1 & echo $!"],
                                     capture_output=True, text=True).stdout)
            script = Path(d) / "update.sh"
            marker = Path(d) / "reopened"
            script.write_text(updater.mac_script(app, new, pid,
                                                 reopen=f"touch {marker}; true"))
            started = time.monotonic()
            subprocess.run(["/bin/sh", str(script)], check=True, timeout=30)
            self.assertGreaterEqual(time.monotonic() - started, 0.5)  # waited for the app
            self.assertEqual((app / "Contents" / "v").read_text(), "new")
            self.assertFalse(Path(f"{app}.old").exists())
            self.assertTrue(marker.exists())

    def test_mac_app_path(self):
        exe = "/Applications/Bet Builder.app/Contents/MacOS/Bet Builder"
        self.assertEqual(updater.mac_app_path(exe).name, "Bet Builder.app")
        with self.assertRaises(updater.UpdateError):
            updater.mac_app_path("/private/var/folders/x/AppTranslocation/1/d/"
                                 "Bet Builder.app/Contents/MacOS/Bet Builder")
