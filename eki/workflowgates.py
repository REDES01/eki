"""What a goal's workflow card (eki/workflow.py) asks of self-work: its plan and the gates after a build.

selfwork asks here and keeps doing the moving. A goal with no card is None everywhere here
and selfwork does today's thing. With a card, gate 1 is one command run made of the chosen
checks in order — `command` (the card's check command), then `playtester` (`eki use
<playtester>` plays the change; a non-zero exit is red). An item that carries goal checks
gives them to the playtester and pipes its answer into `eki verdict --lines`: that gate run
is a `play` judge run (eki/judge.py play, which registers it). With `review` only, a judge
run (eki/judge.py) judges alone and gate 1 just says so; with `self` only there is no gate run at all and the
item is proposed as the builder left it. The board says once per goal which checks run.

eki's own goals keep their gate 1 (bin/check): what the card changes for eki is read by
selfwork.settings() — autonomy, parallel and review — through `own`.
"""
from __future__ import annotations

import json
import shlex
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import builds, judgebrief, pathrules, projectwork, testpick, workflow

#: what an item says for the review when the project's card doesn't choose it
NO_REVIEW = "not in this project's workflow"

_own: dict = {}


def own() -> Optional[workflow.Workflow]:
    """eki's own card (in the source checkout), reread only when a component.json there changed."""
    root = builds.source()
    try:
        key = (str(root),) + tuple(sorted((str(p), p.stat().st_mtime_ns)
                                          for p in (Path(root) / "components").glob("*/component.json")))
    except OSError:
        key = (str(root),)
    if key not in _own:
        _own.clear()
        _own[key] = workflow.of_root(root)
    return _own[key]


def of_goal(conn: sqlite3.Connection, goal: Optional[sqlite3.Row]) -> Optional[workflow.Workflow]:
    """The card of a goal's project, eki's own for an eki goal; None when there is none."""
    if goal is None:
        return None
    try:
        project = projectwork.project_of(conn, goal)
    except KeyError:
        return None
    return own() if project is None else workflow.of_project(conn, project)


def single(conn: sqlite3.Connection, project: Optional[str]) -> bool:
    """Does the card of `project` (None: eki itself) say plan single: the request is one item?"""
    from . import projects
    if project is None:
        wf = own()
    else:
        row = projects.get(conn, project)
        wf = workflow.of_project(conn, row) if row is not None else None
    return wf is not None and wf.plan == "single"


def gate(wf: workflow.Workflow, title: str, touched: Optional[List[str]] = None,
         checks: Optional[List[Tuple[str, str]]] = None, root: Optional[Path] = None) -> Optional[Tuple[str, str]]:
    """Gate 1 for a project item under its card: (command run argv JSON, what it runs),
    or None when the card chooses no gate run (self only). A card with path rules runs the
    rules matching `touched` (the item's changed files) all in one run; its `tests` rules run
    the tests picked in `root`, the item's worktree (eki/testpick.py). With the item's goal
    `checks` [(id, text)] the playtester is asked for a verdict on each (judgebrief.play_line)."""
    lines, says = [], []
    if workflow.has(wf, "command") and (wf.command.strip() or wf.rules):
        lines.append(workflow.check_line(wf, touched or [], root))
        says.append(_chosen(wf, touched or [], root) if wf.rules else wf.command)
    if workflow.has(wf, "playtester"):
        lines.append(judgebrief.play_line(wf, title, checks) if checks else
                     f"eki use {shlex.quote(wf.playtester)} {shlex.quote(f'play the change: {title}')}")
        says.append(lines[-1])
    if len(lines) > 1:                     # the command as written, whatever its own operators
        line = f"( {lines[0]}\n) && {lines[1]}"
    elif lines:
        line = lines[0]
    elif workflow.has(wf, "review"):       # the judge run judges alone: gate 1 only says so
        line = f"echo {shlex.quote(workflow.said(wf))}"
    else:
        return None
    return json.dumps(["/bin/sh", "-c", line, "eki-judge"]), " && ".join(says) or workflow.said(wf)


def _chosen(wf: workflow.Workflow, touched: List[str], root: Optional[Path] = None) -> str:
    """The rules gate 1 runs for these files, as judged_by says them: 'story files: npm run
    check:story · code: …', or the NOTHING note when none matched. A card with `tests` rules
    says the tests it picked first: 'gate 1: 4 test files for eki/queue.py'."""
    rules = [r for r in pathrules.chosen(wf.rules, touched) if r.run]
    runs = " · ".join(f"{pathrules.label(r)}: {r.run}" for r in rules)
    if any(r.tests for r in wf.rules):
        tests = testpick.said(testpick.pick(wf.rules, touched, root, wf.whole_over))
        return f"{tests} · {runs}" if runs else tests
    return runs or pathrules.NOTHING


def rules_said(wf: workflow.Workflow) -> str:
    """A rules card's gate 1 for a builder brief, in the board's words with each rule's paths:
    'Each change: story files (design/script/**, …) → `npm run check:story` · …'."""
    return "Each change: " + " · ".join(
        f"{r.name + ' (' + ', '.join(r.paths) + ')' if r.name else ', '.join(r.paths)} → `{workflow.rule_runs(r)}`"
        for r in wf.rules)


def told(conn: sqlite3.Connection, goal: sqlite3.Row, said: List[str]) -> List[str]:
    """A plan that just concluded: its line says once which checks the goal's card runs."""
    if not said:
        return said
    cur = conn.execute("SELECT state FROM goals WHERE id=?", (goal["id"],)).fetchone()
    wf = of_goal(conn, goal) if cur is not None and cur["state"] == "planned" else None
    return [f"{said[0]} — {workflow.said(wf)}"] + said[1:] if wf is not None else said


def started(conn: sqlite3.Connection, said: List[str]) -> List[str]:
    """Builds that just started: the first build of a goal that had no plan says its card's checks."""
    out = []
    for line in said:
        iid = line.split(":", 1)[0].removeprefix("item ").strip()
        it = conn.execute("SELECT goal_id, tries FROM items WHERE id=?", (iid,)).fetchone()
        goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone() if it else None
        first = (goal is not None and not goal["plan_run"] and it["tries"] == 1 and conn.execute(
            "SELECT COUNT(*) FROM items WHERE goal_id=? AND tries>0", (goal["id"],)).fetchone()[0] == 1)
        wf = of_goal(conn, goal) if first else None
        out.append(f"{line} — {workflow.said(wf)}" if wf is not None else line)
    return out
