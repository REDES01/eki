"""What the agents are told when they plan or build a change to eki, and how
their answers are read (docs/self-build.md)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

PLAN = """\
You are planning a change to eki — the program that is running you right now.
This folder is a read-only worktree of eki's source at commit {base}. Do not
change any file here; you are only writing the plan.

The goal:

{goal}

Read docs/self-build.md, docs/design.md, ROADMAP.md and CLAUDE.md first, then
the code the goal touches. Then split the goal into items that separate agents
will build at the same time, each in a worktree of its own:

- an item is small: one to three files, under an hour of work, with its tests;
- each item declares the files it will touch (its write-set, as paths or globs);
  two items that must touch the same file either become one item, or the later
  one lists the earlier one in "deps";
- when two items share an interface (a function one adds and another calls),
  write the exact signature into both specs so they agree without talking;
- a spec says what to build and how to tell it works — not how to code it;
- one item is fine when the goal is small.

End your answer with a line `ITEMS:` followed by one JSON list, nothing after it:

ITEMS:
[{{"title": "...", "spec": "...", "files": ["eki/x.py", "tests/test_x.py"],
  "deps": ["title of an earlier item"], "independent": false}}]
"""

BUILD = """\
You are changing eki — the program that is running you right now — in a
worktree of its own: this folder, branch {branch}, from commit {base}. Nothing
you do here touches the eki that runs; eki judges what you leave and proposes
it. Never touch anything outside this folder — not ~/.eki, not {source}.

The goal: {goal}

Your item: **{title}**

{spec}

Files you are expected to touch: {files}.
{others}
Rules: read CLAUDE.md and docs/design.md first and keep the invariants; no
module over 400 lines; add or change tests for what you change; run
`bin/check` before you finish and leave it green; do not commit — eki commits
what you leave here; do not ask questions — decide, and say what you decided.
{retry}
End your answer with a `SUMMARY:` paragraph in plain words (what is new, how
to use it, what to check) and then one last line:
`ITEM: done` — or `ITEM: partial` if a slice is done and the rest should be
another item, or `ITEM: person <why>` if this needs a person's hands.
"""

RESOLVE = """\
You are resolving a rebase conflict in eki — the program that is running you
right now. This folder is the worktree of one change to eki, stopped in the
middle of `git rebase` onto commit {head}: the head that changed since the
change was built. Never touch anything outside this folder.

What the change is for — the goal: {goal}

The change: **{title}**

{spec}

What its builder said it did:

{summary}

The rebase stopped with a conflict in: {conflicted}.

What the head changed in those files:

{head_changes}

Resolve the conflicts so both intents survive: what the change is for, and
what the head did. Leave no conflict markers (<<<<<<<, =======, >>>>>>>) in
any file. Then run `bin/check` and leave it green.

Do not commit, do not run `git rebase --continue`, `--skip` or `--abort`, do
not `git add` or reset — eki carries the rebase on once you're done. Do not
touch anything outside this folder; do not ask questions — decide, and say
what you decided.

End your answer with a `SUMMARY:` paragraph in plain words (how you resolved
each file, what to check) and then one last line:
`ITEM: done` — or `ITEM: person <why>` if this needs a person's hands.
"""

DRAFT = """\
Draft a goal for eki from this wish: {first}

The whole wish, as the person typed it:

{wish}

This folder is a read-only worktree of eki's source at commit {base}. Do not
change any file here; you are only writing the goal.

Read first: docs/design.md, docs/self-build.md, ROADMAP.md and CLAUDE.md;
{digest}; the output of `eki observe --since 7d --kind fault,correction` if it
prints anything; then the code the wish touches.{old}

Then write the goal a planner will split into items, in four parts with these
headings:

What must be true when done
Read first
Item shape and shared files
Tests (bin/check green)

Make it specific enough that a planner can split it without guessing: name real
paths and modules that exist here, the functions and tables it changes, and the
files items will share. Where the wish leaves a real choice open, ask the person
with the AskUserQuestion tool — 2 to 4 options per question, at most three
questions — and fold the answers into the goal. Where it doesn't, don't ask:
decide, and say what you decided in the goal.

