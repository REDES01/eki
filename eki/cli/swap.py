"""Put another build of eki live — after its checks — or go back."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .. import builds, launchd
from .common import err

NAME = "swap"
HELP = "make a build of a commit, check it, and put it live; --back goes to the previous one"


def add(p) -> None:
    p.add_argument("ref", nargs="?", default="HEAD",
                   help="a commit of the source repo (default HEAD), or the path of a build/checkout")
    p.add_argument("--repo", help="the repo to build from (default: where this eki came from)")
    p.add_argument("--back", action="store_true", help="go back to the previous build")
    p.add_argument("--dev", action="store_true", help="run the source checkout itself again")
    p.add_argument("--no-check", action="store_true", help="skip bin/check on the build")
    p.add_argument("--wait", action="store_true", help="wait until it's healthy or rolled back")


def check(build: Path, repo: Path) -> bool:
    """bin/check at the build's commit — in a worktree of `repo`, not in the
    export: an export has no .git, and tests that ask git about the checkout
    they run in must find their own."""
    import json
    from .. import rebase, workspace
    py = os.environ.get("EKI_PYTHON") or str(repo / ".venv" / "bin" / "python")
    if not os.access(py, os.X_OK):
        py = builds.python()
    env = {**os.environ, "EKI_PYTHON": py}
    for k in ("EKI_HOME", "EKI_BUILD_DIR", "EKI_LAUNCHED", "EKI_SOURCE"):
        env.pop(k, None)                            # the tests pick a throwaway home themselves
    commit = json.loads((build / ".eki-build.json").read_text()).get("commit")
    key = f"swap-{build.name}"
    where = build if commit in (None, "worktree") else rebase.gate_tree(repo, key, commit)
    try:
        out = subprocess.run(["/bin/sh", str(where / "bin" / "check"), "-q"], cwd=str(where), env=env,
                             capture_output=True, text=True)
    finally:
        if where != build:
            workspace.remove(repo, key)
    tail = (out.stdout + out.stderr).strip().splitlines()[-3:]
    for line in tail:
        err(f"  {line}")
    return out.returncode == 0


def run(args) -> int:
    if args.back:
        rec = builds.back()
    elif args.dev:
        rec = builds.swap_to(builds.source(), "dev mode")
    else:
        repo = Path(args.repo).expanduser() if args.repo else builds.source()
        if os.path.isdir(args.ref) and (Path(args.ref) / "eki" / "engine.py").exists():
            build = Path(args.ref).resolve()
        else:
            build = builds.make(repo, args.ref)
            print(f"build {build.name} from {repo} @ {args.ref}")
        if not args.no_check:
            err("checking…")
            if not check(build, repo):
                err("not fit: the build fails its checks; nothing swapped")
                return 1
        rec = builds.swap_to(build, f"eki swap {args.ref}")
    if rec["state"] == "already":
        print(f"already running {rec['target']}")
        return 0
    if not launchd.installed():
        err("(no login agent: the engine won't be restarted by itself — `eki engine restart`)")
    print(f"current → {rec['target']}" + (f"  (previous: {rec['previous']})" if rec.get("previous") else ""))
    if not args.wait:
        print("the engine steps aside at its next tick; `eki builds` shows how it goes")
        return 0
    return wait(rec)


def wait(rec) -> int:
    end = time.time() + builds.WATCH + 60
    while time.time() < end:
        st = builds.status()
        rb = st.get("rollback")
        if rb and rb.get("at", 0) >= rec["at"]:
            err(f"rolled back to {rb['to']}: exit {rb['exit']} after {rb['after']}s")
            return 1
        sw = st.get("swap") or {}
        if sw.get("state") == "healthy" and sw.get("target") == rec["target"]:
            print(f"healthy: {rec['target']}")
            return 0
        time.sleep(2)
    err("still not healthy; see `eki builds` and the launcher log")
    return 1
