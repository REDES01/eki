"""What the agents are told when they plan or build a change to a person's own
project (a goal with `project` set: eki/projects.py). The same shape as
eki/selfbrief.py — the same split rules, the same `ITEMS:` ending, the same
`SUMMARY:` / `ITEM:` outcome lines — so the same readers parse the answers."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from . import projects, selfbrief

#: the `ITEMS:` JSON ending, the shape eki/selfbrief.py items_in reads; {context} is the
#: `"context"` key when the project has knowledge documents to choose from
ENDING = """\
End your answer with a line `ITEMS:` followed by one JSON list, nothing after it:

ITEMS:
[{{"title": "...", "spec": "...", "files": ["src/x.py", "tests/test_x.py"],
  "deps": ["title of an earlier item"], "independent": false{context}}}]
"""
CONTEXT_KEY = ',\n  "context": [{"name": "scene-loader", "why": "touches how scenes load"}]'

KNOWLEDGE = """
The project's knowledge documents (knowledge cards in components/), what each
holds and how it is read:

{cards}

Give each item a "context" list: the documents that item needs, by document
name, with one short reason each. Documents read `always` reach every run
anyway, so leave them out. A one-line fix may need none; leaving out what the
item doesn't need keeps its turn focused.
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

DRAFT = """\
Draft a goal for {name} from this wish: {first}

The whole wish, as the person typed it:

{wish}

This folder is a read-only worktree of {name} ({path}, branch {branch}) at
commit {base}. Do not change any file here; you are only writing the goal.

Read first: the project's own README, CLAUDE.md and AGENTS.md (whichever
exist); then the code the wish touches.
{known}
{ask}"""

DRAFT_KNOWLEDGE = """
The project's knowledge documents (knowledge cards in components/), what each
holds and how it is read:

{cards}
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
its conventions; add or change tests for what you change; {check}; start every
test run through `eki slot -- `, e.g. `eki slot -- npm test`, so test runs
across all agents share the machine's test slots (it waits for a free one); do not
commit — eki commits what you leave here; do not ask questions — decide, and
say what you decided.
Launch browsers only through Playwright's default lookup or $PLAYWRIGHT_BROWSERS_PATH, never
a hard-coded ~/Library/Caches/ms-playwright path, so each browser waits for one of
the machine's browser slots.
Never run `npm install`, `pip install` or any other install inside this
worktree: node_modules and .venv are links to the project folder's own. If a
dependency must change, edit package.json or the lockfile (or pyproject) and
say so in the SUMMARY; eki installs in the project folder after the change
lands.
Card calls: `eki use <card>` run in the foreground blocks until the answer is
back. Started in the background, end your turn and you will be woken with the
answers (up to an hour, or the card's own limit). Don't end a turn saying
you'll wait for notices any other way.
{retry}
End your answer with a `SUMMARY:` paragraph in plain words (what is new, how
to use it, what to check) and then one last line:
`ITEM: done` — or `ITEM: partial` if a slice is done and the rest should be
another item, or `ITEM: person <why>` if this needs a person's hands.
"""


def _check(project) -> str:
    from . import workflow, workflowgates
    wf = workflow.of_project(None, project)
    if wf is not None and wf.rules:
        return ("run the rules whose paths match the files you touch (each as `eki slot -- <command>`) "
                f"before you finish and leave them green — {workflowgates.rules_said(wf)}")
    if project["check_cmd"]:
        cmd = project["check_cmd"]
        return f"run `{cmd}` (as `eki slot -- {cmd}`) before you finish and leave it green"
    if "repo" in project.keys() and project["repo"]:
        return ("there is no check for this project; run the project's own tests before you finish "
                "and leave them green")
    if projects.check_argv(project):
        return "run `bin/check` (as `eki slot -- bin/check`) before you finish and leave it green"
    return "run the project's own tests before you finish and leave them green"


def _cards(project) -> list:
    """The project's own knowledge cards; [] when it has none or they can't be read."""
    try:
        from . import components
        found, _ = components.own(Path(project["path"]))
        return [c for c in found.values() if c.kind == "knowledge"]
    except Exception:  # reading the cards never breaks the plan brief
        return []


def _doc_lines(cards) -> List[str]:
    """One line per knowledge document, card by card; an old one-document card is one document
    named after the card (eki/components/knowledgedocs.py docs)."""
    from .components import knowledge
    out = []
    for c in sorted(cards, key=lambda c: c.name):
        for d in knowledge.docs(c):
            files = ", ".join(str(f) for f in d["files"])
            about = str(d["about"] or "").strip() or "(no about line)"
            out.append(f"- {d['name']} (read: {d['read'] or 'when-relevant'}; card {c.name}): {about}"
                       f" — files: {files}")
    return out


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
    try:
        lines = _doc_lines(_cards(project))
    except Exception:  # reading the documents never breaks the plan brief
        lines = []
    known = KNOWLEDGE.format(cards="\n".join(lines)) if lines else ""
    return (PLAN.format(name=project["name"], path=project["path"], branch=project["branch"],
                        base=base[:12], goal=goal.strip(), split=selfbrief.SPLIT_RULES,
                        rest=REST if standing else "") + known + "\n"
            + ENDING.format(context=CONTEXT_KEY if lines else ""))


def draft(wish: str, base: str, project) -> str:
    """The brief for a project goal's draft run; it ends with selfbrief.DRAFT_ASK, so
    selfbrief.goal_in reads the answer."""
    wish = wish.strip()
    first = wish.splitlines()[0].strip() if wish else "(empty wish)"
    try:
        lines = _doc_lines(_cards(project))
    except Exception:  # reading the documents never breaks the draft brief
        lines = []
    known = DRAFT_KNOWLEDGE.format(cards="\n".join(lines)) if lines else ""
    return DRAFT.format(name=project["name"], path=project["path"], branch=project["branch"],
                        base=base[:12], first=first, wish=wish or "(empty wish)", known=known,
                        ask=selfbrief.DRAFT_ASK)


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
