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

import http.client
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
    except (OSError, http.client.HTTPException, ValueError) as e:
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
    except (OSError, http.client.HTTPException) as e:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"download failed ({getattr(e, 'reason', e)})")
    if update.size and done != update.size:
        dest.unlink(missing_ok=True)
        raise UpdateError("download was incomplete")
    return dest


# ------------------------------------------------------------------ swapping in
#
# The running app moves its own file aside (Windows and macOS both allow renaming a
# program while it runs), puts the new version in its place under the same name,
# starts it and quits. So there's only ever one copy of the app, and the leftover
# old one (hidden) is deleted the next time the app starts.


def leftover(target: Path) -> Path:
    """Where the replaced version goes: a hidden name next to the app."""
    if sys.platform == "darwin" or target.suffix == ".app":
        return target.with_name(f".{target.name}.old")
    return target.with_name(f"{target.stem}.old")


def swap_in(target: Path, new: Path) -> Path:
    """Put `new` where `target` is, moving `target` aside. Returns where it went;
    puts everything back if the swap fails."""
    old = leftover(target)
    _remove(old)
    if old.exists():  # still in use (an old copy is running): use another name
        old = old.with_name(f"{old.name}.{os.getpid()}")
    os.replace(target, old)
    try:
        shutil.move(str(new), str(target))
    except OSError:
        if target.exists():
            _remove(target)
        os.replace(old, target)
        raise
    _hide(old)
    return old


def _remove(path: Path) -> None:
    try:
        shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass  # still in use; cleaned up on a later start


def _hide(path: Path) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


def clean_env() -> dict:
    """Settings for starting the new version as a fresh program. A bundled app passes
    its own temporary-folder settings to programs it starts; the new version would
    look for files the old one deletes as it quits, and fail to open."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI", "_MEIPASS"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def app_path() -> Path:
    """The installed app: the .exe on Windows, the .app bundle on a Mac."""
    return mac_app_path() if sys.platform == "darwin" else Path(sys.executable).resolve()


def cleanup() -> None:
    """Delete what an earlier update left behind (called when the app starts)."""
    if not can_update():
        return
    try:
        target = app_path()
    except UpdateError:
        return
    for path in target.parent.glob(leftover(target).name + "*"):
        _remove(path)
    _remove(target.with_name(target.stem + ".download"))
    _remove(target.with_name(target.stem + ".new.exe"))  # left by builds before 62


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
    """Download the update, swap it in for this app and start it. The caller must
    quit right after this returns."""
    if not can_update():
        raise UpdateError("only the downloaded app can update itself")
    target = app_path()
    work = Path(tempfile.mkdtemp(prefix="bet-builder-update-"))
    try:
        if sys.platform == "win32":
            new = download(update, target.with_name(target.stem + ".download"), progress,
                           opener)
            with open(new, "rb") as f:
                if f.read(2) != b"MZ":
                    new.unlink(missing_ok=True)
                    raise UpdateError("the download isn't a Windows program")
            command = [str(target)]
        else:
            archive = download(update, work / "update.zip", progress, opener)
            unpacked = work / "new"
            unpacked.mkdir()
            # ditto keeps the app's permissions and links, as the build zipped it
            result = subprocess.run(["ditto", "-x", "-k", str(archive), str(unpacked)],
                                    capture_output=True)
            new = next(unpacked.glob("*.app"), None)
            if result.returncode or new is None:
                raise UpdateError("couldn't unpack the update")
            command = ["open", "-n", str(target)]
        try:
            swap_in(target, new)
        except OSError as e:
            raise UpdateError(f"couldn't replace the app ({e.strerror or e})")
        try:
            if sys.platform == "win32":
                flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(command, env=clean_env(), cwd=str(target.parent),
                                 creationflags=flags, close_fds=True)
            else:
                subprocess.Popen(command, env=clean_env(), start_new_session=True,
                                 close_fds=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except OSError as e:
            raise UpdateError(f"installed, but couldn't reopen it ({e}); open it again "
                              "yourself")
    finally:
        shutil.rmtree(work, ignore_errors=True)
