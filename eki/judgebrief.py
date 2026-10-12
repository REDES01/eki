"""What a judge run is told (eki/judgerun.py), and the playtester's line.

The judge reads the goal, the checks it must judge and, for an item, the
item and its diff; the builder's summary is shown only as a claim. It
changes nothing, runs tests only through `eki slot`, and files exactly one
verdict per check with `eki verdict` (eki/verdicts.py). Its answer text is
never read as a verdict.
"""
from __future__ import annotations

import shlex
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import verdicts

GOAL_MAX = 2000

INTRO = {
    "item": "You judge one item of work another agent built, against the checks below.",
    "final": "You judge whether a goal is done: every item has landed; judge the whole result against "
             "the checks below.",
    "second": "Another judge could not settle the checks below. You are a second judge on another "
              "backend: look again, by other means where you can.",
    "play": "You judge the result by playing it, against the checks below.",
}

RULES = """\
Rules:
- Change nothing: no edits, no commits, no installs. This folder is a
  read-only worktree.
- Run tests only as `eki slot -- <command>` (e.g. `eki slot -- bin/check tests/test_x.py`).
- The builder's claim is not evidence. Look for yourself: run a command and
  read its output, read the code at file:line, or look at the screen.
- File exactly one verdict per check, each with its own command:

    eki verdict '{form}'

  `verdict` is passed, failed or uncertain. Every key is required;
  `evidence`, `reasoning` are never empty; `counter_evidence` may say "none
  found"; `limitation` says what you could not verify (required when
  uncertain); `suggestion` is a concrete fix (required when failed or
  uncertain). `eki verdict --checks` lists what you still have to file. A
  rejected verdict says why; fix it and file it again.
- Say uncertain rather than guess.
- End your answer with one line: `JUDGED: <n>`, the number of verdicts you filed.
"""


def _checks(checks: Iterable[Tuple[str, str]]) -> str:
    return "\n".join(f"{cid}: {text}" for cid, text in checks) or "(none)"


def brief(goal: str, checks: List[Tuple[str, str]], scope: str, item: Optional[Dict[str, Any]] = None,
          diff: Optional[str] = None, claim: Optional[str] = None) -> str:
    """The judge run's prompt. `checks`: [(id, text)], `spec` included for an item's own check;
    `item`: {title, spec}; `diff`: the command that shows the change (`git diff <base>..<sha>`)."""
    goal = (goal or "").strip()
    if len(goal) > GOAL_MAX:
        goal = goal[:GOAL_MAX].rstrip() + "\n…(cut)"
    parts = [INTRO.get(scope, INTRO["final"]), "", "The goal:", "", goal, ""]
    if item is not None:
        parts += [f"The item: **{item.get('title', '')}**", "", str(item.get("spec") or "").strip(), ""]
    if diff:
        parts += [f"The change: run `{diff}` to see it.", ""]
    parts += ["The checks to judge, by id:", "", _checks(checks), ""]
    if any(cid == verdicts.SPEC for cid, _ in checks):
        parts += [f"`{verdicts.SPEC}` asks: does the change do what the item's spec says, without obvious "
                  "bugs, missing tests or stray changes?", ""]
    if claim:
        parts += ["The builder's claim — not evidence:", "", claim.strip(), ""]
    parts.append(RULES.format(form=verdicts.FORM))
    return "\n".join(parts)


def play_line(wf: Any, title: str, checks: List[Tuple[str, str]]) -> str:
    """`eki use <playtester> '<message>' | eki verdict --lines`: the playtester plays the change
    and says one `VERDICT: {json}` line per check; only the screen is what it sees."""
    focus = "\n".join(f"- {cid}: {text}" for cid, text in checks)
    message = (f"play the change: {title}\n\nFocus:\n{focus}\n\nFor each check above, end with one line "
               f"(JSON on one line):\nVERDICT: {verdicts.FORM}")
    return f"eki use {shlex.quote(wf.playtester)} {shlex.quote(message)} | eki verdict --lines"
