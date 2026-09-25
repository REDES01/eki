# SPDX-License-Identifier: Apache-2.0
"""The Mac app, kept in step with the engine (docs/self-build.md).

The engine is swapped by the supervisor; the app is its own program, built
from mac/. Two things keep it from lagging behind or breaking:

- the candidate check typechecks mac/ when a change touches it, so a change
  whose Swift doesn't compile is never "fit" (`typecheck`);
- after a healthy swap whose mac/ differs from what the installed app was
  built from, the app is rebuilt from the running build and put in place
  (`install`). An app in the background is quit and reopened at once; one
  you're using is left open, and offers to restart into the new build itself
  — at a click, or once you've left it a few minutes (mac/Update.swift).
  Waiting for it to leave the front never ended for an app kept in front.

What the installed app was built from is a hash of mac/'s sources, kept in
~/.eki/app.json, so an unchanged app is never rebuilt. The bundle carries
the short hash and when it was built (EkiBuild, EkiBuiltAt in Info.plist);
the open app writes its own to ~/.eki/app-running/, so `versions` can
say what runs and what's installed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE = Path("~/.eki/app.json").expanduser()
RUNNING = Path("~/.eki/app-running").expanduser()   # <pid>.json, written by the open app
BUNDLE_ID = "local.eki.app"
FRAMEWORKS = ("AppKit", "SwiftUI", "ServiceManagement", "WebKit")


def _sources(root: Path) -> List[Path]:
    mac = Path(root) / "mac"
    if not mac.is_dir():
        return []
    files = sorted(mac.glob("*.swift")) + [mac / "build_app.sh"]
    assets = mac / "assets"
    if assets.is_dir():
        files += sorted(p for p in assets.rglob("*") if p.is_file())
    return [f for f in files if f.is_file()]


def sources_hash(root: Path) -> str:
    """What the app is built from, as one hash; "" when there's no mac/."""
    files = _sources(root)
    if not files:
        return ""
    h = hashlib.sha1()
    for f in files:
        h.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def state() -> Dict[str, Any]:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(**fields: Any) -> Dict[str, Any]:
    s = {**state(), **fields, "at": int(time.time())}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))
    return s


def changed(root: Path) -> bool:
    """mac/ here differs from what the installed app was built from."""
    got = sources_hash(root)
    return bool(got) and got != state().get("hash")


def typecheck(root: Path, timeout: float = 300) -> str:
    """The app's Swift, typechecked (no build). Raises RuntimeError with the
    first errors when it doesn't compile."""
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise FileNotFoundError("no swiftc (Xcode command line tools)")
    files = [str(p) for p in sorted((Path(root) / "mac").glob("*.swift"))]
    cmd = [swiftc, "-typecheck", "-target", "arm64-apple-macos14.0"]
    for fw in FRAMEWORKS:
        cmd += ["-framework", fw]
    began = time.time()
    got = subprocess.run(cmd + files, capture_output=True, text=True, timeout=timeout)
    if got.returncode != 0:
        errors = [x for x in got.stderr.splitlines() if ": error:" in x]
        shown = [e.replace(str(root) + "/", "") for e in (errors or got.stderr.splitlines())[:6]]
        raise RuntimeError("the app doesn't compile: " + " | ".join(shown))
    return f"the app compiles ({len(files)} Swift files, {time.time() - began:.0f}s)"


def frontmost() -> bool:
    """You're using the app right now (it's the frontmost one)."""
    try:
        front = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
        info = subprocess.run(["lsappinfo", "info", "-only", "bundleid", front],
                              capture_output=True, text=True, timeout=5).stdout
        return BUNDLE_ID in info
    except (OSError, subprocess.SubprocessError):
        return False


def _running(app: Path) -> bool:
    exe = str(app / "Contents" / "MacOS" / "Eki")
    return subprocess.run(["pgrep", "-f", exe], capture_output=True).returncode == 0


def label(build: Dict[str, Any]) -> str:
    """"b7a8cf2 (built 09-26 00:51)" — a build as the person reads it."""
    name = build.get("build") or "?"
    at = build.get("built_at") or 0
    return name + (time.strftime(" (built %m-%d %H:%M)", time.localtime(at)) if at else "")


def bundle_build(app: Path) -> Dict[str, Any]:
    """The build a bundle on disk carries; {} when there's none there. A
    bundle from before builds were stamped gets the hash from app.json, and
    was built when its Info.plist was written."""
    plist = Path(app) / "Contents" / "Info.plist"
    try:
        with open(plist, "rb") as f:
            info = plistlib.load(f)
        written = int(plist.stat().st_mtime)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    got = {"build": str(info.get("EkiBuild") or ""), "built_at": int(info.get("EkiBuiltAt") or 0)}
    if not got["build"]:
        got = {"build": str(state().get("hash") or "")[:7] or "unstamped", "built_at": written}
    return got


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True             # someone else's — still there
    return True


