# SPDX-License-Identifier: Apache-2.0
"""Keep the engine alive without anyone remembering to start it.

A launchd agent: it starts the engine at login and starts it again if it
dies. With it installed, the Mac app is a pure viewer — quit it, reboot, log
back in, and every conversation and every run is where you left it.

Only the engine is managed this way. The model servers (MLX, ComfyUI) stay
on-demand, because 22GB of weights resident from login is a cost you should
choose each time, not inherit.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Tuple

LABEL = "local.eki.engine"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / ".eki" / "engine.log"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str) -> Tuple[int, str]:
    out = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    return out.returncode, (out.stdout + out.stderr).strip()


def plist_for(root: Path) -> dict:
    """launchd runs ~/.eki/builds/current — your checkout until a swap
    points it at a build (eki/builds.py) — with your checkout's venv and
    config either way."""
    from . import builds
    python = root / ".venv" / "bin" / "python"
    current = builds.ensure_layout(root)
    env = {"PYTHONPATH": str(current), "EKI_SOURCE": str(root),
           "EKI_CONFIG": str(root / "config.yaml")}
    hid = root / "Eki.app" / "Contents" / "Helpers" / "eki-hid"
    if hid.exists():
        env["EKI_HID"] = str(hid)           # a build has no app beside it
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "eki.cli", "serve"],
        "WorkingDirectory": str(current),
        "RunAtLoad": True,
        # restart if it exits for any reason — but not in a tight loop if it
        # can't start at all (a port someone else holds, say)
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        # launchd's PATH is /usr/bin:/bin. The CLIs eki drives live in these,
        # and claude and codex spawn their own helpers from the same places.
        "EnvironmentVariables": {
            "PATH": ":".join([
                str(Path.home() / ".local" / "bin"),
                "/opt/homebrew/bin", "/usr/local/bin",
                "/usr/bin", "/bin", "/usr/sbin", "/sbin",
            ]),
            "PYTHONUNBUFFERED": "1",
            **env,
        },
        # an engine answering a chat should not be throttled like a daemon
        "ProcessType": "Interactive",
    }


def installed() -> bool:
    return PLIST.exists()


def loaded() -> bool:
    code, _ = _launchctl("print", f"{_domain()}/{LABEL}")
    return code == 0


def install(root: Path) -> str:
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        return f"no virtualenv at {python} — run ./eki.sh once first"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    with PLIST.open("wb") as f:
        plistlib.dump(plist_for(root), f)
    from . import builds
    builds.install_supervisor()             # a person's command: the only way it changes
    code, out = _launchctl("bootstrap", _domain(), str(PLIST))
    if code != 0:
        return f"wrote {PLIST}, but launchd refused it: {out}"
    return f"installed — the engine now starts at login ({PLIST})"


def uninstall() -> str:
    if loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    if PLIST.exists():
        PLIST.unlink()
        return "removed — the engine will no longer start at login"
    return "not installed"


def restart() -> str:
    code, out = _launchctl("kickstart", "-k", f"{_domain()}/{LABEL}")
    return "restarted" if code == 0 else f"couldn't restart: {out}"


def status() -> str:
    if not installed():
        code, out = _launchctl("print", f"{_domain()}/{LABEL}")
        if code == 0:
            # the packaged app registers the same job through SMAppService, so
            # launchd knows about it even though no plist of ours is on disk
            state = next((l.split("=", 1)[1].strip() for l in out.splitlines()
                          if l.strip().startswith("state =")), "?")
            return f"installed by Eki.app · {state}"
        return "not installed — `eki agent install` to start the engine at login"
    if not loaded():
        return f"installed at {PLIST}, but not loaded"
    code, out = _launchctl("print", f"{_domain()}/{LABEL}")
    state = next((line.split("=", 1)[1].strip() for line in out.splitlines()
                  if line.strip().startswith("state =")), "?")
    pid = next((line.split("=", 1)[1].strip() for line in out.splitlines()
                if line.strip().startswith("pid =")), "")
    return f"installed · {state}" + (f" · pid {pid}" if pid else "") + f" · log {LOG}"


if __name__ == "__main__":
    print(status())
    sys.exit(0)
