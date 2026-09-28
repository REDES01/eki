"""What the agents are told when they plan or build a change to a person's own
project (a goal with `project` set: eki/projects.py). The same shape as
eki/selfbrief.py — the same split rules, the same `ITEMS:` ending, the same
`SUMMARY:` / `ITEM:` outcome lines — so the same readers parse the answers."""
from __future__ import annotations

from typing import List, Optional, Tuple

from . import projects, selfbrief

#: the `ITEMS:` JSON ending, word for word as the self plan asks for it
ENDING = selfbrief.PLAN[selfbrief.PLAN.index("End your answer with a line `ITEMS:`"):].format()

PLAN = """\
You are planning a change to {name}, a project at {path}. This folder is a
read-only worktree of it at commit {base}, the tip of its branch {branch}. Do
not change any file here; you are only writing the plan.

The goal:

{goal}

Read the project's README, its docs and any CLAUDE.md or AGENTS.md first, then
the code the goal touches. Then split the goal into items that separate agents
will build at the same time, each in a worktree of its own:

- an item is small: one to three files, under an hour of work, with its tests;
- each item declares the files it will touch (its write-set, as paths or globs);
{split}- a spec says what to build and how to tell it works — not how to code it;
- one item is fine when the goal is small.
{rest}
"""

REST = """
This is one round of a standing goal. When nothing is worth doing now, the
right answer is one line saying why, then `ITEMS:` and an empty list `[]`.
"""

BUILD = """\
You are changing {name} — a project at {path} — in a worktree of its own: this
folder, branch {branch}, from commit {base}. Nothing you do here touches the
project's own checkout or branches; eki judges what you leave and proposes it
as the branch {branch} for the person to merge. Never touch anything outside
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


def plan(goal: str, base: str, project, *, standing: bool = False) -> str:
    return PLAN.format(name=project["name"], path=project["path"], branch=project["branch"],
                       base=base[:12], goal=goal.strip(), split=selfbrief.SPLIT_RULES,
                       rest=REST if standing else "") + "\n" + ENDING


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
    return BUILD.format(name=project["name"], path=project["path"], goal=goal.strip(), title=title,
                        spec=spec.strip(), branch=branch, base=base[:12], source=source, others=beside,
                        retry=retry, check=_check(project),
                        files=", ".join(files) or "(not declared — say which in your summary)")
