# SPDX-License-Identifier: Apache-2.0
"""The small app the engine runs under, so macOS asks in eki's name.

Everything the engine starts — Claude Code, Codex, a goal's agent and the
shells it opens, the model servers — inherits the identity of the process
launchd started. As a bare python that's "python3.12" in every permission
prompt; under ~/.eki/bin/eki.app it's "eki", with eki's icon, and one entry in
System Settings → Privacy & Security to see or revoke.

Built once from eki/launcher.swift with the Swift compiler (Xcode's command
line tools), signed ad hoc, and rebuilt only when that source changes — a new
build is a new signature, which macOS would ask about afresh. Without a Swift
compiler the engine runs as python, as before.
"""
from __future__ import annotations

import hashlib
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Optional

APP = Path("~/.eki/bin/eki.app").expanduser()
SOURCE = Path(__file__).with_name("launcher.swift")
IDENTIFIER = "local.eki.engine"
ICON_SOURCES = [Path(__file__).resolve().parent.parent / "mac" / "assets" / "AppIcon.icns"]


def binary() -> Path:
    return APP / "Contents" / "MacOS" / "eki"


def _hash() -> str:
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:16]


def current() -> bool:
    """Built, and from this source."""
    try:
        info = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError):
        return False
    return binary().exists() and info.get("EkiLauncherSource") == _hash()


def build() -> Optional[Path]:
    """The launcher, built if it isn't (or its source changed). None if it can't be."""
    if current():
        return binary()
    swiftc = shutil.which("swiftc")
    if not swiftc or not SOURCE.exists():
        return None
    tmp = APP.with_name("eki.app.building")
    shutil.rmtree(tmp, ignore_errors=True)
    macos = tmp / "Contents" / "MacOS"
    res = tmp / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    out = subprocess.run([swiftc, "-O", "-o", str(macos / "eki"), str(SOURCE)],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    icon = next((p for p in ICON_SOURCES if p.exists()), None)
    if icon:
        shutil.copy2(icon, res / "AppIcon.icns")
    info = {"CFBundleName": "eki", "CFBundleDisplayName": "eki", "CFBundleIdentifier": IDENTIFIER,
            "CFBundleExecutable": "eki", "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1", "LSMinimumSystemVersion": "14.0",
            "LSUIElement": True,                      # no Dock icon: it only starts the engine
            "EkiLauncherSource": _hash()}
    if icon:
        info["CFBundleIconFile"] = "AppIcon"
    (tmp / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", IDENTIFIER, str(tmp)],
                   capture_output=True, text=True, timeout=60)
    shutil.rmtree(APP, ignore_errors=True)
    APP.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(APP)
    return binary()
