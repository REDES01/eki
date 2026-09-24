# SPDX-License-Identifier: Apache-2.0
"""What the engine runs, and moving it to something else (docs/self-build.md).

    ~/.eki/builds/
        3f2a9c1d0e/     one commit, exported (`git archive`), never edited
        current  ->     what launchd runs: a build, or your checkout
        previous ->     what ran before the last swap

A build is an export of one commit plus `.eki-build.json` saying which. It
shares your checkout's virtualenv — eki isn't installed into it, it runs
from its folder — so a build is a few megabytes and no reinstall.

At install `current` is your checkout itself, so editing it and restarting
works as it always did. A swap points `current` at a build; `eki swap --dev`
points it back at your checkout.

Swapping is never done by the engine to itself: the supervisor (a small
shell script a person installs, eki/supervisor.sh) waits for the runs to
finish, swaps, watches the new engine, and swaps back if it isn't healthy.
The engine only asks for a swap and reads the outcome.

Applied changes don't each go live on their own: they board the release
train (`board`), and at most once every `self_release_minutes` (15) the
engine sends the newest build — which carries every car before it — to the
supervisor (`depart`). Work never pauses for it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BUILDS = Path("~/.eki/builds").expanduser()
SELF_HOME = Path("~/.eki/self").expanduser()
SUPERVISOR = Path("~/.eki/bin/eki-supervisor").expanduser()
TEMPLATE = Path(__file__).with_name("supervisor.sh")
MARK = ".eki-build.json"
#: builds other than current and previous are removed after this long
KEEP_DAYS = 7
#: how long a swap waits for a quiet moment before it goes ahead anyway;
#: runs still going are carried on by the new engine
WAIT = 120
#: at most one go-live this often (setting `self_release_minutes`)
RELEASE_MINUTES = 15


def here() -> Path:
    """The folder this engine is running from."""
    return Path(__file__).resolve().parent.parent


def info(path: Path) -> Dict[str, Any]:
    """What a folder is: a build (its mark), or a checkout ("dev")."""
    try:
        return json.loads((path / MARK).read_text())
    except (OSError, ValueError):
        pass
    commit = ""
    try:
        commit = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"id": "dev", "dev": True, "commit": commit, "source": str(path)}


REPO = "https://github.com/REDES01/eki"


def installed_by() -> str:
    """"homebrew" when a package manager put eki here (its `eki` command says
    so), "" for a checkout or a build."""
    return os.environ.get("EKI_INSTALL", "").strip().lower()


def cant_change_code(src: Path) -> str:
    """Why eki can't work on its own code from `src`, or "".

    A package manager owns what it installed and puts the released version
    back on every upgrade, so eki doesn't edit it or swap builds under it.
    Everything eki learns lives in ~/.eki and works the same either way."""
    if not installed_by() or (Path(src) / ".git").exists():
        return ""                   # a checkout, or a folder git will say more about
    how = {"homebrew": "Homebrew"}.get(installed_by(), "a package manager")
    return (f"eki was installed by {how}, which keeps it at the released version, so it "
            f"doesn't change its own code here. To work on eki: git clone {REPO} and run "
            f"it from there (docs/self-build.md).")


def running() -> Dict[str, Any]:
    return info(here())


def source() -> Path:
    """Your checkout: where builds are made from and self-work happens."""
    env = os.environ.get("EKI_SOURCE")
    if env:
        return Path(env).expanduser()
    return Path(running().get("source") or here())


def _point(link: Path, target: Path) -> None:
    """Repoint a symlink in one step (a rename), never a moment without one."""
    tmp = link.with_name(f".{link.name}.{os.getpid()}")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(str(target), str(tmp))
    os.replace(tmp, link)


def ensure_layout(src: Path) -> Path:
    """`current` exists — your checkout, the first time."""
    BUILDS.mkdir(parents=True, exist_ok=True)
    cur = BUILDS / "current"
    if not cur.is_symlink():
        _point(cur, src)
    return cur


def make(src: Path, ref: str = "HEAD", note: str = "") -> Path:
    """An export of `ref` in your checkout, as a build. The same commit is
    the same build: made once."""
    why = cant_change_code(src)
    if why:
        raise ValueError(why)
    commit = subprocess.run(["git", "-C", str(src), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                            capture_output=True, text=True, timeout=30)
    if commit.returncode != 0:
        raise ValueError(f"no commit {ref!r} in {src}")
    sha = commit.stdout.strip()
    bid = sha[:10]
    dest = BUILDS / bid
    if (dest / MARK).exists():
        return dest
    BUILDS.mkdir(parents=True, exist_ok=True)
    tmp = BUILDS / f".{bid}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    archive = subprocess.Popen(["git", "-C", str(src), "archive", "--format=tar", sha],
                               stdout=subprocess.PIPE)
    untar = subprocess.run(["tar", "-x", "-C", str(tmp)], stdin=archive.stdout)
    archive.wait()
    if archive.returncode != 0 or untar.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"couldn't export {sha[:10]} from {src}")
    (tmp / MARK).write_text(json.dumps({"id": bid, "commit": sha, "source": str(src),
                                        "ref": ref, "note": note, "made": int(time.time())},
                                       indent=2))
    os.replace(tmp, dest)
    return dest


def listing() -> List[Dict[str, Any]]:
    cur = _target("current")
    prev = _target("previous")
    rows = []
    for p in sorted(BUILDS.glob("*")) if BUILDS.is_dir() else []:
        if p.name.startswith(".") or p.is_symlink() or not (p / MARK).exists():
            continue
        rows.append({**info(p), "path": str(p), "current": p == cur, "previous": p == prev})
    for name, target in (("current", cur), ("previous", prev)):
        if target is not None and not (target / MARK).exists():
            rows.append({**info(target), "path": str(target), "current": name == "current",
                         "previous": name == "previous"})
    return rows


def _target(name: str) -> Optional[Path]:
    link = BUILDS / name
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).resolve()


def swap(target: Path, *, wait: int = WAIT, watch: int = 180, self_id: str = "",
         supervisor: Optional[Path] = None) -> Dict[str, Any]:
    """Ask the supervisor to move the engine onto `target`. It runs apart
    from the engine (its own session), since it restarts the engine.

    Work never pauses for it: runs keep going and new ones keep starting.
    The supervisor waits a little (`wait`) for a quiet moment, then swaps
    anyway — the new engine carries on the runs it cut off."""
    sup = supervisor or SUPERVISOR
    if not os.access(sup, os.X_OK):
        raise RuntimeError(f"no supervisor at {sup} — a person installs it with `eki agent install`")
    SELF_HOME.mkdir(parents=True, exist_ok=True)
    # One swap at a time, newest wins. A build made now contains what was
    # waiting to go in (`live()` counts it), so a swap still waiting is
    # superseded; one already swapping finishes its watch first.
    before, pid, now = last_swap(), _swap_pid(), int(time.time())
    deadline = now + max(0, int(wait))
    behind = 0                                  # the supervisor mid-swap this one waits out
    with open(SELF_HOME / "swap.log", "a") as log:
        if pid and before.get("state") == "waiting":
            # the new build keeps the waiting one's deadline: with work always
            # going, a wait that restarted on every apply would never end
            deadline = min(deadline, int(before.get("deadline") or deadline))
            try:
                os.kill(pid, 15)
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} superseded the waiting swap to "
                          f"{before.get('target')} (the new build contains it; its deadline stands)\n")
            except OSError:
                pass
            # one already past its wait no longer yields: it is waited out instead
            behind = _alive(int(before.get("behind") or 0)) or _outlives(pid)
        elif pid and before.get("state") == "swapping":
            behind = pid
        # the wait is counted to the deadline when the supervisor starts — after
        # the one mid-swap has finished, if there is one
        # (ps, not kill -0: one that has ended but isn't reaped yet is a zombie)
        cmd = ["/bin/sh", "-c", 'while [ "$1" -gt 0 ] && ps -o stat= -p "$1" 2>/dev/null | grep -qv Z; '
               'do sleep 2; done; '
               'w=$(( $2 - $(date +%s) )); [ "$w" -gt 0 ] || w=0; exec "$3" "$4" "$w" "$5" "$6"',
               "eki-swap", str(behind), str(deadline), str(sup), str(target), str(watch), self_id]
        (SELF_HOME / "swap.json").write_text(json.dumps(
            {"state": "waiting", "target": str(target), "self": self_id, "at": now,
             "deadline": deadline, **({"behind": behind} if behind else {})}))
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True)
    (SELF_HOME / "swap.pid").write_text(str(proc.pid))
    return {"target": str(target), "log": str(SELF_HOME / "swap.log"), "deadline": deadline}


def going_live() -> Dict[str, Any]:
    """A new version on its way in, for `eki self` and the Self board:
    {"target", "self", "in": seconds until it swaps (0: now), "state"}.
    {} when nothing is on its way."""
    s = last_swap()
    if s.get("state") not in ("waiting", "swapping") or not _swap_pid():
        return {}
    left = max(0, int(s.get("deadline") or 0) - int(time.time())) if s["state"] == "waiting" else 0
    return {"state": s["state"], "target": s.get("target") or "", "self": s.get("self") or "",
            "in": left}


def _alive(pid: int) -> int:
    """`pid` while it runs, else 0. A supervisor this engine started and
    that has ended is reaped here — a zombie would look alive."""
    if pid <= 0:
        return 0
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return 0
    except ChildProcessError:
        pass                                    # not ours: the kill below says
    except OSError:
        return 0
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return 0


def _outlives(pid: int, seconds: float = 3.0) -> int:
    """`pid` if it is still running a moment after being told to stop."""
    end = time.time() + seconds
    while _alive(pid) and time.time() < end:
        time.sleep(0.05)
    return _alive(pid)


def _swap_pid() -> int:
    """The supervisor of the last swap, while it's still alive."""
    try:
        return _alive(int((SELF_HOME / "swap.pid").read_text().strip()))
    except (OSError, ValueError):
        return 0