def _open_builds(app: Path) -> List[Dict[str, Any]]:
    """What open copies of the app at `app` said about themselves when they
    started (one file per process in RUNNING). A scratch copy elsewhere, or
    one that has quit, doesn't count."""
    want = os.path.realpath(app)
    got = []
    for f in sorted(RUNNING.glob("*.json")) if RUNNING.is_dir() else []:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not _alive(int(d.get("pid") or 0)):
            f.unlink(missing_ok=True)
        elif os.path.realpath(str(d.get("path") or "")) == want:
            got.append(d)
    return got


def _unstamped(app: Path) -> Dict[str, Any]:
    """An open app too old to say which build it is: when it was opened."""
    exe = os.path.realpath(app) + "/Contents/MacOS/Eki"
    try:
        out = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and os.path.realpath(parts[2].strip()) == exe:
            return {"pid": int(parts[0]), "build": "", "opened": int(time.time()) - _seconds(parts[1])}
    return {}


def _seconds(etime: str) -> int:
    """ps's elapsed time, [[dd-]hh:]mm:ss, in seconds."""
    days, _, rest = etime.rpartition("-")
    n = 0
    for part in rest.split(":"):
        n = n * 60 + int(part or 0)
    return n + int(days or 0) * 86400


def versions(app: Optional[Path] = None) -> Dict[str, Any]:
    """Which build of the app is open and which is installed. `running` is {}
    when the app isn't open; `behind` when the open one is older than what's
    on disk — it offers to restart then (mac/Update.swift)."""
    app = Path(app or _installed_app())
    opened = _open_builds(app)
    run = opened[-1] if opened else _unstamped(app)
    installed = bundle_build(app)
    if not (run and installed):
        behind = False
    elif run.get("build"):
        behind = (run["build"], run.get("built_at") or 0) != (installed["build"], installed["built_at"])
    else:                       # unstamped: older when the bundle was put in place after it opened
        behind = installed["built_at"] > run.get("opened", 0)
    return {"running": run, "installed": installed, "behind": behind, "path": str(app)}


def _installed_app() -> Path:
    from . import builds
    return builds.source() / "Eki.app"


def versions_line(v: Optional[Dict[str, Any]] = None) -> str:
    """"app: running …, installed …" — for eki self and the board."""
    v = v if v is not None else versions()
    run, inst = v.get("running") or {}, v.get("installed") or {}
    if not inst:
        return ""
    have = "installed " + label(inst)
    if not run:
        return f"app: not open, {have}"
    now = label(run) if run.get("build") else \
        time.strftime("an unnamed build (open since %m-%d %H:%M)", time.localtime(run.get("opened") or 0))
    if not v.get("behind"):
        return f"app: running {now} — up to date"
    if not run.get("build"):                # too old to offer it: no banner there
        return f"app: running {now}, {have} — quit and reopen Eki to pick it up"
    return f"app: running {now}, {have} — Restart in the app, or it restarts by itself once left a few minutes"


def install(build: Path, app: Path, *, force: bool = False) -> str:
    """Rebuild the app from `build`'s mac/ and put it at `app`. An open app
    in the background is quit and reopened; one you're using stays open and
    offers to restart itself. "" when there was nothing to do; otherwise what
    happened. One rebuild at a time on this Mac, whoever asks."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_suffix(".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "waiting: another rebuild of the app is under way"
        return _install(Path(build), Path(app), force)


def _install(build: Path, app: Path, force: bool) -> str:
    want = sources_hash(build)
    if not want or (want == state().get("hash") and app.exists() and not force):
        _save(pending="")
        return ""
    new = app.with_name(app.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    got = subprocess.run(["./build_app.sh"], cwd=str(build / "mac"), capture_output=True, text=True,
                         timeout=900, env={**os.environ, "EKI_APP_PATH": str(new), "EKI_APP_BUILD": want[:7]})
    if got.returncode != 0 or not (new / "Contents" / "MacOS" / "Eki").exists():
        shutil.rmtree(new, ignore_errors=True)
        tail = (got.stderr.strip() or got.stdout.strip()).splitlines()[-3:]
        _save(pending="", failed=want)
        return "not rebuilt: " + " | ".join(tail)[:300]
    was_open = _running(app)
    in_use = was_open and frontmost() and not force
    if was_open and not in_use:
        subprocess.run(["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'],
                       capture_output=True, timeout=15)
        for _ in range(40):
            if not _running(app):
                break
            time.sleep(0.25)
    old = app.with_name(app.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if app.exists():
        os.replace(app, old)
    os.replace(new, app)
    shutil.rmtree(old, ignore_errors=True)
    if was_open and not in_use:
        subprocess.run(["open", str(app)], capture_output=True, timeout=15)
    _save(hash=want, pending="", failed="", built_from=str(build))
    if in_use:
        return ("the app was rebuilt from the running build — the open one says "
                "'New version ready': Restart, or it restarts by itself once left a few minutes")
    return "the app was rebuilt from the running build" + (" and reopened" if was_open else "")
