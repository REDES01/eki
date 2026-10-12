"""What the agents are told when they plan or build a change to eki, and how
their answers are read (docs/self-build.md)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

SPLIT_RULES = """\
- prefer items that don't depend on each other: they are built at the same time;
- add a dep only when the later item's tests can't pass without the earlier
  item's code;
- a shared interface (an exact function signature, a table and its columns, a
  JSON key) goes into the spec of every item that uses it, word for word, so
  they agree without talking;
- when several items need the same new names, one small first item adds them
  (with working minimal bodies and their tests) and the rest depend only on it:
  one root plus a few leaves, and no dependency chain is longer than two;
- two items may share a file only if one depends on the other; otherwise they
  become one item.
"""

PLAN = """\
You are planning a change to eki — the program that is running you right now.
This folder is a read-only worktree of eki's source at commit {base}. Do not
change any file here; you are only writing the plan.

The goal:

{goal}

Read docs/self-build.md, docs/design.md, ROADMAP.md and CLAUDE.md first, then
the code the goal touches. Then split the goal into items that separate agents
will build at the same time, each in a worktree of its own:

- make the fewest items that keep each agent turn short: about 15 minutes of
  agent work or less, with its tests. Usually that is one item; at most it is
  one root plus a few leaves that depend only on the root;
- never a multi-hour run: when the work is big, split it into parallel parts
  that are merged after, never one long turn;
- each item declares the files it will touch (its write-set, as paths or globs);
{split}- a spec says what to build and how to tell it works — not how to code it.
{checks}
End your answer with a line `ITEMS:` followed by one JSON list, nothing after it:

ITEMS:
[{{"title": "...", "spec": "...", "files": ["eki/x.py", "tests/test_x.py"],
  "deps": ["title of an earlier item"], "independent": false{checks_key}}}]
"""

#: the goal's checks in a plan brief; each item names the ones it delivers
PLAN_CHECKS = """
The goal's checks — an independent judge holds the finished work to each:

{lines}

Give each item a "checks" list: the ids of the checks it delivers, e.g.
["c1", "c3"]. Every check should be delivered by at least one item.
"""
CHECKS_KEY = ',\n  "checks": ["c1", "c3"]'

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
module over 400 lines; add or change tests for what you change; don't run the
whole suite (plain `bin/check`) — run the tests for the files you touched,
e.g. `eki slot -- bin/check tests/test_x.py` (bin/check passes its arguments
to pytest); start every test run through `eki slot -- ` so test runs across all
agents share the machine's test slots (it waits for a free one); eki runs the
whole check right after your turn and sends any failure back; do not commit —
eki commits what you leave here; do not ask questions — decide, and say what
you decided.
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

#: the fifth part of a goal, after the four headings: the checks a judge holds the work to
#: (eki/verdicts.py checks_in reads them)
CHECKS_ASK = """\
After the four parts, write a fifth: a `Checks` heading followed by 3 to 8
lines, each `C<n>: <one concrete, checkable statement — what to run or look at,
and what must be seen>` (C1, C2, …). An independent judge holds the finished
work to these checks, so write each one so that someone who did not build it
can settle it by running a command or reading a file. They are part of the goal
text, before the `DRAFT:` line."""

#: the shared end of a draft brief (this one and eki/projectbrief.py draft): the four
#: headings, the item-shape and AskUserQuestion rules, and the ending goal_in reads
DRAFT_ASK = """\
Then write the goal a planner will split into items, in four parts with these
headings:

What must be true when done
Read first
Item shape and shared files
Tests (bin/check green)

""" + CHECKS_ASK + """

Make it specific enough that a planner can split it without guessing: name real
paths and modules that exist here, the functions and tables it changes, and the
files items will share. For "Item shape": the fewest items that keep each agent
turn to about 15 minutes of agent work or less — usually one item; at most
one small root item (when several items need the same new names) plus a few
leaves that depend only on it, no dependency chain longer than two, never a multi-hour
run (big work splits into parallel parts merged after); items that
don't depend on each other; shared interfaces written into every spec that uses them. Where the wish leaves a real choice open, ask the person
with the AskUserQuestion tool — 2 to 4 options per question, at most three
questions — and fold the answers into the goal. Where it doesn't, don't ask:
decide, and say what you decided in the goal.

End your answer with a line `GOAL:` followed by the goal text, then a final
line `DRAFT: done` — or `DRAFT: person <why>` when the wish can't be made into
a goal.
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

{ask}"""

RANK = """\
Pick eki's next piece of self-work from its open ROADMAP.md entries, and draft
the goal for it. Nobody asked for this: eki is idle and picks its own work, so
do NOT ask the person any questions — decide, and say what you decided.

This folder is a read-only worktree of eki's source at commit {base}. Do not
change any file here; you are only choosing and writing the goal.

Read first: docs/design.md, docs/self-build.md, ROADMAP.md and CLAUDE.md;
{digest}; then the code the entries touch.

eki's score over the last 7 days:

{score}

The open entries:

{entries}

Pick the one entry with the most impact on eki now. Then write the goal a
planner will split into items for that entry, in four parts with these
headings:

What must be true when done
Read first
Item shape and shared files
Tests (bin/check green)

""" + CHECKS_ASK + """

Make it specific enough that a planner can split it without guessing: name real
paths and modules that exist here, the functions and tables it changes, and the
files items will share. For "Item shape": the fewest items that keep each agent
turn to about 15 minutes of agent work or less — usually one item; at most one
small root plus a few leaves that depend only on it, no dependency chain longer
than two, never a multi-hour run (big work splits into parallel parts merged
after).

