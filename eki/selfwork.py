# SPDX-License-Identifier: Apache-2.0
"""eki, working on eki.

    eki self "make `eki runs` show how long each run took"

eki's job is handing work to agents that can change code, and its own source
is code. So a change to eki is a run like any other — routed, quota-aware,
visible in the chat list — with three things added around it:

  a worktree   the agent never edits the checkout that is running. It gets a
               fresh `git worktree` of this repo on a branch `self/<id>`,
               under ~/.eki/self/.
  a base check before any quota is spent, the base has to pass its own
               tests — a change to something already broken can't be judged.
               A base that passed once is remembered for a day, runs that
               need the same base share one check, and a build that went
               live and stayed healthy counts as passed.
  a verdict    when the agent is done, its work is committed to the branch
               and put through the candidate check (eki/candidate.py).

The steps are separate so the engine can drive them around an ordinary run
(`begin` → the agent → `conclude`); `propose` does all three for a caller
that just wants the answer. What comes out is a branch, a diff and a report.

What happens to it then is `apply` (put it to work: a code change is built
and swapped in by the supervisor, watched, and rolled back if unhealthy — a
change to documentation only goes straight into your checkout), `discard`,
or — once applied — `undo`, which is a change of its own: the revert, judged
like any other. Where each change stands is kept in ~/.eki/self/changes.json.

Some paths eki may change but never on its own say-so (HARD_LOCKED) — the
supervisor and the way back, what launchd runs, the checker that judges it,
anything that holds or reads a credential. A proposal that touches them
says so, loudly, and is never applied by eki. The rest of its own machinery
— this file among it — is GUARDED: eki may apply such a change alone when
autonomy is apply, but only fully checked, judged again on top of your
checkout right before, and live on its own so a rollback undoes only it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import candidate, pipeline, roadmap, workers

HOME = Path("~/.eki/self").expanduser()
#: screenshots an agent takes of the app it changed, by change id
SHOTS = Path("~/.eki/shots").expanduser()

#: a diff touching these is for a person to read, whatever the checks say:
#: eki never applies it alone, at any autonomy
HARD_LOCKED = (
    "eki/builds.py", "eki/supervisor.sh",        # the swap and the way back
    "eki/agent.py", "eki/launcher.py",           # what launchd runs
    "eki/launcher.swift",
    "eki/candidate.py", "eki/drill.py",          # the judge: eki can't weaken its own checks
    "eki/secrets.py", "eki/quota/",              # the credentials rule
    "mac/sign.sh", "mac/hub.entitlements",
    "LICENSE", "NOTICE",
)
#: how eki builds itself: applied alone only when autonomy is apply, every
#: check passing (the restart check among them), judged again on top of
#: your checkout right before, and live on its own (the person decided,
#: 2026-09-25 — most improvements to the self-work sat waiting)
GUARDED = (
    "eki/selfwork.py", "eki/selfloop.py",        # who asks, and what it takes on
    "eki/selfengine.py",                         # how far it goes alone
    "eki/workers.py", "eki/steps.py",            # work a restart doesn't lose
    "eki/observe.py", "eki/roadmap.py",          # what it notices, what it works from
)
PROTECTED = HARD_LOCKED + GUARDED
#: checks a guarded change must have run and passed — never skipped
GUARDED_CHECKS = ("tests", "restart")
#: a guarded change on its way live holds the others back at most this long
ALONE_HOLD = 3600
#: a base that passed its tests is trusted for this long
BASE_OK_SECONDS = 24 * 3600
#: states a change can be in (changes.json)
STATES = ("not started", "no change", "stopped", "unfit", "proposed", "conflicts",
          "applying", "applied", "rolled back", "discarded", "undoing", "undone", "gone")
#: who wanted a change
SOURCES = ("asked", "fault", "roadmap", "note", "undo")

_lock = threading.Lock()


class SelfWorkError(RuntimeError):
    pass


@dataclass
class Proposal:
    id: str
    request: str
    root: str
    base: str = ""                   # the commit it started from
    branch: str = ""
    worktree: str = ""
    run: str = ""                    # the engine run that did the work
    backend: str = ""
    files: List[str] = field(default_factory=list)
    protected: List[str] = field(default_factory=list)
    locked: List[str] = field(default_factory=list)   # the part of `protected` that's HARD_LOCKED
    commit: str = ""
    report: Optional[Dict[str, Any]] = None
    verdict: str = ""
    fit: bool = False
    at: int = 0
    source: str = "asked"            # asked | fault | roadmap | note | undo
    title: str = ""                  # one line for lists; the request's first line if empty
    item: str = ""                   # the self-work item it was made for (eki/selfloop.py)
    conversation: str = ""           # the thread it was worked in
    said: str = ""                   # the agent's ITEM line (a roadmap item): done|partial|already|person
    ticks: str = ""                  # the ROADMAP item it ticks, by key
    reverts: str = ""                # an undo: the change it takes back
    #: what's new for you, in plain words — the agent's SUMMARY, for the
    #: thread, the notification and `eki self show`
    summary: str = ""
    #: conflicts eki had resolved when the change was put on top of your
    #: checkout: {"files": [...], "by": backend, "how": its words}
    resolved: Optional[Dict[str, Any]] = None
    #: "you" when a person asked for it to be applied (`eki self apply`, the
    #: board's Apply) — the only way a change touching PROTECTED gets in
    applied_by: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def headline(self) -> str:
        first = self.request.strip().splitlines()[0] if self.request.strip() else ""
        return (self.title or first)[:100]

    def lines(self) -> List[str]:
        out = [f"self/{self.id}: {self.headline}"]
        if self.summary:
            out += ["", *("  " + x for x in self.summary.splitlines()), ""]
        if self.resolved:
            out.append(f"  conflicts resolved by {self.resolved.get('by') or '?'} in "
                       + ", ".join(self.resolved.get("files") or []))
        if self.run:
            out.append(f"  done by   {self.backend or '?'} (run {self.run})")
        if self.files:
            out.append(f"  changed   {len(self.files)} file(s): " + ", ".join(self.files[:8])
                       + (" …" if len(self.files) > 8 else ""))
        if self.report:
            for c in self.report["checks"]:
                mark = "skip" if c["skipped"] else ("ok  " if c["ok"] else "FAIL")
                out.append(f"  {mark}  {c['name']:9} {c['detail']}".rstrip())
        if self.locked:
            out.append("  !! touches what eki may never change alone: " + ", ".join(self.locked))
        guarded = [f for f in self.protected if f not in self.locked]
        if guarded:
            out.append("  !  guarded — eki applies it alone only when autonomy is apply, "
                       "fully checked and on its own: " + ", ".join(guarded))
        if self.applied_by:
            out.append(f"  applied because {self.applied_by} asked for it")
        out.append(f"  → {self.verdict}")
        if self.commit:
            out += ["",
                    f"  read it    eki self diff {self.id}      (git -C {self.root} diff "
                    f"{self.base[:10]}..{self.branch or self.commit[:10]})",
                    f"  take it    eki self apply {self.id}     (git -C {self.root} merge {self.branch})",
                    f"  drop it    eki self discard {self.id}"]
        return out


# ---- git -------------------------------------------------------------------

def git(root: Path | str, *args: str) -> str:
    got = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if got.returncode != 0:
        raise SelfWorkError(f"git {' '.join(args[:3])}: {got.stderr.strip()[:300]}")
    return got.stdout.strip()


def _ok(root: Path | str, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True).returncode == 0


def open_worktree(root: Path, wid: str, base: str = "HEAD", home: Optional[Path] = None) -> Path:
    """A fresh checkout of `base` on branch self/<wid>, away from the one running."""
    try:
        git(root, "rev-parse", "--git-dir")
    except SelfWorkError:
        raise SelfWorkError(f"{root} isn't a git checkout — eki can only work on "
                            "itself where its source is a repo") from None
    where = (home or HOME) / wid
    where.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-q", "-b", f"self/{wid}", str(where), base)
    return where


def close_worktree(root: Path, where: Path, wid: str, keep_branch: bool = False) -> None:
    steps = [("worktree", "remove", "--force", str(where))]
    if not keep_branch:
        steps.append(("branch", "-D", f"self/{wid}"))
    for args in steps:
        try:
            git(root, *args)
        except SelfWorkError:
            pass


def changed(where: Path) -> List[str]:
    git(where, "add", "-A")
    names = git(where, "diff", "--cached", "--name-only")
    return [n for n in names.splitlines() if n.strip()]


def _under(files: List[str], paths: Tuple[str, ...]) -> List[str]:
    return [f for f in files
            if any(f == p or (p.endswith("/") and f.startswith(p)) for p in paths)]


def touches_protected(files: List[str]) -> List[str]:
    """Files in either tier: what a person is told about."""
    return _under(files, PROTECTED)


def touches_locked(files: List[str]) -> List[str]:
    """Files eki never applies alone, at any autonomy."""
    return _under(files, HARD_LOCKED)


def locked_of(c: Dict[str, Any]) -> List[str]:
    """A change's hard-locked files — worked out again for a record written
    before the tiers, when everything protected waited for a person."""
    return list(c.get("locked") or touches_locked(c.get("protected") or []))


def fully_checked(report: Dict[str, Any]) -> str:
    """"" when every check passed and none of GUARDED_CHECKS was skipped —
    what a guarded change needs to go in alone; else why not."""
    checks = {k.get("name"): k for k in report.get("checks") or []}
    for name in GUARDED_CHECKS:
        k = checks.get(name)
        if k is None or k.get("skipped"):
            return f"the {name} check didn't run"
    bad = [n for n, k in checks.items() if not k.get("ok")]
    return f"{', '.join(bad)} failed" if bad else ""


def alone_blocked(cid: str = "", home: Optional[Path] = None) -> str:
    """Why a guarded change can't go live alone right now: others aboard
    the release train, a swap on its way in, or another guarded change not
    yet settled. "" when it can."""
    from . import builds
    if any(car.get("self") != cid for car in builds.train().get("cars") or []):
        return "waiting for the release train to leave — a guarded change goes live on its own"
    if builds.going_live():
        return "waiting for the new version going live to settle — a guarded change goes live on its own"
    other = guarded_applying(home)
    if other and other != cid:
        return f"waiting for self/{other}, a guarded change going live alone, to settle"
    return ""


def guarded_applying(home: Optional[Path] = None, now: Optional[float] = None) -> str:
    """The guarded change on its way live alone, if there is one: nothing
    else goes live until it's settled, so a rollback undoes only it. It
    holds them at most ALONE_HOLD — a swap that never came can't hold
    everything forever."""
    now = now or time.time()
    for c in changes(home):
        if c.get("state") == "applying" and c.get("alone") \
                and now - float(c.get("alone_at") or 0) < ALONE_HOLD:
            return c["id"]
    return ""


def docs_only(files: List[str]) -> bool:
    """Nothing that runs: Markdown outside the package. Such a change needs no
    candidate engine and no swap — it goes straight into your checkout."""
    return bool(files) and all(f.endswith(".md") and not f.startswith("eki/") for f in files)


# ---- the brief ---------------------------------------------------------------

def brief(request: str, python: str, where: str = "") -> str:
    """What the agent is told. It knows it's working on eki, where the tests
    are, and that nobody is there to answer a question."""
    here = f" ({where} — make every change there)" if where else ""
    shots = SHOTS / Path(where).name if where else SHOTS / "change"
    return (
        f"You are working on eki's own source code, in a git worktree made for this "
        f"one change{here}. eki is the program that sent you this request.\n\n"
        f"The change: {request.strip()}\n\n"
        "How to work here:\n"
        "- Read README.md and the modules you touch first; match their style — "
        "plain docstrings that say why, small functions, no new dependencies.\n"
        "- Add or adjust tests under tests/ for what you change.\n"
        f"- Run the tests with `{python} -m pytest -q` and leave them passing.\n"
        "- If you change the Mac app (mac/), make sure it compiles: "
        f"`{python} -c \"from eki import appbuild; print(appbuild.typecheck('.'))\"` — every "
        "mac/*.swift file is part of the app, so new files need no build-script edit.\n"
        "- If you change how the app looks, look at it — before you start and after, and "
        "keep going until the difference shows. With eki's screen tools (eki_screenshot, "
        "eki_click, eki_key): build this worktree's app to a scratch path "
        "(`cd mac && EKI_APP_PATH=/tmp/eki-look.app ./build_app.sh`), open it as a second "
        "window (`open -n /tmp/eki-look.app`, dark: `open -n /tmp/eki-look.app --args "
        "-AppleInterfaceStyle Dark`), put the view you changed in front, and take a "
        "screenshot; quit it (`pkill -f /tmp/eki-look.app`) before the next build. The "
        "person's own Eki.app shows the old code — never judge by it. Save your pictures as "
        f"PNG in {shots}/ named before-<view>-<light|dark>.png and "
        "after-<view>-<light|dark>.png; eki shows them with your change.\n"
        "- Don't commit, push, or touch git branches; eki commits your work itself.\n"
        "- Don't tick items in ROADMAP.md (`- [ ]` → `- [x]`); eki ticks an item itself, once "
        "your change has landed.\n"
        "- Don't ask questions — nobody is watching. If something is ambiguous, take "
        "the smaller reading and say so in your final message.\n"
        "- Leave these alone unless the change is about them: "
        + ", ".join(PROTECTED) + ".\n"
        "- Finish with two or three sentences: what you changed and anything a "
        "reviewer should look at.\n"
        "- Then a summary for the person, after a line that says only `SUMMARY:` — three to "
        "six short lines in plain words, no code: what's new or different for them, how to "
        "use it (a command, or where to click), and anything to check before applying it.\n"
    )


SUMMARY_LINE = re.compile(r"^\s*\**SUMMARY:?\**\s*:?\s*$", re.M | re.I)


def summary_of(answer: str, limit: int = 900) -> str:
    """The agent's plain-words summary: what follows its `SUMMARY:` line, up
    to an `ITEM:` line. "" when it didn't write one."""
    found = list(SUMMARY_LINE.finditer(answer or ""))
    if not found:
        return ""
    text = answer[found[-1].end():]
    lines = []
    for line in text.strip().splitlines():
        if re.match(r"^\s*`?ITEM:", line):
            break
        lines.append(line.rstrip())
    out = "\n".join(lines).strip()
    return out if len(out) <= limit else out[:limit].rsplit("\n", 1)[0].rstrip() + "\n…"


def slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return words[:60].rstrip("-") or "change"


# ---- the record ----------------------------------------------------------------

def record(p: Proposal, home: Optional[Path] = None) -> None:
    path = (home or HOME) / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a") as f:
        f.write(json.dumps(p.to_json()) + "\n")


def history(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = (home or HOME) / "log.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _states_path(home: Optional[Path]) -> Path:
    return (home or HOME) / "changes.json"


def _states(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_states_path(home).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_state(cid: str, state: str, home: Optional[Path] = None, **extra: Any) -> Dict[str, Any]:
    """Where a change stands now, and anything worth saying about how it got there."""
    with _lock:
        data = _states(home)
        entry = {**data.get(cid, {}), **{k: v for k, v in extra.items() if v is not None},
                 "state": state, "at": int(time.time())}
        data[cid] = entry
        path = _states_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
    return entry


def first_state(e: Dict[str, Any]) -> str:
    """Where a change stood when it was written down."""
    verdict = e.get("verdict") or ""
    if verdict.startswith("not started"):
        return "not started"
    if not e.get("commit"):
        return "no change" if "changed nothing" in verdict else "stopped"
    return "proposed" if e.get("fit") else "unfit"


def changes(home: Optional[Path] = None, limit: int = 0) -> List[Dict[str, Any]]:
    """Every change eki made to itself, newest first, with where it stands.
    The last record of a change wins (a rebase at apply writes a new one)."""
    latest: Dict[str, Dict[str, Any]] = {}
    seq: Dict[str, int] = {}
    for n, e in enumerate(history(home)):
        if e.get("id"):
            latest[e["id"]] = e
            seq[e["id"]] = n
    states = _states(home)
    out = []
    for cid, e in latest.items():
        st = states.get(cid) or {}
        row = {**e, **{k: v for k, v in st.items() if k not in ("state", "at")}}
        row["state"] = st.get("state") or first_state(e)
        row["state_at"] = st.get("at") or e.get("at") or 0
        row["title"] = e.get("title") or (e.get("request") or "").strip().split("\n")[0][:100]
        row["locked"] = locked_of(row)
        # records from before the loop don't say who wanted them; a fault's brief does
        row.setdefault("source", "fault" if (e.get("request") or "").startswith("Fix a fault eki observed")
                       else "asked")
        out.append(row)
    # newest news first; within a second, the one written down last
    out.sort(key=lambda c: (-int(c.get("state_at") or 0), -seq.get(c["id"], 0)))
    return out[:limit] if limit else out


def change(cid: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """One change, by id or the start of it."""
    rows = changes(home)
    exact = [c for c in rows if c["id"] == cid]
    found = exact or [c for c in rows if cid and c["id"].startswith(cid)]
    if len(found) != 1:
        raise SelfWorkError(f"no change {cid!r}" if not found else f"{cid!r} could be several changes")
    return found[0]


_RECONCILED = {"at": 0.0}


def reconcile(home: Optional[Path] = None, force: bool = False) -> List[str]:
    """Proposals decided outside eki: merged by hand (applied), or their
    branch deleted (gone). Written down, so each is looked at in git once.
    At most once a minute unless `force`. Returns the ids that moved."""
    now = time.time()
    if not force and now - _RECONCILED["at"] < 60:
        return []
    _RECONCILED["at"] = now
    moved = []
    for c in changes(home):
        if c["state"] not in ("proposed", "conflicts") or not c.get("commit"):
            continue
        root = Path(c["root"])
        branch = c.get("branch") or f"self/{c['id']}"
        if not (root / ".git").exists():
            set_state(c["id"], "gone", home, why=f"{root} isn't a checkout any more")
        elif is_in(root, c["commit"], "HEAD"):
            set_state(c["id"], "applied", home, how="merged into your checkout by hand")
        elif not _ok(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            set_state(c["id"], "gone", home, why="its branch was deleted outside eki")
        else:
            continue
        moved.append(c["id"])
    return moved


def waiting(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Fit changes nobody has decided on yet — what eki is waiting for you to look at."""
    return [c for c in changes(home) if c["state"] in ("proposed", "conflicts") and c.get("fit")]


# ---- a base known to be good -----------------------------------------------------

def base_known_good(sha: str, home: Optional[Path] = None, now: Optional[float] = None) -> bool:
    try:
        seen = json.loads(((home or HOME) / "base-ok.json").read_text())
    except (OSError, ValueError):
        return False
    return (now or time.time()) - float(seen.get(sha) or 0) < BASE_OK_SECONDS


def note_base_good(sha: str, home: Optional[Path] = None) -> None:
    """Remember `sha` passed its own tests — a check that passed, or a build
    that went live and stayed healthy (its change passed the candidate check
    before it was swapped in)."""
    if not sha:
        return
    path = (home or HOME) / "base-ok.json"
    with _lock:
        try:
            seen = json.loads(path.read_text())
        except (OSError, ValueError):
            seen = {}
        now = time.time()
        seen = {k: v for k, v in seen.items() if now - float(v) < BASE_OK_SECONDS}
        seen[sha] = int(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen))


def _base_failed(sha: str, since: float, home: Optional[Path]) -> str:
    """Why `sha` failed its check, if it failed one that ended after `since`."""
    try:
        got = json.loads(((home or HOME) / "base-bad.json").read_text()).get(sha) or {}
    except (OSError, ValueError):
        return ""
    return str(got.get("why") or "") if float(got.get("at") or 0) >= since else ""


def _note_base_bad(sha: str, why: str, home: Optional[Path]) -> None:
    path = (home or HOME) / "base-bad.json"
    with _lock:
        try:
            seen = json.loads(path.read_text())
        except (OSError, ValueError):
            seen = {}
        now = time.time()
        seen = {k: v for k, v in seen.items() if now - float(v.get("at") or 0) < BASE_OK_SECONDS}
        seen[sha] = {"at": now, "why": why}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen))


def check_base_once(where: Path, sha: str, python: str, home: Optional[Path] = None,
               tests: Optional[Callable[[Path, str], Any]] = None) -> str:
    """The base passing its own tests, checked once per commit: "" when it
    passes, else why not. After a swap every run wants the same new base at
    once (2026-09-24: five full test runs side by side, each slowing the
    others); one takes the commit's lock and runs the check, the rest wait
    for it and take its answer — a pass, or a failure that ended while they
    waited. The lock is a file, so a CLI `eki self` waits too."""
    import fcntl
    tests = tests or candidate.check_tests
    if base_known_good(sha, home):
        return ""
    since = time.time()
    locks = (home or HOME) / "base-checks"
    locks.mkdir(parents=True, exist_ok=True)
    with open(locks / f"{sha}.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if base_known_good(sha, home):
                return ""
            why = _base_failed(sha, since, home)
            if why:
                return why
            try:
                tests(where, python)
            except RuntimeError as e:
                _note_base_bad(sha, str(e), home)
                return str(e) or "its tests failed"
            note_base_good(sha, home)
            return ""
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---- the steps -----------------------------------------------------------------------

Ask = Callable[[str, str], Dict[str, str]]
"""(prompt, folder) → {"state", "run", "backend"}; blocks until the run ends."""
Say = Optional[Callable[[str], None]]


def begin(request: str, *, root: Path, base: str = "HEAD", python: Optional[str] = None,
          home: Optional[Path] = None, check_base: bool = True, say: Say = None,
          passed: Optional[Callable[[str], None]] = None,
          opened: Optional[Callable[[Proposal], None]] = None,
          resume: Optional[Proposal] = None, **meta: Any) -> Proposal:
    """Open a change: its worktree, and — before anything is spent — the base
    passing its own tests. A proposal that comes back with a verdict didn't
    start (and is already written down). `opened` hears the proposal as soon
    as its worktree exists, and `resume` is that proposal again after a
    restart cut the base check off: the check runs again in the same
    worktree, nothing opened twice. `passed` hears the base once it has
    passed (or wasn't checked), so a run cut off here — by a swap — can start
    again from that base instead of checking a new one. A check cut off
    raises steps.Interrupted: no verdict, the worktree kept."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    if not request.strip():
        raise SelfWorkError("say what to change")
    from . import builds
    why = builds.cant_change_code(root)
    if why:
        raise SelfWorkError(why)
    if resume is not None and resume.worktree and _ok(resume.worktree, "rev-parse", "--git-dir"):
        p, where, wid = resume, Path(resume.worktree), resume.id
        say(f"worktree: {where}  (branch {p.branch}, from {p.base[:10]}) — again, after a restart")
    else:
        wid = uuid.uuid4().hex[:8]
        known = {k: v for k, v in meta.items() if k in Proposal.__dataclass_fields__}
        p = Proposal(id=wid, request=request.strip(), root=str(root), at=int(time.time()), **known)
        where = open_worktree(root, wid, base, home)
        p.branch, p.worktree = f"self/{wid}", str(where)
        p.base = git(where, "rev-parse", "HEAD")
        say(f"worktree: {where}  (branch {p.branch}, from {p.base[:10]})")
        if opened is not None:
            opened(p)
    if check_base and not base_known_good(p.base, home):
        say("checking the base passes its own tests, before spending anything…")
        why = check_base_once(where, p.base, python, home)
        if why:
            close_worktree(root, where, wid)
            p.worktree = p.branch = ""
            p.verdict = (f"not started: the base ({p.base[:10]}) doesn't pass its own tests, "
                         f"so a change to it can't be judged — {why}")[:500]
            record(p, home)
            return p
    if passed is not None:
        passed(p.base)
    return p


def conclude(p: Proposal, got: Dict[str, str], *, python: Optional[str] = None,
             home: Optional[Path] = None, check: Callable[..., candidate.Report] = candidate.check,
             say: Say = None) -> Proposal:
    """The agent is done: commit what it did and judge it. Safe to do again:
    a check cut off by a restart (steps.Interrupted) leaves the commit, and
    the next try judges that commit rather than finding nothing to commit.
    A tick the agent put in ROADMAP.md is taken out — the merge queue ticks
    the item once the change lands."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    root, where = Path(p.root), Path(p.worktree)
    p.run = got.get("run") or p.run
    p.backend = got.get("backend") or p.backend

    def finish(verdict: str, keep: bool = True) -> Proposal:
        p.verdict = verdict
        if not keep:
            close_worktree(root, where, p.id)
            p.worktree = p.branch = ""
        record(p, home)
        return p

    if got.get("state") != "done":
        return finish(f"the agent's run ended {got.get('state') or 'without a state'}; "
                      "whatever it wrote is still in the worktree")
    pending = changed(where)
    if roadmap.NAME in pending and p.base and not asks_to_tick(p.request, p.source) and drop_ticks(where, p.base):
        pending = changed(where)
    if pending:
        why = {"fault": "A fault eki observed in itself", "roadmap": "The next item in ROADMAP.md",
               "note": "Picked from eki's weekly note", "undo": f"Taking back self/{p.reverts}"
               }.get(p.source, "Asked through `eki self`")
        git(where, "-c", "user.name=eki", "-c", "user.email=eki@localhost",
            "commit", "-q", "--no-verify", "-m",
            f"self: {p.headline.splitlines()[0][:68]}\n\n{why}; written by "
            f"{p.backend or 'an agent'} in run {p.run or '?'}.\nNot merged by eki.")
    head = git(where, "rev-parse", "HEAD")
    # committed before a restart cut its check off: what it holds is the change
    p.files = [f for f in git(where, "diff", "--name-only", p.base, head).splitlines() if f] \
        if p.base and head != p.base else pending
    if not p.files:
        return finish("the agent changed nothing", keep=False)
    p.protected, p.locked = touches_protected(p.files), touches_locked(p.files)
    p.commit = head

    if docs_only(p.files):
        report = candidate.Report(str(where), [candidate.Check(
            "docs", True, "only documentation changed — nothing to run")])
    else:
        say("judging the result…")
        report = check(where, python=python, say=say)
    p.report, p.fit = report.to_json(), report.fit
    if not report.fit:
        return finish("not fit to run — the branch is kept so the failure can be read")
    if p.locked:
        return finish("passes, but touches protected paths eki may never change alone: "
                      "for a person to read line by line before merging")
    if p.protected:
        return finish("fit to run — a guarded change: eki applies it alone only when autonomy "
                      "is apply, judged again first and live on its own")
    return finish("fit to run — proposed, not merged")


def propose(request: str, *, root: Path, ask: Ask, base: str = "HEAD",
            python: Optional[str] = None, home: Optional[Path] = None,
            check: Callable[..., candidate.Report] = candidate.check,
            check_base: bool = True, say: Say = None, **meta: Any) -> Proposal:
    """One change to eki, from request to verdict. Never merges anything."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    p = begin(request, root=root, base=base, python=python, home=home,
              check_base=check_base, say=say, **meta)
    if p.verdict:
        return p
    say("handing it to an agent…")
    got = ask(brief(request, python), p.worktree)
    return conclude(p, got, python=python, home=home, check=check, say=say)


# ---- after the verdict: apply, discard, undo ----------------------------------------

def ensure_worktree(c: Dict[str, Any], home: Optional[Path] = None) -> Path:
    """The change's worktree, made again from its branch (or commit) if it's gone."""
    root, cid = Path(c["root"]), c["id"]
    where = Path(c.get("worktree") or ((home or HOME) / cid))
    if where.is_dir() and _ok(where, "rev-parse", "--git-dir"):
        return where
    git(root, "worktree", "prune")
    where = (home or HOME) / cid
    branch = c.get("branch") or f"self/{cid}"
    if _ok(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
        git(root, "worktree", "add", "-q", str(where), branch)
    elif c.get("commit") and _ok(root, "cat-file", "-e", f"{c['commit']}^{{commit}}"):
        git(root, "worktree", "add", "-q", "-b", branch, str(where), c["commit"])
    else:
        raise SelfWorkError(f"self/{cid} is gone — its branch and commit aren't in {root} any more")
    return where


def is_in(root: Path | str, commit: str, ref: str = "HEAD") -> bool:
    """`commit` is part of `ref`'s history."""
    return bool(commit) and _ok(root, "merge-base", "--is-ancestor", commit, ref)


def line(root: Path) -> str:
    """What a change goes on top of: your checkout's HEAD with whatever the
    engine runs in it (eki/builds.py `base`) — never a base that would drop
    a change already running because your checkout hadn't taken it yet."""
    from . import builds
    try:
        return builds.base(root)
    except ValueError as e:
        raise SelfWorkError(str(e)) from None
    except (OSError, subprocess.SubprocessError):
        return git(root, "rev-parse", "HEAD")


def fast_forward(root: Path, commit: str) -> str:
    """Bring `commit` into your checkout, fast-forward only. Files you haven't
    added to git don't stop it; edits to tracked files do."""
    if not commit:
        return "no commit to bring in"
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        return "not merged: your checkout has uncommitted changes"
    if not _ok(root, "merge-base", "--is-ancestor", "HEAD", commit):
        return "not merged: your checkout has moved on"
    got = subprocess.run(["git", "-C", str(root), "merge", "--ff-only", "-q", commit],
                         capture_output=True, text=True)
    return "merged into your checkout" if got.returncode == 0 else \
        f"not merged: {got.stderr.strip()[:160]}"


# ---- when a change no longer goes on top: its conflicts, resolved -----------------
#
# Your checkout moves on while a change waits (another change applied, your
# own commits). Apply puts the change on top first; when that stops at a
# conflict, eki doesn't hand it back to you — the engine asks an agent to
# resolve the conflicted files in the change's own worktree, mid-rebase,
# then eki finishes the rebase, checks nothing is left, judges the change
# again and applies it (eki/selfengine.py `_self_resolve`).

CONFLICT_MARK = re.compile(r"^(<<<<<<< |>>>>>>> )", re.M)


def _git_path(where: Path, name: str) -> Path:
    p = Path(git(where, "rev-parse", "--git-path", name))
    return p if p.is_absolute() else Path(where) / p


def rebasing(where: Path) -> bool:
    """A rebase is stopped in this worktree."""
    return _git_path(where, "rebase-merge").exists() or _git_path(where, "rebase-apply").exists()


def unmerged(where: Path) -> List[str]:
    return [f for f in git(where, "diff", "--name-only", "--diff-filter=U").splitlines() if f]


def marked(where: Path, files: List[str]) -> List[str]:
    """Files that still hold conflict markers."""
    out = []
    for f in files:
        try:
            if CONFLICT_MARK.search((Path(where) / f).read_text(errors="replace")):
                out.append(f)
        except OSError:
            pass
    return out


def _put_back(where: Path, commit: str) -> None:
    """The change as it was before the attempt: no rebase, its own commit."""
    if rebasing(where):
        subprocess.run(["git", "-C", str(where), "rebase", "--abort"], capture_output=True)
    if commit:
        subprocess.run(["git", "-C", str(where), "reset", "-q", "--hard", commit], capture_output=True)


# ---- the ROADMAP tick: never part of a change ------------------------------------
#
# A change used to tick its roadmap item in its own commit. Put on top of a
# checkout whose ROADMAP.md had moved on — other changes' ticks, landing
# back to back — that hunk conflicted, and an agent resolving the rest could
# drop it. Now the tick is written once, by the merge queue, as its own
# commit after the change lands (eki/selfengine.py `_self_ticks`); a change
# carries only real edits to the file, and a tick in one (an agent's, or a
# change made before this) is taken out before it's put on top. Nor does a
# change take a tick back: one written from an older copy of the file (or
# resolved to the older side) has the ticks of the checkout it lands on put
# back first (`keep_ticks`), and the writer ticks any landed item it finds
# open again (`write_ticks`).

def tick_of(c: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(key, mark) of the roadmap item the change finishes, or None."""
    if c.get("ticks") and c.get("said") in ("done", "already"):
        return c["ticks"], roadmap.mark(c["id"])
    return None


#: a request that is itself about ticking the roadmap ("tick the item …"):
#: its ticks are what it was asked for, and stay
_TICK_ASKED = re.compile(r"\b(?:un)?tick(?:\s+off)?\s+(?:the\b|its\b|an?\b|items?\b|roadmap\b|['\"“‘*`])"
                         r"|\bcheck off\b|\bmark (?:it|them|\w+ items?) (?:as )?done\b", re.I)


_NOT = re.compile(r"(n't|\bnot|\bnever|\bno longer|\bwithout)\s+(\w+\s+)?$", re.I)


def asks_to_tick(request: str, source: str = "asked") -> bool:
    """Does a person's request ask for a tick ("tick 'Roots eki starts…'")
    — not merely mention one ("don't tick", "no longer tick")? A roadmap
    item's own request never does: its tick is the merge queue's to write."""
    if source == "roadmap":
        return False
    text = request or ""
    return any(not _NOT.search(text[max(0, m.start() - 24):m.start()])
               for m in _TICK_ASKED.finditer(text))


def asks_to_untick(request: str, source: str = "asked") -> bool:
    """Does a person's request ask to open a ticked item again ("untick …")?
    Only then may a change land with fewer ticks than the file it goes on."""
    if source == "roadmap":
        return False
    text = request or ""
    return any(m.group(0).lower().startswith("untick")
               and not _NOT.search(text[max(0, m.start() - 24):m.start()])
               for m in _TICK_ASKED.finditer(text))


def keep_ticks(where: Path, onto: str) -> bool:
    """The change's last commit, with every item ticked at `onto` (the
    checkout it lands on) ticked in it too — an old copy of a ticked line,
    carried by a docs edit or kept by a conflict resolution, is fixed before
    it lands. True if it had to be."""
    path = Path(where) / roadmap.NAME
    try:
        before = git(where, "show", f"{onto}:{roadmap.NAME}")
        after = path.read_text()
    except (SelfWorkError, OSError):
        return False
    fixed = roadmap.keep_ticks(before, after)
    if fixed == after:
        return False
    path.write_text(fixed)
    git(where, "add", "--", roadmap.NAME)
    git(where, "-c", "user.name=eki", "-c", "user.email=eki@localhost",
        "commit", "-q", "--amend", "--no-edit", "--no-verify", "--allow-empty")
    return True


def drop_ticks(where: Path, base: str) -> bool:
    """Items open at `base` and ticked in the worktree's ROADMAP.md, open
    again — the file's other edits kept. True if the file changed."""
    path = Path(where) / roadmap.NAME
    try:
        before = git(where, "show", f"{base}:{roadmap.NAME}")
        after = path.read_text()
    except (SelfWorkError, OSError):
        return False
    fixed = roadmap.without_new_ticks(before, after)
    if fixed == after:
        return False
    path.write_text(fixed)
    return True


def _untick_commit(where: Path, base: str) -> bool:
    """A committed change that ticks ROADMAP.md: the tick taken out of its
    last commit. True if it had one."""
    if not drop_ticks(where, base):
        return False
    git(where, "add", "--", roadmap.NAME)
    if not git(where, "diff", "--cached", "--name-only"):
        return False
    git(where, "-c", "user.name=eki", "-c", "user.email=eki@localhost",
        "commit", "-q", "--amend", "--no-edit", "--no-verify")
    return True


def landed(c: Dict[str, Any]) -> bool:
    """The change is in: applied, or its agent found the item already done."""
    return c["state"] == "applied" or (c["state"] == "no change" and not c.get("commit")
                                       and c.get("said") in ("done", "already"))


def landed_keys(rows: List[Dict[str, Any]]) -> List[str]:
    """Keys of the roadmap items a change landed for — never taken again on
    their own, whatever ROADMAP.md says about them."""
    return [c["ticks"] for c in rows if c.get("ticks") and c["state"] == "applied"]


def write_ticks(root: Path, home: Optional[Path] = None) -> List[str]:
    """The one writer of ROADMAP.md ticks. An item whose change has landed —
    or that its agent found already done — is ticked in your checkout, as a
    small commit of its own; one whose change was undone is opened again.
    Each once (ticks.json). While your checkout has edits of its own to
    ROADMAP.md, or git is mid-merge there, it waits. What it wrote, as
    "tick self/<id>" / "untick self/<id>" / "retick self/<id>".

    Then every landed item's tick is asserted again: an item the file shows
    open whose change is still in — ticked by this writer, or ticked by hand
    since its change landed — is ticked again. A change that carried an old
    copy of the line, or a conflict resolved to the older side, took it
    back; nothing else may."""
    path = (home or HOME) / "ticks.json"
    everything = list(reversed(changes(home)))                         # oldest first
    rows = [c for c in everything if tick_of(c)]
    try:
        done: Dict[str, str] = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    wrote: List[str] = []
    ready = True
    for c in rows:
        was = done.get(c["id"], "")
        if landed(c) and not was:
            act = "tick"
        elif c["state"] == "undone" and was == "ticked":
            act = "untick"
        else:
            continue
        got = _roadmap_commit(Path(root), c, act)
        if got is None:
            ready = False
            break                                   # your checkout isn't ready: later
        done[c["id"]] = act + "ed"
        if got:
            wrote.append(f"{act} self/{c['id']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(done, indent=2))
    if ready:
        wrote += _reassert_ticks(Path(root), everything, done)
    return wrote


def _reassert_ticks(root: Path, rows: List[Dict[str, Any]], done: Dict[str, str]) -> List[str]:
    """Landed items the file shows open, ticked again (see write_ticks)."""
    wrote: List[str] = []
    text = roadmap.read(root)
    for c in rows:
        key = c.get("ticks") or ""
        if not key or not landed(c):
            continue
        item = roadmap.find(text, key)
        if item is None or item.done:
            continue
        if tick_of(c):
            if done.get(c["id"]) != "ticked":
                continue
        elif c["state"] != "applied" or not ticked_since(root, key, c.get("commit") or ""):
            continue
        got = _roadmap_commit(root, c, "retick", key=key)
        if got is None:
            break
        if got:
            wrote.append(f"retick self/{c['id']}")
            text = roadmap.read(root)
    return wrote


def ticked_since(root: Path, key: str, since: str, limit: int = 200) -> bool:
    """Was the item ticked in any ROADMAP.md your checkout has had since
    `since` (a change's commit)? A slice a person ticked by hand counts as
    landed from then on."""
    if not since or not _ok(root, "cat-file", "-e", f"{since}^{{commit}}"):
        return False
    try:
        shas = git(root, "log", f"-{limit}", "--format=%H", f"{since}..HEAD", "--", roadmap.NAME).split()
    except SelfWorkError:
        return False
    for sha in shas:
        try:
            item = roadmap.find(git(root, "show", f"{sha}:{roadmap.NAME}"), key)
        except SelfWorkError:
            continue
        if item is not None and item.done:
            return True
    return False


def _roadmap_commit(root: Path, c: Dict[str, Any], act: str, key: str = "") -> Optional[str]:
    """Tick (or untick, or tick again) the change's item in
    `root`/ROADMAP.md and commit just that file. The commit, "" when there
    was nothing to do, None when the checkout isn't ready for it."""
    found = tick_of(c)
    key = key or (found[0] if found else "")
    mark = roadmap.mark(c["id"])
    file = Path(root) / roadmap.NAME
    try:
        if git(root, "status", "--porcelain", "--", roadmap.NAME):
            return None                             # your own edits to it come first
        text = file.read_text()
    except (SelfWorkError, OSError):
        return None
    after = roadmap.untick(text, key, mark) if act == "untick" else roadmap.tick(text, key, mark)
    if after == text:
        return ""
    file.write_text(after)
    title = (c.get("title") or key)[:60]
    said = {"tick": "landed", "untick": "was undone",
            "retick": "landed; a later change had taken the tick back"}[act]
    verb = "tick again" if act == "retick" else act
    try:
        git(root, "-c", "user.name=eki", "-c", "user.email=eki@localhost", "commit", "-q",
            "--no-verify", "-m", f"roadmap: {verb} “{title}” — self/{c['id']} {said}", "--", roadmap.NAME)
    except SelfWorkError:
        file.write_text(text)                       # mid-merge, say: as it was, and later
        return None
    return git(root, "rev-parse", "--short", "HEAD")


def _rebase(where: Path, *args: str) -> Tuple[subprocess.CompletedProcess, List[str]]:
    """`git rebase <args>`: what git said, and the files still conflicting."""
    cmd = ["git", "-C", str(where), "-c", "user.name=eki", "-c", "user.email=eki@localhost",
           "-c", "core.editor=true", "rebase"]
    env = {**os.environ, "GIT_EDITOR": "true"}
    # a worker (eki/workers.py): a restart doesn't kill it halfway, and the
    # step taken up again joins it rather than starting a second rebase over
    # the first (one that ended is not taken: the step puts things back first)
    code, out, err = workers.run(cmd + list(args), cwd=str(where), env=env, kind="git", finished=False,
                                 key=workers.key_of("rebase", os.path.realpath(where), *args))
    got = subprocess.CompletedProcess(cmd + list(args), code, out, err)
    return got, (unmerged(where) if got.returncode and rebasing(where) else [])


def begin_rebase(cid: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """Start putting the change on top of your checkout, and stop at its
    conflicts. {"where", "onto", "files"} — files [] means it went on cleanly."""
    c = change(cid, home)
    root = Path(c["root"])
    where = ensure_worktree(c, home)
    onto = line(root)
    _put_back(where, "")                        # a rebase left stopped by an earlier try
    if c.get("base") and not asks_to_tick(c.get("request") or "", c.get("source") or "") and _untick_commit(where, c["base"]):
        c["commit"] = git(where, "rev-parse", "HEAD")
    got, files = _rebase(where, "-q", onto)
    if got.returncode and not files:
        _put_back(where, c.get("commit") or "")
        why = (got.stderr.strip() or got.stdout.strip()).splitlines()
        raise SelfWorkError("it couldn't be put on top of your checkout: " + (why[-1] if why else "git failed"))
    return {"where": str(where), "onto": onto, "files": files, "commit": c.get("commit") or ""}


def landed_since(cid: str, onto: str, files: List[str], home: Optional[Path] = None) -> str:
    """What your checkout gained in those files since the change was made."""
    c = change(cid, home)
    base = c.get("base") or ""
    if not base or base == "HEAD":
        return ""
    got = subprocess.run(["git", "-C", c["root"], "log", "--oneline", "--no-decorate",
                          f"{base}..{onto}", "--", *files], capture_output=True, text=True)
    return got.stdout.strip()[:1500]


def resolve_brief(c: Dict[str, Any], files: List[str], landed: str, python: str, where: str,
                  step: str = "") -> str:
    """What the agent is told when a change stops on conflicts. `step`: the
    later commit it stopped on, once an earlier one was resolved and went on."""
    what = c.get("summary") or c.get("title") or (c.get("request") or "").strip()[:600]
    now = (f"The earlier conflicts are resolved and committed; the rebase went on and stopped "
           f"again, at its commit “{step}” — conflicts in: {', '.join(files)}.\n\n") if step else \
        (f"Putting it on top of the current one — a git rebase, stopped now — conflicts in: "
         f"{', '.join(files)}.\n\n")
    return (
        f"You are resolving conflicts in eki's own source code, in {where}.\n\n"
        f"eki's change self/{c['id']} was made on an older checkout. " + now
        + f"What the change does:\n{what}\n\n"
        + (f"What the checkout gained in those files since:\n{landed}\n\n" if landed else "")
        + "How to work here:\n"
        "- Resolve every <<<<<<< / ======= / >>>>>>> block in those files so both sides' intent "
        "survives: the checkout's side is what's current; the change's side is what it adds.\n"
        "- Touch only what the conflicts need. Don't redo or extend the change. Whatever you "
        "edit is added to this commit, so leave no scratch files behind.\n"
        "- Don't run `git rebase --continue`, `--abort`, commit, or touch branches — eki "
        "finishes the rebase itself once the files are clean.\n"
        f"- Run the tests with `{python} -m pytest -q` and leave them passing.\n"
        "- Don't ask questions — nobody is watching.\n"
        "- Finish with one line per file: how you resolved it.\n")


def touched(where: Path) -> List[str]:
    """What the worktree holds beyond HEAD: staged, unstaged, and new files
    git doesn't ignore — everything an agent may have edited mid-rebase."""
    changed = git(where, "diff", "--name-only", "HEAD").splitlines()
    new = git(where, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({f for f in changed + new if f})


def stopped_at(where: Path) -> str:
    """The subject of the commit a rebase is stopped on."""
    try:
        return git(where, "log", "-1", "--format=%s", "REBASE_HEAD")
    except SelfWorkError:
        return ""


def continue_rebase(info: Dict[str, Any]) -> List[str]:
    """After the agent: add everything it resolved — the conflicted files and
    whatever else it had to touch, since a rebase won't go on past a file left
    unstaged — and go on, commit by commit. [] once the rebase is done; when a
    later commit stops on conflicts of its own, their files (and `info` now
    points at them), for the agent to resolve in the same run. Anything else
    that stops it is a real failure: the change is put back exactly as it was
    and SelfWorkError says why."""
    where = Path(info["where"])
    try:
        if not rebasing(where):
            return []
        still = marked(where, sorted(set(info.get("files") or []) | set(touched(where))))
        if still:
            raise SelfWorkError("conflict markers are still in " + ", ".join(still))
        git(where, "add", "-A")
        left = unmerged(where)
        if left:
            raise SelfWorkError("still conflicting: " + ", ".join(left))
        info["resolved"] = sorted(set(info.get("resolved") or []) | set(info.get("files") or []))
        got, more = _rebase(where, "--continue")
        if not rebasing(where):
            if got.returncode != 0:
                raise SelfWorkError("the rebase didn't finish: "
                                    + (got.stderr.strip() or got.stdout.strip())[-200:])
            return []
        if not more:
            raise SelfWorkError("the rebase stopped for another reason: "
                                + (got.stderr.strip() or got.stdout.strip())[-200:])
        info["files"], info["step"] = more, stopped_at(where)
        return more
    except SelfWorkError:
        _put_back(where, info.get("commit") or "")
        raise


def finish_rebase(cid: str, info: Dict[str, Any], by: str = "", how: str = "",
                  home: Optional[Path] = None) -> str:
    """After the agent: finish the rebase, and make sure it holds. "" when the
    change now sits cleanly on top of your checkout; otherwise why not — and
    the change is put back exactly as it was. A later commit that conflicts
    too is a why here; the engine hands those back to the agent through
    `continue_rebase` first."""
    where, onto = Path(info["where"]), info["onto"]
    why = ""
    try:
        if continue_rebase(info):
            why = "another of its commits conflicts too: " + ", ".join(info["files"])
        if not why and not is_in(where, onto, "HEAD"):
            why = "it still isn't on top of your checkout"
        if not why:
            went = [f for f in git(where, "diff", "--name-only", onto, "HEAD").splitlines() if f]
            still = marked(where, went)
            if still:
                why = "conflict markers were committed in " + ", ".join(still)
    except SelfWorkError as e:
        why = str(e)
    if why:
        _put_back(where, info.get("commit") or "")
        return why
    c = change(cid, home)
    p = Proposal(**{k: v for k, v in c.items() if k in Proposal.__dataclass_fields__})
    p.resolved = {"files": info.get("resolved") or list(info.get("files") or []),
                  "by": by, "how": how[:600], "onto": onto}
    record(p, home)
    return ""


def apply(cid: str, *, python: Optional[str] = None, home: Optional[Path] = None,
          check: Callable[..., candidate.Report] = candidate.check, say: Say = None,
          swap: Optional[Callable[..., Any]] = None, by_person: bool = False,
          now: bool = False, guarded: bool = False) -> Dict[str, Any]:
    """Put a proposed change to work. If your checkout has moved on since it
    was made, it's put on top of it first and judged again. Documentation
    goes straight into your checkout; code becomes a build that boards the
    release train (builds.board): the next go-live carries it, and everything
    else applied since, and the supervisor swaps it in — watched, and rolled
    back if it isn't healthy. `now`: a person wants it live at once.

    Safe to do again after a restart: a rebase that already happened is a
    rebase with nothing to do, and a check cut off (steps.Interrupted) is
    simply run again.

    A change touching HARD_LOCKED goes in only when a person asked for it
    (`by_person`): the protection keeps eki from applying those alone, not
    you. It takes the same path as any other, and its record says who asked.

    One touching only GUARDED paths eki may apply alone (`guarded`: autonomy
    is apply) — but only alone: with nothing else on the release train or
    on its way live, judged again on top of your checkout whether or not it
    moved, every check passing and the tests and restart check actually run,
    and then swapped in by itself rather than boarding the train, so a
    rollback undoes only it."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    c = change(cid, home)
    cid = c["id"]
    if c["state"] not in ("proposed", "conflicts", "rolled back"):
        raise SelfWorkError(f"self/{cid} is {c['state']} — only a proposed change can be applied")
    if c.get("resolving") and time.time() - float(c.get("resolving_at") or 0) < 3600:
        raise SelfWorkError("eki is having its conflicts resolved right now — it applies it when that's done")
    if locked_of(c) and not by_person:
        raise SelfWorkError("it touches what eki may never change alone ("
                            + ", ".join(locked_of(c)) + f") — a person applies it: `eki self apply {cid}`")
    if c.get("protected") and not by_person and not guarded:
        raise SelfWorkError("it's a guarded change (" + ", ".join(c["protected"]) + ") — eki applies "
                            f"those alone only when autonomy is apply; a person applies it: `eki self apply {cid}`")
    if not c.get("fit"):
        raise SelfWorkError(f"self/{cid} didn't pass its checks")
    alone = bool(c.get("protected")) and not by_person
    if alone:
        busy = alone_blocked(cid, home)
        if busy:
            return {"state": c["state"], "id": cid, "why": busy}
    if by_person:
        set_state(cid, c["state"], home, applied_by="you", asked_at=int(time.time()))
    root = Path(c["root"])
    where = ensure_worktree(c, home)
    commit = c["commit"]
    head = line(root)
    p = Proposal(**{k: v for k, v in c.items() if k in Proposal.__dataclass_fields__})
    p.worktree = str(where)
    if by_person:
        p.applied_by = "you"
    if c.get("base") and not asks_to_tick(c.get("request") or "", c.get("source") or "") and _untick_commit(where, c["base"]):
        commit = p.commit = git(where, "rev-parse", "HEAD")
        p.files = [f for f in git(where, "diff", "--name-only", c["base"], commit).splitlines() if f]
    moved = not is_in(root, head, commit)
    if moved:
        say("your checkout has moved on since — putting the change on top of it…")
        pipeline.mark(cid, "rebasing", home, onto=head[:10])
        got, _ = _rebase(where, "-q", head)
        if got.returncode != 0:
            subprocess.run(["git", "-C", str(where), "rebase", "--abort"], capture_output=True)
            why = (got.stderr.strip() or got.stdout.strip()).splitlines()
            set_state(cid, "conflicts", home, why="it doesn't go on top of your checkout as it is now: "
                      + (why[-1] if why else "a conflict")[:200])
            pipeline.mark(cid, "conflicts", home, why=(why[-1] if why else "a conflict")[:200])
            return {"state": "conflicts", "id": cid}
        pipeline.mark(cid, "rebased", home, onto=head[:10])
        p.commit, p.base = git(where, "rev-parse", "HEAD"), head
        p.files = [f for f in git(where, "diff", "--name-only", head, p.commit).splitlines() if f]
        p.protected, p.locked = touches_protected(p.files), touches_locked(p.files)
        if (p.locked or (p.protected and not guarded)) and not by_person:
            p.verdict = "on top of your checkout it touches protected paths — for a person"
            record(p, home)
            set_state(cid, "proposed", home, why="it touches what eki may not change alone ("
                      + ", ".join(p.locked or p.protected) + ")")
            pipeline.mark(cid, "stopped", home, why=p.verdict)
            return {"state": "proposed", "id": cid, "why": p.verdict}
        alone = bool(p.protected) and not by_person
        busy = alone_blocked(cid, home) if alone else ""
        if busy:
            record(p, home)
            return {"state": c["state"], "id": cid, "why": busy}
    if moved or alone:
        if not docs_only(p.files):
            say("judging it again, on top of your checkout…")
            pipeline.mark(cid, "checking", home)
            report = check(where, python=python, say=say)
            p.report, p.fit = report.to_json(), report.fit
            pipeline.mark(cid, "rechecked", home, fit=bool(report.fit))
            if not report.fit:
                p.verdict = "not fit to run on top of your checkout — the branch is kept"
                record(p, home)
                set_state(cid, "unfit", home, why="failed its checks once put on top of your checkout")
                return {"state": "unfit", "id": cid}
            short = fully_checked(p.report) if alone else ""
            if short:
                p.verdict = f"a guarded change goes in alone only fully checked: {short}"
                record(p, home)
                set_state(cid, "proposed", home, why=p.verdict)
                return {"state": "proposed", "id": cid, "why": p.verdict}
        p.verdict = "fit to run on top of your checkout"
        record(p, home)
    if roadmap.NAME in p.files and c.get("source") != "undo" \
            and not asks_to_untick(c.get("request") or "", c.get("source") or "") and keep_ticks(where, head):
        say("it would have opened a ticked ROADMAP item again — the tick is kept")
        p.commit = git(where, "rev-parse", "HEAD")
        p.files = [f for f in git(where, "diff", "--name-only", head, p.commit).splitlines() if f]
        record(p, home)
    if docs_only(p.files):
        merged = fast_forward(root, p.commit)
        if merged.startswith("merged"):
            close_worktree(root, where, cid, keep_branch=True)
            set_state(cid, "applied", home, merged=merged, how="into your checkout (documentation only)")
            pipeline.mark(cid, "landed", home)
            pipeline.mark(cid, "live", home, how="documentation: straight into your checkout")
            return {"state": "applied", "id": cid, "merged": merged}
        set_state(cid, "proposed", home, why=merged)
        pipeline.mark(cid, "stopped", home, why=merged)
        return {"state": "proposed", "id": cid, "why": merged}
    from . import builds
    build = builds.make(root, p.commit, note=f"self/{cid}")
    if alone:
        # live by itself, not on the train: a rollback undoes only it
        (swap or builds.swap)(build, self_id=cid)
        set_state(cid, "applying", home, build=str(build), alone=True, alone_at=int(time.time()),
                  how="applied alone as a guarded change — every check passed, the full tests "
                      "again on top of your checkout just before")
        return {"state": "applying", "id": cid, "build": str(build), "alone": True}
    if swap is not None:
        swap(build, self_id=cid)
    else:
        builds.board(build, self_id=cid, now=now)
    set_state(cid, "applying", home, build=str(build), alone=False)
    pipeline.mark(cid, "landed", home, build=build.name)
    return {"state": "applying", "id": cid, "build": str(build)}


def settled(cid: str, outcome: str, merged: str = "", why: str = "",
            home: Optional[Path] = None) -> Dict[str, Any]:
    """The supervisor's outcome for a change it swapped in: healthy or not."""
    try:
        c = change(cid, home)
    except SelfWorkError:
        return {}
    if outcome == "healthy":
        entry = set_state(c["id"], "applied", home, merged=merged or None, how="swapped in, healthy")
        pipeline.mark(c["id"], "live", home)
        if merged.startswith("merged") and c.get("worktree"):
            close_worktree(Path(c["root"]), Path(c["worktree"]), c["id"], keep_branch=True)
        if c.get("reverts"):
            set_state(c["reverts"], "undone", home, by=c["id"])
        return entry
    pipeline.mark(c["id"], "rolled back", home, why=why or outcome)
    return set_state(c["id"], "rolled back", home, why=why or outcome)


def carried(root: Path, commit: str, home: Optional[Path] = None) -> List[str]:
    """Changes still "applying" that a healthy build carries anyway — their
    own swap was superseded by a newer one built on top of them. Marked
    applied; returns their ids."""
    done = []
    for c in changes(home):
        if c["state"] != "applying" or not c.get("commit") or not commit:
            continue
        if is_in(root, c["commit"], commit):
            set_state(c["id"], "applied", home, how="swapped in with a later change, healthy")
            pipeline.mark(c["id"], "live", home, how="with a later change")
            done.append(c["id"])
    return done


def discard(cid: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """Drop a change nobody wants: its worktree and its branch."""
    c = change(cid, home)
    if c["state"] in ("applying", "applied"):
        raise SelfWorkError(f"self/{c['id']} is {c['state']} — undo it instead")
    root = Path(c["root"])
    where = Path(c.get("worktree") or ((home or HOME) / c["id"]))
    close_worktree(root, where, c["id"])
    return set_state(c["id"], "discarded", home)


def diff(cid: str, home: Optional[Path] = None) -> str:
    c = change(cid, home)
    if not c.get("commit"):
        return ""
    root = Path(c["root"])
    base = c.get("base") or f"{c['commit']}^"
    try:
        return git(root, "diff", "--stat", "--patch", f"{base}..{c['commit']}")
    except SelfWorkError as e:
        raise SelfWorkError(f"self/{c['id']}'s commit isn't in {root} any more ({e})") from None


def undo(cid: str, *, python: Optional[str] = None, home: Optional[Path] = None,
         check: Callable[..., candidate.Report] = candidate.check, say: Say = None) -> Proposal:
    """Take an applied change back. The revert is a change of its own, on top
    of your checkout as it is now, judged like any other; the caller applies it."""
    c = change(cid, home)
    if c["state"] != "applied":
        raise SelfWorkError(f"self/{c['id']} is {c['state']} — only an applied change can be undone")
    root = Path(c["root"])
    p = begin(f"Take back self/{c['id']}: {c['title']}", root=root, base="HEAD", python=python,
              home=home, check_base=False, say=say, source="undo", reverts=c["id"],
              title=f"Undo: {c['title']}"[:100])
    where = Path(p.worktree)
    got = subprocess.run(["git", "-C", str(where), "revert", "--no-commit", c["commit"]],
                         capture_output=True, text=True)
    if got.returncode != 0:
        close_worktree(root, where, p.id)
        p.worktree = p.branch = ""
        p.verdict = ("can't take it back cleanly — later changes touch the same lines: "
                     + got.stderr.strip()[:200])
        record(p, home)
        return p
    p = conclude(p, {"state": "done", "run": "", "backend": "eki"}, python=python, home=home,
                 check=check, say=say)
    if p.commit:
        set_state(c["id"], "undoing", home, by=p.id)
    return p


# ---- asking the running engine ----------------------------------------------------

def ask_engine(service: str, backend: str = "",
               say: Optional[Callable[[str], None]] = None) -> Ask:
    """The real `Ask`: an ordinary folder run in the engine that's running,
    so it is routed, priced and shown like anything else you'd ask."""
    import httpx
    from .runs import TERMINAL
    say = say or (lambda _line: None)

    def ask(prompt: str, folder: str) -> Dict[str, str]:
        started = httpx.post(f"{service}/api/ask", timeout=30,
                             json={"prompt": prompt, "repo": folder, "backend": backend}).json()
        rid = started["run"]
        say(f"run {rid} — follow it with `eki watch {rid}`, or in the app")
        told = ""
        while True:
            run = httpx.get(f"{service}/api/runs/{rid}", timeout=30).json()
            if run.get("backend") and run["backend"] != told:
                told = run["backend"]
                say(f"routed to {told}: {run.get('reason') or ''}".rstrip(": "))
            if run.get("state") in TERMINAL:
                return {"state": run["state"], "run": rid, "backend": run.get("backend") or ""}
            time.sleep(2)

    return ask
