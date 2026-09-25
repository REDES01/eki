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


def plan(goal: str, base: str) -> str:
    return PLAN.format(goal=goal.strip(), base=base[:12])


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
