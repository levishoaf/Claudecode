"""Keep the downloaded app up to date from the project's GitHub Releases.

Every build of the app is published as a GitHub Release tagged "build-<number>",
with the Windows program and the Mac app attached (see
.github/workflows/build-executables.yml). The build number is stamped into the
app as nfl_edge/_build.py when it's built. The app asks GitHub for the latest
release; when it's newer, it downloads the new program, starts a small helper
that swaps it in once the app has closed, and reopens it.

Only the built program updates itself; running from source (python bets_gui.py)
never does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:
    from ._build import BUILD, REPO  # written by the build workflow
except ImportError:  # running from source
    BUILD, REPO = 0, "levishoaf/Claudecode"

API = "https://api.github.com/repos/{repo}/releases/latest"
TAG_PREFIX = "build-"
# The release file each platform's app updates from.
ASSETS = {"win32": "BetBuilder-Windows.exe", "darwin": "BetBuilder-Mac-App.zip"}
CHECK_HOURS = 6


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Update:
    build: int
    url: str
    size: int
    notes: str = ""


def can_update(platform: str | None = None) -> bool:
    """True for a built app (with a build number) on Windows or Mac."""
    return bool(getattr(sys, "frozen", False)) and BUILD > 0 and \
        (platform or sys.platform) in ASSETS


def _get_json(url: str, opener=None):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "Bet-Builder-updater"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no release yet
        raise UpdateError(f"GitHub answered {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise UpdateError(f"couldn't reach GitHub ({getattr(e, 'reason', e)})")


def latest(platform: str | None = None, opener=None) -> Update | None:
    """The newest published build for this platform, or None if there isn't one."""
    release = _get_json(API.format(repo=REPO), opener)
    if not release:
        return None
    tag = str(release.get("tag_name", ""))
    if not tag.startswith(TAG_PREFIX) or not tag[len(TAG_PREFIX):].isdigit():
        return None
    name = ASSETS.get(platform or sys.platform)
    asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if asset is None:
        return None
    return Update(int(tag[len(TAG_PREFIX):]), asset["browser_download_url"],
                  int(asset.get("size", 0)), release.get("body") or "")


def available(opener=None) -> Update | None:
    """A newer build than this one, if there is one (None when running from source)."""
    if not can_update():
        return None
    found = latest(opener=opener)
    return found if found and found.build > BUILD else None


def download(update: Update, dest: Path, progress=None, opener=None) -> Path:
    """Save the update to `dest`, calling progress(done, total) as it goes."""
    req = urllib.request.Request(update.url, headers={"User-Agent": "Bet-Builder-updater"})
    done = 0
    try:
        with (opener or urllib.request.urlopen)(req, timeout=60) as resp, open(dest, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, update.size)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"download failed ({getattr(e, 'reason', e)})")
    if update.size and done != update.size:
        dest.unlink(missing_ok=True)
        raise UpdateError("download was incomplete")
    return dest


# ------------------------------------------------------------------ swapping in

def windows_script(target: Path, new: Path, pid: int) -> str:
    """A batch file that waits for the app to close, swaps in the new .exe, reopens it."""
    return "\r\n".join([
        "@echo off",
        "set tries=0",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
        "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)",
        ":swap",
        f'move /y "{new}" "{target}" >nul 2>&1',
        "if errorlevel 1 (",
        "  set /a tries+=1",
        "  if %tries% lss 60 (timeout /t 1 /nobreak >nul & goto swap)",
        ")",
        f'start "" "{target}"',
        'del "%~f0"',
        ""])


def mac_script(app: Path, new_app: Path, pid: int, reopen: str = "open") -> str:
    """A shell script that waits for the app to close, swaps in the new .app, reopens it."""
    old = f"{app}.old"
    return "\n".join([
        "#!/bin/sh",
        f"while kill -0 {pid} 2>/dev/null; do sleep 1; done",
        "sleep 1",
        f'rm -rf "{old}"',
        f'if mv "{app}" "{old}" && mv "{new_app}" "{app}"; then rm -rf "{old}";',
        f'else [ -d "{old}" ] && [ ! -d "{app}" ] && mv "{old}" "{app}"; fi',
        f'{reopen} "{app}"',
        'rm -f "$0"',
        ""])


def mac_app_path(executable: str | None = None) -> Path:
    """The .app bundle around the running program (…/X.app/Contents/MacOS/X)."""
    exe = Path(executable or sys.executable).resolve()
    app = exe.parents[2]
    if app.suffix != ".app":
        raise UpdateError("the app isn't running from its .app bundle")
    if "AppTranslocation" in str(app):  # macOS runs downloaded apps from a read-only copy
        raise UpdateError("move Bet Builder to your Applications folder, open it from "
                          "there, then update")
    return app


def install(update: Update, progress=None, opener=None) -> None:
    """Download the update and start the helper that swaps it in. The caller must
    quit the app right after this returns; the helper reopens the new version."""
    if not can_update():
        raise UpdateError("only the downloaded app can update itself")
    work = Path(tempfile.mkdtemp(prefix="bet-builder-update-"))
    pid = os.getpid()
    if sys.platform == "win32":
        target = Path(sys.executable).resolve()
        new = target.with_name(target.stem + ".new.exe")
        download(update, new, progress, opener)
        if new.read_bytes()[:2] != b"MZ":
            new.unlink(missing_ok=True)
            raise UpdateError("the download isn't a Windows program")
        script = work / "update.bat"
        script.write_text(windows_script(target, new, pid))
        flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True)
    else:
        app = mac_app_path()
        archive = download(update, work / "update.zip", progress, opener)
        unpacked = work / "new"
        unpacked.mkdir()
        # ditto keeps the app's permissions and links, as the build zipped it
        result = subprocess.run(["ditto", "-x", "-k", str(archive), str(unpacked)],
                                capture_output=True)
        new_app = next(unpacked.glob("*.app"), None)
        if result.returncode or new_app is None:
            shutil.rmtree(work, ignore_errors=True)
            raise UpdateError("couldn't unpack the update")
        script = work / "update.sh"
        script.write_text(mac_script(app, new_app, pid))
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
