"""What the agents are told when they plan or build a change to a person's own
project (a goal with `project` set: eki/projects.py). The same shape as
eki/selfbrief.py — the same split rules, the same `ITEMS:` ending, the same
`SUMMARY:` / `ITEM:` outcome lines — so the same readers parse the answers."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from . import projects, selfbrief

#: the `ITEMS:` JSON ending, the shape eki/selfbrief.py items_in reads; {context} is the
#: `"context"` key when the project has knowledge cards to choose from
ENDING = """\
End your answer with a line `ITEMS:` followed by one JSON list, nothing after it:

ITEMS:
[{{"title": "...", "spec": "...", "files": ["src/x.py", "tests/test_x.py"],
  "deps": ["title of an earlier item"], "independent": false{context}}}]
"""
CONTEXT_KEY = ',\n  "context": [{"name": "architecture", "why": "touches the scene loader"}]'

KNOWLEDGE = """
The project's knowledge cards (components/), what each holds and how it is read:

{cards}

Give each item a "context" list: the knowledge cards that item needs, by name,
with one short reason each. Cards read `always` reach every run anyway, so
leave them out. A one-line fix may need none; leaving out what the item doesn't
need keeps its turn focused.
"""

#: how a project's finished work lands, as the build brief tells it
LANDS = {
    "auto": ("eki judges what you leave and, once it passes the checks, lands it on the\n"
             "project's branch {target} itself."),
    "pr": ("eki judges what you leave and opens a pull request from the branch {branch}\n"
           "for the person to review and merge."),
    "person": "eki judges what you leave and proposes it as the branch {branch} for the person to merge.",
}

PLAN = """\
You are planning a change to {name}, a project at {path}. This folder is a
read-only worktree of it at commit {base}, the tip of its branch {branch}. Do
not change any file here; you are only writing the plan.

The goal:

{goal}

Read the project's README, its docs and any CLAUDE.md or AGENTS.md first, then
the code the goal touches. Then split the goal into items that separate agents
will build at the same time, each in a worktree of its own:

- make the fewest items that keep each agent turn short: about 15 minutes of
  agent work or less, with its tests. Usually that is one item; at most it is
  one root plus a few leaves that depend only on the root;
- never a multi-hour run: when the work is big, split it into parallel parts
  that are merged after, never one long turn;
- each item declares the files it will touch (its write-set, as paths or globs);
{split}- a spec says what to build and how to tell it works — not how to code it.
{rest}
"""

REST = """
This is one round of a standing goal. When nothing is worth doing now, the
right answer is one line saying why, then `ITEMS:` and an empty list `[]`.
"""

BUILD = """\
You are changing {name} — a project at {path} — in a worktree of its own: this
folder, branch {branch}, from commit {base}. Nothing you do here touches the
project's own checkout or branches; {lands} Never touch anything outside
this folder — not {path}, not ~/.eki, not {source}.

The goal: {goal}

Your item: **{title}**

{spec}

Files you are expected to touch: {files}.
{others}
Rules: read the project's README and any CLAUDE.md or AGENTS.md first and keep
its conventions; add or change tests for what you change; {check}; do not
commit — eki commits what you leave here; do not ask questions — decide, and
say what you decided.
Never run `npm install`, `pip install` or any other install inside this
worktree: node_modules and .venv are links to the project folder's own. If a
dependency must change, edit package.json or the lockfile (or pyproject) and
say so in the SUMMARY; eki installs in the project folder after the change
lands.
{retry}
End your answer with a `SUMMARY:` paragraph in plain words (what is new, how
to use it, what to check) and then one last line:
`ITEM: done` — or `ITEM: partial` if a slice is done and the rest should be
another item, or `ITEM: person <why>` if this needs a person's hands.
"""


def _check(project) -> str:
    if project["check_cmd"]:
        return f"run `{project['check_cmd']}` before you finish and leave it green"
    if "repo" in project.keys() and project["repo"]:
        return ("there is no check for this project; run the project's own tests before you finish "
                "and leave them green")
    if projects.check_argv(project):
        return "run `bin/check` before you finish and leave it green"
    return "run the project's own tests before you finish and leave them green"


def _cards(project) -> list:
    """The project's own knowledge cards; [] when it has none or they can't be read."""
    try:
        from . import components
        found, _ = components.own(Path(project["path"]))
        return [c for c in found.values() if c.kind == "knowledge"]
    except Exception:  # reading the cards never breaks the plan brief
        return []


def _card_line(c) -> str:
    files = c.spec.get("files")
    files = ", ".join(str(f) for f in files) if isinstance(files, list) else ""
    about = str(c.spec.get("about") or "").strip() or "(no about line)"
    return f"- {c.name} (read: {c.spec.get('read') or 'when-relevant'}): {about} — files: {files}"


def landing(project) -> str:
    """'auto', 'pr' or 'person': how this project's finished work lands. The project's
    workflow card says when it has one; else a GitHub project opens pull requests, an
    'apply' project lands itself, and the rest waits for the person."""
    try:
        from . import components
        found, _ = components.own(Path(project["path"]))
        for c in found.values():
            if c.kind == "workflow" and c.spec.get("merge") in LANDS:
                return c.spec["merge"]
    except Exception:  # a broken card falls back to the project's own setting
        pass
    if "repo" in project.keys() and project["repo"]:
        return "pr"
    return "auto" if projects.autonomy(project) == "apply" else "person"


def plan(goal: str, base: str, project, *, standing: bool = False) -> str:
    cards = _cards(project)
    known = KNOWLEDGE.format(cards="\n".join(_card_line(c) for c in cards)) if cards else ""
    return (PLAN.format(name=project["name"], path=project["path"], branch=project["branch"],
                        base=base[:12], goal=goal.strip(), split=selfbrief.SPLIT_RULES,
                        rest=REST if standing else "") + known + "\n"
            + ENDING.format(context=CONTEXT_KEY if cards else ""))


def build(*, goal: str, title: str, spec: str, files: List[str], branch: str, base: str,
          source: str, others: List[Tuple[str, List[str]]], failure: Optional[str] = None,
          project, objection: Optional[str] = None) -> str:
    beside = ""
    if others:
        lines = "\n".join(f"- {t}: {', '.join(f) or '(files not declared)'}" for t, f in others)
        beside = ("Other items are being built beside yours right now — stay out of their files:\n"
                  f"{lines}\n")
    retry = ""
    if failure:
        retry = ("\nA previous attempt at this item failed its checks. What failed:\n\n"
                 f"{failure.strip()}\n\nFix that as well.\n")
    if objection:
        retry += (f"\nA second reader objected: {objection.strip().rstrip('.')}. Answer it in the change,"
                  " or say in SUMMARY why it's wrong.\n")
    lands = LANDS[landing(project)].format(branch=branch, target=project["branch"])
    return BUILD.format(name=project["name"], path=project["path"], goal=goal.strip(), title=title,
                        lands=lands,
                        spec=spec.strip(), branch=branch, base=base[:12], source=source, others=beside,
                        retry=retry, check=_check(project),
                        files=", ".join(files) or "(not declared — say which in your summary)")
