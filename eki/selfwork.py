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
               A base that passed once is remembered for a day.
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

Some paths eki may change but never on its own say-so — the checker that
judges it, this file, the login agent, anything that holds or reads a
credential. A proposal that touches them says so, loudly, and is never
applied by eki.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import candidate

HOME = Path("~/.eki/self").expanduser()

#: a diff touching these is for a person to read, whatever the checks say
PROTECTED = (
    "eki/candidate.py", "eki/selfwork.py",       # who judges, and who asks
    "eki/selfloop.py", "eki/selfengine.py",      # what it takes on, and how far it goes alone
    "eki/agent.py",                              # what launchd runs
    "eki/builds.py", "eki/supervisor.sh",        # the swap and the way back
    "eki/secrets.py", "eki/quota/",              # the credentials rule
    "mac/sign.sh", "mac/hub.entitlements",
    "LICENSE", "NOTICE",
)
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

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def headline(self) -> str:
        first = self.request.strip().splitlines()[0] if self.request.strip() else ""
        return (self.title or first)[:100]

    def lines(self) -> List[str]:
        out = [f"self/{self.id}: {self.headline}"]
        if self.run:
            out.append(f"  done by   {self.backend or '?'} (run {self.run})")
        if self.files:
            out.append(f"  changed   {len(self.files)} file(s): " + ", ".join(self.files[:8])
                       + (" …" if len(self.files) > 8 else ""))
        if self.report:
            for c in self.report["checks"]:
                mark = "skip" if c["skipped"] else ("ok  " if c["ok"] else "FAIL")
                out.append(f"  {mark}  {c['name']:9} {c['detail']}".rstrip())
        if self.protected:
            out.append("  !! touches what eki may not change alone: " + ", ".join(self.protected))
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


def touches_protected(files: List[str]) -> List[str]:
    return [f for f in files
            if any(f == p or (p.endswith("/") and f.startswith(p)) for p in PROTECTED)]


def docs_only(files: List[str]) -> bool:
    """Nothing that runs: Markdown outside the package. Such a change needs no
    candidate engine and no swap — it goes straight into your checkout."""
    return bool(files) and all(f.endswith(".md") and not f.startswith("eki/") for f in files)


# ---- the brief ---------------------------------------------------------------

def brief(request: str, python: str, where: str = "") -> str:
    """What the agent is told. It knows it's working on eki, where the tests
    are, and that nobody is there to answer a question."""
    here = f" ({where} — make every change there)" if where else ""
    return (
        f"You are working on eki's own source code, in a git worktree made for this "
        f"one change{here}. eki is the program that sent you this request.\n\n"
        f"The change: {request.strip()}\n\n"
        "How to work here:\n"
        "- Read README.md and the modules you touch first; match their style — "
        "plain docstrings that say why, small functions, no new dependencies.\n"
        "- Add or adjust tests under tests/ for what you change.\n"
        f"- Run the tests with `{python} -m pytest -q` and leave them passing.\n"
        "- Don't commit, push, or touch git branches; eki commits your work itself.\n"
        "- Don't ask questions — nobody is watching. If something is ambiguous, take "
        "the smaller reading and say so in your final message.\n"
        "- Leave these alone unless the change is about them: "
        + ", ".join(PROTECTED) + ".\n"
        "- Finish with two or three sentences: what you changed and anything a "
        "reviewer should look at.\n"
    )


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
    path = (home or HOME) / "base-ok.json"
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        seen = {}
    now = time.time()
    seen = {k: v for k, v in seen.items() if now - float(v) < BASE_OK_SECONDS}
    seen[sha] = int(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen))


# ---- the steps -----------------------------------------------------------------------

Ask = Callable[[str, str], Dict[str, str]]
"""(prompt, folder) → {"state", "run", "backend"}; blocks until the run ends."""
Say = Optional[Callable[[str], None]]