# ---- the line: nothing the engine runs is dropped by the next build -------------
#
# A healthy build is brought into your checkout — but only when that can
# happen cleanly, and edits you haven't committed stop it. Until then the
# engine runs something your checkout doesn't have, and a build made from
# your checkout would quietly drop it (2026-09-24: a Gemini change, for two
# minutes). So new work is built on your checkout *with* what's running, the
# missed merge is caught up as soon as your checkout allows, and `eki swap`
# refuses a build that would drop anything.

def _g(src: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(src), *a], capture_output=True, text=True, timeout=60)


def live() -> Dict[str, Any]:
    """What the engine runs — or is about to: the newest build on the
    release train, or a swap still waiting or swapping, counts as run,
    since it will be."""
    cars = train().get("cars") or []
    if cars and Path(cars[-1].get("build") or "").is_dir():
        return info(Path(cars[-1]["build"]))
    s = last_swap()
    if s.get("state") in ("waiting", "swapping") and s.get("target") and _swap_pid():
        return info(Path(s["target"]))
    cur = _target("current")
    return info(cur) if cur is not None else running()


def behind(src: Path, ref: str = "HEAD") -> str:
    """The commit the engine runs, when `ref` in your checkout doesn't have
    it — a build of `ref` would drop it. "" when nothing would be lost."""
    lv = live()
    commit = str(lv.get("commit") or "")
    if lv.get("dev") or not commit:
        return ""                   # running your checkout itself
    if _g(src, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        return ""                   # not from this checkout (a release)
    if _g(src, "merge-base", "--is-ancestor", commit, ref).returncode == 0:
        return ""
    # the same changes under other ids (cherry-picked, rebased) aren't lost:
    # `git cherry` marks each commit whose change `ref` already has with "-"
    left = _g(src, "cherry", ref, commit).stdout.split("\n")
    return commit if any(x.startswith("+") for x in left) else ""


def base(src: Path) -> str:
    """The commit new work goes on top of: your checkout's HEAD with what the
    engine runs in it. HEAD when it has it; the running commit when your
    checkout is only behind; otherwise the two merged — made in git's store,
    not in your files. ValueError when they conflict."""
    head = _g(src, "rev-parse", "HEAD").stdout.strip()
    missing = behind(src)
    if not missing:
        return head
    if _g(src, "merge-base", "--is-ancestor", head, missing).returncode == 0:
        return missing
    tree = _g(src, "merge-tree", "--write-tree", "--name-only", "--no-messages", head, missing)
    lines = tree.stdout.split()
    if tree.returncode != 0 or not lines:
        raise ValueError(f"your checkout and the running engine ({missing[:10]}) conflict in "
                         + (", ".join(lines[1:]) or "some files") + " — merge it into your checkout")
    made = _g(src, "-c", "user.name=eki", "-c", "user.email=eki@localhost", "commit-tree", lines[0],
              "-p", head, "-p", missing, "-m", "eki: your checkout, with what the engine runs")
    if made.returncode != 0:
        raise ValueError(f"couldn't put your checkout and the running engine together: {made.stderr.strip()[:160]}")
    return made.stdout.strip()


def catch_up(src: Path, commit: str = "") -> str:
    """Bring what the engine runs (or `commit`) into your checkout, if it's
    missing and your checkout allows. "" when nothing was missing;
    "merged …" when it went in; "not merged: …" when it's still waiting."""
    missing = commit if commit else behind(src)
    if not missing or _g(src, "merge-base", "--is-ancestor", missing, "HEAD").returncode == 0:
        return ""
    if _g(src, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return ("not merged: your checkout has uncommitted changes — the running engine's "
                "changes go in once they're committed")
    if _g(src, "merge-base", "--is-ancestor", "HEAD", missing).returncode == 0:
        got = _g(src, "merge", "--ff-only", "-q", missing)
        return "merged into your checkout" if got.returncode == 0 else f"not merged: {got.stderr.strip()[:160]}"
    got = _g(src, "-c", "user.name=eki", "-c", "user.email=eki@localhost", "merge", "--no-edit", "-q",
             "-m", "eki: bring in what the engine runs", missing)
    if got.returncode == 0:
        return "merged into your checkout, alongside your commits since"
    _g(src, "merge", "--abort")
    return (f"not merged: it conflicts with your commits since — merge {missing[:10]} "
            "into your checkout yourself")


def last_swap() -> Dict[str, Any]:
    try:
        return json.loads((SELF_HOME / "swap.json").read_text())
    except (OSError, ValueError):
        return {}


# ---- the release train ------------------------------------------------------------
#
# On 2026-09-24 eki went live six times in an hour, once per applied change,
# and every go-live cut off what was running beside it. Now an applied change
# boards the train, and the train leaves at most once every
# `self_release_minutes`, carrying everything applied since: each car's
# build is made on top of the one before (`live` counts the train), so the
# newest carries them all. A person's own apply can say "now".

def train() -> Dict[str, Any]:
    """{"cars": [{"self", "build", "commit", "at"}], "now": bool, "last": when
    the last one left, "departed": {"at", "build", "cars": [ids]}}"""
    try:
        t = json.loads((SELF_HOME / "train.json").read_text())
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_train(t: Dict[str, Any]) -> None:
    SELF_HOME.mkdir(parents=True, exist_ok=True)
    tmp = SELF_HOME / "train.json.tmp"
    tmp.write_text(json.dumps(t, indent=2))
    tmp.replace(SELF_HOME / "train.json")


def board(build: Path, *, self_id: str = "", now: bool = False) -> Dict[str, Any]:
    """A build for the next go-live. The same change boarding again (a
    restart made it apply again) takes its old seat, with the newer build."""
    t = train()
    cars = [c for c in t.get("cars") or [] if not self_id or c.get("self") != self_id]
    cars.append({"self": self_id, "build": str(build), "commit": info(Path(build)).get("commit") or "",
                 "at": int(time.time())})
    t.update(cars=cars, now=bool(t.get("now")) or bool(now))
    _save_train(t)
    return t


def departs_in(minutes: float = RELEASE_MINUTES, now: Optional[float] = None) -> Optional[int]:
    """Seconds until the train leaves; None with nobody aboard."""
    t = train()
    if not t.get("cars"):
        return None
    if t.get("now"):
        return 0
    return max(0, int(float(t.get("last") or 0) + minutes * 60 - (now or time.time())))


def depart(minutes: float = RELEASE_MINUTES, now: Optional[float] = None) -> Dict[str, Any]:
    """The train leaves, if it's time: the newest build to the supervisor,
    carrying every car. {} when it isn't time or nobody is aboard;
    RuntimeError (from `swap`) leaves the cars aboard."""
    left = departs_in(minutes, now)
    if left is None or left > 0:
        return {}
    t = train()
    cars = t["cars"]
    last = cars[-1]
    swap(Path(last["build"]), self_id=last.get("self") or "")
    gone = {"at": int(now or time.time()), "build": last["build"], "commit": last.get("commit") or "",
            "cars": [c.get("self") for c in cars if c.get("self")]}
    _save_train({"cars": [], "now": False, "last": gone["at"], "departed": gone})
    return gone


def settle_swap() -> Dict[str, Any]:
    """Once, after a swap has an outcome: bring a healthy self-change into
    your checkout if it can go in cleanly (fast-forward only, nothing
    uncommitted), and mark the outcome seen. Returns what was done."""
    s = last_swap()
    if not s or s.get("seen") or s.get("state") not in ("healthy", "rolled back", "failed"):
        return {}
    done: Dict[str, Any] = {"state": s["state"], "self": s.get("self") or "",
                            "why": s.get("why") or "", "target": s.get("target")}
    gone = train().get("departed") or {}
    if gone.get("build") and gone["build"] == s.get("target"):
        done["cars"] = list(gone.get("cars") or [])       # every change that train carried
    if s["state"] == "healthy" and s.get("self"):
        build = info(Path(s["target"]))
        # its change passed the candidate check, and it has run healthy since:
        # new work on this commit needn't test it all over again
        from . import selfwork
        selfwork.note_base_good(str(build.get("commit") or ""), SELF_HOME)
        src = Path(build.get("source") or source())
        done["merged"] = catch_up(src, build.get("commit") or "") or "merged into your checkout"
    s["seen"] = int(time.time())
    (SELF_HOME / "swap.json").write_text(json.dumps(s))
    return done


def prune(now: Optional[float] = None) -> List[str]:
    """Builds that are neither current nor previous, older than a week."""
    now = now or time.time()
    keep = {_target("current"), _target("previous")}
    gone = []
    for row in listing():
        p = Path(row["path"])
        if p in keep or row.get("dev") or now - float(row.get("made") or now) < KEEP_DAYS * 86400:
            continue
        shutil.rmtree(p, ignore_errors=True)
        gone.append(str(p))
    return gone


def install_supervisor() -> Path:
    """Copy the supervisor into place. Called by `eki agent install` — a
    person's command — and nowhere else."""
    SUPERVISOR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEMPLATE, SUPERVISOR)
    SUPERVISOR.chmod(0o755)
    return SUPERVISOR
