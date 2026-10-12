"""Browsers outside the slots: Chrome started in an eki worktree that holds no browser slot.

A script that builds Playwright's cache path itself (~/Library/Caches/ms-playwright)
skips the browser shim (browsershim.py), so its browser never waits for a slot.
Housekeeping notes them: a browser main process (a Chrome, Chromium or headless-shell
command line without `--type=`, which only its helpers carry) whose working directory
is under EKI_HOME/work.noindex (or the old EKI_HOME/work) and whose pid holds no slot (browserslots.pids()). It only
says so; it never kills one. `ps` and `lsof` output can be handed in, for tests.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import browserslots, browsershim, paths

#: what a browser executable's name holds
NAMES = ("chrome", "chromium", "headless_shell", "headless-shell")
TIMEOUT = 10


def _ps() -> str:
    return subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                          timeout=TIMEOUT).stdout


def _lsof(pids: Iterable[int]) -> str:
    return subprocess.run(["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-Fpn"],
                          capture_output=True, text=True, timeout=TIMEOUT).stdout


def _is_browser(command: str) -> bool:
    if "--type=" in command:
        return False                            # a renderer, GPU or utility helper, not a browser
    exe = command.split(" --")[0].rsplit("/", 1)[-1].lower()
    return "crashpad" not in exe and any(n in exe for n in NAMES)


def browsers(ps_out: str) -> List[Tuple[int, str]]:
    """(pid, command) of each browser main process in `ps -axo pid=,command=` output."""
    shim = str(browsershim.mirror())
    out = []
    for line in ps_out.splitlines():
        pid, _, command = line.strip().partition(" ")
        command = command.strip()
        if not pid.isdigit() or shim in command or not _is_browser(command):
            continue                            # one in the shim's folder is waiting for a slot
        out.append((int(pid), command))
    return out


def cwds(lsof_out: str) -> Dict[int, str]:
    """pid → working directory, from `lsof -a -d cwd -Fpn` output."""
    out: Dict[int, str] = {}
    pid = None
    for line in lsof_out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            out[pid] = line[1:]
    return out


def _under(path: str, roots: List[Path]) -> bool:
    p = Path(path)
    return any(p == r or r in p.parents for r in roots)


def strays(ps_out: Optional[str] = None, lsof_out: Optional[str] = None) -> List[int]:
    """The pids of browsers in eki worktrees that hold no browser slot."""
    found = [pid for pid, _ in browsers(_ps() if ps_out is None else ps_out)]
    if not found:
        return []
    where = cwds(_lsof(found) if lsof_out is None else lsof_out)
    old, new = paths.home() / "work", paths.work()          # the old name may still be a browser's cwd
    roots = [old, old.resolve(), new, new.resolve()]
    held = browserslots.pids()
    return sorted(p for p in found if p not in held and where.get(p) and _under(where[p], roots))


def note(ps_out: Optional[str] = None, lsof_out: Optional[str] = None) -> List[str]:
    """One line for the engine's log when there are any, else []."""
    pids = strays(ps_out, lsof_out)
    if not pids:
        return []
    return [f"browsers: {len(pids)} open in eki worktrees outside the browser slots "
            f"(pids {', '.join(str(p) for p in pids)}) — a script with a hard-coded Playwright cache path"]
