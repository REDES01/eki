"""The engine as a login agent: started at login, started again if it stops."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths

LABEL = os.environ.get("EKI_LAUNCHD_LABEL") or "local.eki.engine"


def plist_path() -> Path:
    return Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> str:
    """launchd runs the launcher (copied once into EKI_HOME/bin), which runs the
    engine from builds/current — this checkout, until a build is swapped in."""
    from . import builds
    root = builds.source()
    launcher = paths.home() / "bin" / "eki-launcher"
    launcher.parent.mkdir(exist_ok=True)
    shutil.copy2(builds.launcher_source(), launcher)
    launcher.chmod(0o755)
    if builds.current() is None:
        builds.swap_to(root, "installed: dev mode")
    path_env = ":".join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
                         "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    spec = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(launcher)],
        "WorkingDirectory": str(paths.home()),
        "EnvironmentVariables": {"EKI_HOME": str(paths.home()), "EKI_PYTHON": _python(root),
                                 "EKI_SOURCE": str(root), "PATH": path_env,
                                 "HOME": os.path.expanduser("~")},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "StandardOutPath": str(paths.logs() / "launcher.log"),
        "StandardErrorPath": str(paths.logs() / "launcher.log"),
        "ProcessType": "Background",
    }
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    for _ in range(50):                    # bootout returns before the job is gone
        gone = subprocess.run(["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True)
        if gone.returncode != 0:
            break
        time.sleep(0.1)
    with open(p, "wb") as f:
        plistlib.dump(spec, f)
    out = None
    for _ in range(5):
        out = subprocess.run(["launchctl", "bootstrap", _domain(), str(p)], capture_output=True, text=True)
        if out.returncode == 0:
            return str(p)
        time.sleep(1)
    raise RuntimeError((out.stderr.strip() if out else "") or "launchctl bootstrap failed")


def _python(root: Path) -> str:
    """The checkout's own venv when it has one (it has pytest), else this interpreter."""
    venv = root / ".venv" / "bin" / "python"
    return str(venv) if os.access(venv, os.X_OK) else sys.executable


def uninstall() -> bool:
    p = plist_path()
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    if p.exists():
        p.unlink()
        return True
    return False


def installed() -> bool:
    return plist_path().exists()


def kick() -> None:
    """Restart the launcher and its engine under launchd (workers keep running)."""
    subprocess.run(["launchctl", "kickstart", "-k", f"{_domain()}/{LABEL}"], capture_output=True)