Answer in this shape, the last lines of your answer:

PICK: <the entry's number>
WHY: <one line: why this entry, now>
GOAL:
<the goal text>
DRAFT: done

— or `DRAFT: person <why>` as the last line when the picked entry can't be
made into a goal without a person.
"""


def plan_checks(checks: Optional[List[Tuple[str, str]]]) -> str:
    """The plan brief's checks section: "" for a goal with no checks."""
    if not checks:
        return ""
    return PLAN_CHECKS.format(lines="\n".join(f"{cid}: {text}" for cid, text in checks))


def plan(goal: str, base: str, checks: Optional[List[Tuple[str, str]]] = None) -> str:
    """The plan brief; `checks` are the goal's [(id, text)] (eki/verdicts.py), listed for tagging."""
    return PLAN.format(goal=goal.strip(), base=base[:12], split=SPLIT_RULES, checks=plan_checks(checks),
                       checks_key=CHECKS_KEY if checks else "")


def draft(wish: str, base: str, *, digest: Optional[str] = None, old: Optional[str] = None) -> str:
    wish = wish.strip()
    first = wish.splitlines()[0].strip() if wish else "(empty wish)"
    seen = (f"the last digest, at {digest}" if digest
            else "there is no digest yet, so skip that")
    ref = (f"\n\nThe old eki's version of the same thing is at {old} — read it as reference\n"
           "only; do not copy it or change it.") if old else ""
    return DRAFT.format(first=first, wish=wish or "(empty wish)", base=base[:12],
                        digest=seen, old=ref, ask=DRAFT_ASK)


def rank(entries: List[Any], base: str, *, digest: Optional[str] = None,
         score: Optional[Dict[str, Any]] = None) -> str:
    """The brief for the draft run that ranks ROADMAP entries (roadmap.Entry:
    .title, .section, .text) and drafts the winner's goal."""
    seen = (f"the last digest, at {digest}" if digest
            else "there is no digest yet, so skip that")
    shown = "\n".join(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}"
                      for k, v in (score or {}).items()) or "(no score yet)"
    listed = "\n\n".join(f"{n}. [{e.section}] {e.title}\n{e.text.strip()}"
                          for n, e in enumerate(entries, 1)) or "(none)"
    return RANK.format(base=base[:12], digest=seen, score=shown, entries=listed)


def build(*, goal: str, title: str, spec: str, files: List[str], branch: str, base: str,
          source: str, others: List[Tuple[str, List[str]]], failure: Optional[str] = None,
          objection: Optional[str] = None) -> str:
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
    return BUILD.format(goal=goal.strip(), title=title, spec=spec.strip(), branch=branch,
                        base=base[:12], source=source, others=beside, retry=retry,
                        files=", ".join(files) or "(not declared — say which in your summary)")


# ---- reading answers ----------------------------------------------------------------------

def items_in(answer: str, allow_empty: bool = False, known: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """The JSON list after the last `ITEMS:` line. Raises ValueError when there isn't one,
    or when it is empty and `allow_empty` is not set (only a standing round may say `[]`).
    Each item's `checks` keeps only ids in `known` (the goal's checks), lower-cased."""
    known = {str(k).lower() for k in known}
    # the marker on a line of its own: a spec may well mention `ITEMS: []` in prose
    marks = [m for m in re.finditer(r"^\s*`?ITEMS:`?\s*$", answer or "", re.M)]
    if not marks:
        marks = [m for m in re.finditer(r"ITEMS:", answer or "")]
    if not marks:
        raise ValueError("the plan has no ITEMS: list")
    tail = answer[marks[-1].end():]
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
                    "independent": bool(it.get("independent")),
                    "context": context_in(it.get("context")),
                    "checks": checks_of_item(it.get("checks"), known)})
    if not out and not allow_empty:
        raise ValueError("the plan has no items")
    return out


def checks_of_item(raw: Any, known: Set[str]) -> List[str]:
    """An item's `checks` as goal check ids, each once: unknown ids (and `spec`) dropped."""
    out: List[str] = []
    for c in raw if isinstance(raw, list) else []:
        cid = str(c).strip().lower() if isinstance(c, (str, int)) else ""
        if cid in known and cid not in out:
            out.append(cid)
    return out


def context_in(raw: Any) -> Optional[List[Dict[str, str]]]:
    """An item's `context` as [{name, why}]: None when it isn't a list; a plain string is a name
    with no why; empty names dropped, each name kept once."""
    if not isinstance(raw, list):
        return None
    out: List[Dict[str, str]] = []
    for e in raw:
        name, why = (e.get("name"), e.get("why")) if isinstance(e, dict) else (e, "")
        name = str(name or "").strip() if isinstance(name, (str, int, float)) else ""
        if name and name not in [c["name"] for c in out]:
            out.append({"name": name, "why": str(why or "").strip()[:200]})
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


def pick_in(answer: str) -> Tuple[Optional[int], str]:
    """(n, why) from the last `PICK:` and `WHY:` lines of a ranking answer.
    n is None when there is no PICK or it isn't a number; why is "" when missing."""
    n: Optional[int] = None
    picks = re.findall(r"^\s*`?PICK:\s*(.*)$", answer or "", re.M)
    if picks:
        m = re.fullmatch(r"#?(\d+)\.?", picks[-1].strip(" `*"))
        n = int(m.group(1)) if m else None
    whys = re.findall(r"^\s*`?WHY:\s*(.*)$", answer or "", re.M)
    return n, (whys[-1].strip(" `") if whys else "")