def begin(request: str, *, root: Path, base: str = "HEAD", python: Optional[str] = None,
          home: Optional[Path] = None, check_base: bool = True, say: Say = None,
          **meta: Any) -> Proposal:
    """Open a change: its worktree, and — before anything is spent — the base
    passing its own tests. A proposal that comes back with a verdict didn't
    start (and is already written down)."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    if not request.strip():
        raise SelfWorkError("say what to change")
    from . import builds
    why = builds.cant_change_code(root)
    if why:
        raise SelfWorkError(why)
    wid = uuid.uuid4().hex[:8]
    known = {k: v for k, v in meta.items() if k in Proposal.__dataclass_fields__}
    p = Proposal(id=wid, request=request.strip(), root=str(root), at=int(time.time()), **known)
    where = open_worktree(root, wid, base, home)
    p.branch, p.worktree = f"self/{wid}", str(where)
    p.base = git(where, "rev-parse", "HEAD")
    say(f"worktree: {where}  (branch {p.branch}, from {p.base[:10]})")
    if check_base and not base_known_good(p.base, home):
        say("checking the base passes its own tests, before spending anything…")
        try:
            candidate.check_tests(where, python)
        except RuntimeError as e:
            close_worktree(root, where, wid)
            p.worktree = p.branch = ""
            p.verdict = (f"not started: the base ({p.base[:10]}) doesn't pass its own tests, "
                         f"so a change to it can't be judged — {e}")[:500]
            record(p, home)
            return p
        note_base_good(p.base, home)
    return p


def conclude(p: Proposal, got: Dict[str, str], *, python: Optional[str] = None,
             home: Optional[Path] = None, check: Callable[..., candidate.Report] = candidate.check,
             say: Say = None, before_commit: Optional[Callable[[Path], None]] = None) -> Proposal:
    """The agent is done: commit what it did and judge it. `before_commit`
    may add to the change (the ROADMAP tick) before it's committed."""
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
    if before_commit is not None:
        before_commit(where)
    p.files = changed(where)
    if not p.files:
        return finish("the agent changed nothing", keep=False)
    p.protected = touches_protected(p.files)
    why = {"fault": "A fault eki observed in itself", "roadmap": "The next item in ROADMAP.md",
           "note": "Picked from eki's weekly note", "undo": f"Taking back self/{p.reverts}"
           }.get(p.source, "Asked through `eki self`")
    git(where, "-c", "user.name=eki", "-c", "user.email=eki@localhost",
        "commit", "-q", "--no-verify", "-m",
        f"self: {p.headline.splitlines()[0][:68]}\n\n{why}; written by "
        f"{p.backend or 'an agent'} in run {p.run or '?'}.\nNot merged by eki.")
    p.commit = git(where, "rev-parse", "HEAD")

    if docs_only(p.files):
        report = candidate.Report(str(where), [candidate.Check(
            "docs", True, "only documentation changed — nothing to run")])
    else:
        say("judging the result…")
        report = check(where, python=python, say=say)
    p.report, p.fit = report.to_json(), report.fit
    if not report.fit:
        return finish("not fit to run — the branch is kept so the failure can be read")
    if p.protected:
        return finish("passes, but touches protected paths: for a person to read "
                      "line by line before merging")
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


def apply(cid: str, *, python: Optional[str] = None, home: Optional[Path] = None,
          check: Callable[..., candidate.Report] = candidate.check, say: Say = None,
          swap: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Put a proposed change to work. If your checkout has moved on since it
    was made, it's put on top of it first and judged again. Documentation
    goes straight into your checkout; code becomes a build the supervisor
    swaps in — watched, and rolled back if it isn't healthy."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    c = change(cid, home)
    cid = c["id"]
    if c["state"] not in ("proposed", "conflicts", "rolled back"):
        raise SelfWorkError(f"self/{cid} is {c['state']} — only a proposed change can be applied")
    if c.get("protected"):
        raise SelfWorkError("it touches what eki may not change alone ("
                            + ", ".join(c["protected"]) + ") — read it and merge it yourself")
    if not c.get("fit"):
        raise SelfWorkError(f"self/{cid} didn't pass its checks")
    root = Path(c["root"])
    where = ensure_worktree(c, home)
    commit = c["commit"]
    head = git(root, "rev-parse", "HEAD")
    p = Proposal(**{k: v for k, v in c.items() if k in Proposal.__dataclass_fields__})
    p.worktree = str(where)
    if not is_in(root, head, commit):
        say("your checkout has moved on since — putting the change on top of it…")
        got = subprocess.run(["git", "-C", str(where), "-c", "user.name=eki", "-c",
                              "user.email=eki@localhost", "rebase", "-q", head],
                             capture_output=True, text=True)
        if got.returncode != 0:
            subprocess.run(["git", "-C", str(where), "rebase", "--abort"], capture_output=True)
            why = (got.stderr.strip() or got.stdout.strip()).splitlines()
            set_state(cid, "conflicts", home, why="it doesn't go on top of your checkout as it is now: "
                      + (why[-1] if why else "a conflict")[:200])
            return {"state": "conflicts", "id": cid}
        p.commit, p.base = git(where, "rev-parse", "HEAD"), head
        p.files = [f for f in git(where, "diff", "--name-only", head, p.commit).splitlines() if f]
        if not docs_only(p.files):
            say("judging it again, on top of your checkout…")
            report = check(where, python=python, say=say)
            p.report, p.fit = report.to_json(), report.fit
            if not report.fit:
                p.verdict = "not fit to run on top of your checkout — the branch is kept"
                record(p, home)
                set_state(cid, "unfit", home, why="failed its checks once put on top of your checkout")
                return {"state": "unfit", "id": cid}
        p.verdict = "fit to run on top of your checkout"
        record(p, home)
    if docs_only(p.files):
        merged = fast_forward(root, p.commit)
        if merged.startswith("merged"):
            close_worktree(root, where, cid, keep_branch=True)
            set_state(cid, "applied", home, merged=merged, how="into your checkout (documentation only)")
            return {"state": "applied", "id": cid, "merged": merged}
        set_state(cid, "proposed", home, why=merged)
        return {"state": "proposed", "id": cid, "why": merged}
    from . import builds
    build = builds.make(root, p.commit, note=f"self/{cid}")
    (swap or builds.swap)(build, self_id=cid)
    set_state(cid, "applying", home, build=str(build))
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
        if merged.startswith("merged") and c.get("worktree"):
            close_worktree(Path(c["root"]), Path(c["worktree"]), c["id"], keep_branch=True)
        if c.get("reverts"):
            set_state(c["reverts"], "undone", home, by=c["id"])
        return entry
    return set_state(c["id"], "rolled back", home, why=why or outcome)


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
