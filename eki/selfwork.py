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
  a verdict    when the agent is done, its work is committed to the branch
               and put through the candidate check (eki/candidate.py).

This is *propose*: what comes out is a branch, a diff and a report. Nothing
is merged and nothing that is running is touched; that is a person's
`git merge`, until the supervisor exists (docs/self-build.md).

Some paths eki may change but never on its own say-so — the checker that
judges it, this file, the login agent, anything that holds or reads a
credential. A proposal that touches them says so, loudly.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
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
    "eki/agent.py",                              # what launchd runs
    "eki/builds.py", "eki/supervisor.sh",        # the swap and the way back
    "eki/secrets.py", "eki/quota/",              # the credentials rule
    "mac/sign.sh", "mac/hub.entitlements",
    "LICENSE", "NOTICE",
)


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

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    def lines(self) -> List[str]:
        out = [f"self/{self.id}: {self.request}"]
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
                    f"  read it    git -C {self.root} diff {self.base[:10]}..{self.branch}",
                    f"  take it    git -C {self.root} merge {self.branch}",
                    f"  drop it    git -C {self.root} worktree remove --force {self.worktree}"
                    f" && git -C {self.root} branch -D {self.branch}"]
        return out


# ---- git -------------------------------------------------------------------

def git(root: Path | str, *args: str) -> str:
    got = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if got.returncode != 0:
        raise SelfWorkError(f"git {' '.join(args[:3])}: {got.stderr.strip()[:300]}")
    return got.stdout.strip()


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


def close_worktree(root: Path, where: Path, wid: str) -> None:
    for args in (("worktree", "remove", "--force", str(where)), ("branch", "-D", f"self/{wid}")):
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


# ---- the brief ---------------------------------------------------------------

def brief(request: str, python: str) -> str:
    """What the agent is told. It knows it's working on eki, where the tests
    are, and that nobody is there to answer a question."""
    return (
        "You are working on eki's own source code, in a git worktree made for this "
        "one change. eki is the program that sent you this request.\n\n"
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
    with open(path, "a") as f:
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


# ---- the loop --------------------------------------------------------------------

Ask = Callable[[str, str], Dict[str, str]]
"""(prompt, folder) → {"state", "run", "backend"}; blocks until the run ends."""


def propose(request: str, *, root: Path, ask: Ask, base: str = "HEAD",
            python: Optional[str] = None, home: Optional[Path] = None,
            check: Callable[..., candidate.Report] = candidate.check,
            check_base: bool = True,
            say: Optional[Callable[[str], None]] = None) -> Proposal:
    """One change to eki, from request to verdict. Never merges anything."""
    say = say or (lambda _line: None)
    python = python or sys.executable
    if not request.strip():
        raise SelfWorkError("say what to change")
    wid = uuid.uuid4().hex[:8]
    p = Proposal(id=wid, request=request.strip(), root=str(root), at=int(time.time()))

    where = open_worktree(root, wid, base, home)
    p.branch, p.worktree = f"self/{wid}", str(where)
    p.base = git(where, "rev-parse", "HEAD")
    say(f"worktree: {where}  (branch {p.branch}, from {p.base[:10]})")

    def finish(verdict: str, keep: bool = True) -> Proposal:
        p.verdict = verdict
        if not keep:
            close_worktree(root, where, wid)
            p.worktree = p.branch = ""
        record(p, home)
        return p

    if check_base:
        say("checking the base passes its own tests, before spending anything…")
        try:
            candidate.check_tests(where, python)
        except RuntimeError as e:
            return finish(f"not started: the base ({p.base[:10]}) doesn't pass its own "
                          f"tests, so a change to it can't be judged — {e}"[:500], keep=False)

    say("handing it to an agent…")
    got = ask(brief(request, python), str(where))
    p.run, p.backend = got.get("run", ""), got.get("backend", "")
    if got.get("state") != "done":
        return finish(f"the agent's run ended {got.get('state') or 'without a state'}; "
                      "whatever it wrote is still in the worktree")

    p.files = changed(where)
    if not p.files:
        return finish("the agent changed nothing", keep=False)
    p.protected = touches_protected(p.files)
    git(where, "-c", "user.name=eki", "-c", "user.email=eki@localhost",
        "commit", "-q", "-m",
        f"self: {p.request[:68]}\n\nAsked through `eki self`; written by "
        f"{p.backend or 'an agent'} in run {p.run or '?'}.\nNot merged by eki.")
    p.commit = git(where, "rev-parse", "HEAD")

    say("judging the result…")
    report = check(where, python=python, say=say)
    p.report, p.fit = report.to_json(), report.fit
    if not report.fit:
        return finish("not fit to run — the branch is kept so the failure can be read")
    if p.protected:
        return finish("passes, but touches protected paths: for a person to read "
                      "line by line before merging")
    return finish("fit to run — proposed, not merged")


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
