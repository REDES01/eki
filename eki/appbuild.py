# SPDX-License-Identifier: Apache-2.0
"""The Mac app, kept in step with the engine (docs/self-build.md).

The engine is swapped by the supervisor; the app is its own program, built
from mac/. Two things keep it from lagging behind or breaking:

- the candidate check typechecks mac/ when a change touches it, so a change
  whose Swift doesn't compile is never "fit" (`typecheck`);
- after a healthy swap whose mac/ differs from what the installed app was
  built from, the app is rebuilt from the running build and put in place —
  only while you aren't using it; otherwise at the next chance (`install`).

What the installed app was built from is a hash of mac/'s sources, kept in
~/.eki/app.json, so an unchanged app is never rebuilt.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE = Path("~/.eki/app.json").expanduser()
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


def install(build: Path, app: Path, *, force: bool = False) -> str:
    """Rebuild the app from `build`'s mac/ and put it at `app`, reopening it
    if it was open. "" when there was nothing to do; otherwise what happened.
    Waits (says so, remembers it) while you're using the app."""
    build, app = Path(build), Path(app)
    want = sources_hash(build)
    if not want or (want == state().get("hash") and app.exists() and not force):
        _save(pending="")
        return ""
    if frontmost() and not force:
        _save(pending=str(build))
        return "waiting: you're using the app — it's rebuilt when it's not in front"
    new = app.with_name(app.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    got = subprocess.run(["./build_app.sh"], cwd=str(build / "mac"), capture_output=True, text=True,
                         timeout=900, env={**os.environ, "EKI_APP_PATH": str(new)})
    if got.returncode != 0 or not (new / "Contents" / "MacOS" / "Eki").exists():
        shutil.rmtree(new, ignore_errors=True)
        tail = (got.stderr.strip() or got.stdout.strip()).splitlines()[-3:]
        _save(pending="", failed=want)
        return "not rebuilt: " + " | ".join(tail)[:300]
    was_open = _running(app)
    if was_open:
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
    if was_open:
        subprocess.run(["open", str(app)], capture_output=True, timeout=15)
    _save(hash=want, pending="", failed="", built_from=str(build))
    return "the app was rebuilt from the running build" + (" and reopened" if was_open else "")