End your answer with a line `GOAL:` followed by the goal text, then a final
line `DRAFT: done` — or `DRAFT: person <why>` when the wish can't be made into
a goal.
"""


def plan(goal: str, base: str) -> str:
    return PLAN.format(goal=goal.strip(), base=base[:12])


def draft(wish: str, base: str, *, digest: Optional[str] = None, old: Optional[str] = None) -> str:
    wish = wish.strip()
    first = wish.splitlines()[0].strip() if wish else "(empty wish)"
    seen = (f"the last digest, at {digest}" if digest
            else "there is no digest yet, so skip that")
    ref = (f"\n\nThe old eki's version of the same thing is at {old} — read it as reference\n"
           "only; do not copy it or change it.") if old else ""
    return DRAFT.format(first=first, wish=wish or "(empty wish)", base=base[:12],
                        digest=seen, old=ref)


def build(*, goal: str, title: str, spec: str, files: List[str], branch: str, base: str,
          source: str, others: List[Tuple[str, List[str]]], failure: Optional[str] = None) -> str:
    beside = ""
    if others:
        lines = "\n".join(f"- {t}: {', '.join(f) or '(files not declared)'}" for t, f in others)
        beside = ("Other items are being built beside yours right now — stay out of their files:\n"
                  f"{lines}\n")
    retry = ""
    if failure:
        retry = ("\nA previous attempt at this item failed its checks. What failed:\n\n"
                 f"{failure.strip()}\n\nFix that as well.\n")
    return BUILD.format(goal=goal.strip(), title=title, spec=spec.strip(), branch=branch,
                        base=base[:12], source=source, others=beside, retry=retry,
                        files=", ".join(files) or "(not declared — say which in your summary)")


def resolve(*, goal: str, title: str, spec: str, summary: str, head: str, conflicted: List[str],
            head_changes: str) -> str:
    return RESOLVE.format(goal=goal.strip(), title=title, spec=spec.strip() or "(no spec)",
                          summary=(summary or "").strip() or "(nothing said)", head=head[:12],
                          conflicted=", ".join(conflicted) or "(none named)",
                          head_changes=head_changes.strip() or "(nothing found)")


# ---- reading answers ----------------------------------------------------------------------

def items_in(answer: str) -> List[Dict[str, Any]]:
    """The JSON list after the last `ITEMS:` line. Raises ValueError when there isn't one."""
    idx = answer.rfind("ITEMS:")
    if idx < 0:
        raise ValueError("the plan has no ITEMS: list")
    tail = answer[idx + len("ITEMS:"):]
    tail = re.sub(r"```(?:json)?", "", tail).strip()
    start, end = tail.find("["), tail.rfind("]")
    if start < 0 or end < start:
        raise ValueError("the ITEMS: list isn't a JSON list")
    try:
        data = json.loads(tail[start:end + 1])
    except ValueError as e:
        raise ValueError(f"the ITEMS: list isn't valid JSON: {e}") from e
    out = []
    for i, it in enumerate(data if isinstance(data, list) else []):
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            raise ValueError(f"item {i + 1} has no title")
        out.append({"title": str(it["title"]).strip()[:120],
                    "spec": str(it.get("spec") or "").strip(),
                    "files": [str(f).strip() for f in (it.get("files") or []) if str(f).strip()],
                    "deps": [str(d).strip() for d in (it.get("deps") or []) if str(d).strip()],
                    "independent": bool(it.get("independent"))})
    if not out:
        raise ValueError("the plan has no items")
    return out


def outcome(answer: str) -> Tuple[str, str, str]:
    """(ITEM: verdict, its reason, the SUMMARY text) from a builder's answer."""
    verdict, reason = "done", ""
    m = None
    for m in re.finditer(r"^\s*`?ITEM:\s*(done|partial|person)\b\s*`?(.*)$", answer or "", re.M | re.I):
        pass
    if m:
        verdict, reason = m.group(1).lower(), m.group(2).strip(" `")
    summary = ""
    s = (answer or "").rfind("SUMMARY:")
    if s >= 0:
        summary = answer[s + len("SUMMARY:"):]
        summary = re.split(r"^\s*`?ITEM:", summary, maxsplit=1, flags=re.M)[0].strip()
    return verdict, reason, summary[:2000]


def goal_in(answer: str) -> Tuple[Optional[str], str]:
    """(goal text, "") from a drafter's answer, or (None, why) when there is no goal."""
    lines = (answer or "").splitlines()
    draft_at = [i for i, ln in enumerate(lines) if re.match(r"^\s*`?DRAFT:", ln)]
    if draft_at:
        last = re.sub(r"^\s*`?DRAFT:", "", lines[draft_at[-1]]).strip(" `")
        m = re.match(r"person\b\s*(.*)$", last, re.I)
        if m:
            return None, m.group(1).strip(" `:-") or "the drafter left it for you"
    goal_at = [i for i, ln in enumerate(lines) if re.match(r"^\s*`?GOAL:", ln)]
    if not goal_at:
        return None, "the draft has no GOAL: block"
    g = goal_at[-1]
    end = draft_at[-1] if draft_at and draft_at[-1] > g else len(lines)
    body = [re.sub(r"^\s*`?GOAL:`?", "", lines[g])] + lines[g + 1:end]
    text = "\n".join(ln for ln in body if not ln.strip().startswith("```")).strip()
    return (text, "") if text else (None, "the draft has no GOAL: block")
