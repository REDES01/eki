"""What a fixer run is told (eki/fixer.py): an eki item that failed — gate 1,
gate 2, a resolve, or a build that ended its turn early — put on integration
main, what it is for, the failure, what landed since its base on the files it
touched (eki/landed.py), and the rule. The answer is read by eki/selfbrief.py
outcome, as a builder's is."""
from __future__ import annotations

from typing import List

#: the brief's first line: a thread's fixer runs are the runs whose prompt starts with it
FIRST_LINE = "You are the fixer: one more try at a change to eki before it is left for a person."

#: said word for word in every brief
RULE = ("Make the item's intent work with what landed. Update a test only when a landed change "
        "made its expectation wrong; never weaken, skip or delete a check to make it pass.")

STAGES = {
    "gate1": "Gate 1 failed: the checks (bin/check) were red on the change in its own worktree.",
    "gate2": ("Gate 2 failed: the whole check was red with the change on top of the queue's head. "
              "It may have passed alone and clash with what landed, or a landed change may have made "
              "one of its tests stale."),
    "resolve": ("The change could not be rebased onto main: the conflict resolver did not settle it, "
                "or the rebase would not go on."),
    "nothing": ("The build ended its turn early without changing anything (it stopped mid-way, or said "
                "it was waiting). Do the item: build what the spec asks."),
}

BRIEF = """\
{first}
The change is to eki — the program that is running you right now. This folder
is the change's own worktree. Never touch anything outside this folder — not
~/.eki, not the source checkout.

What the change is for — the goal: {goal}

The change: **{title}**

{spec}

What its builder said it did:

{summary}

What went wrong: {stage}

{failure}

{where}

What landed on main since the change was built, on the files it touched or
conflicted on — each change and what it was for:

{landed}

The rule: {rule}

Find the cause and fix it here. Run only the tests for the files you touch,
e.g. `eki slot -- bin/check tests/test_x.py` (bin/check passes its arguments
to pytest) — not the whole suite: eki runs the change through its gates again
right after your turn. Do not commit; eki commits what you leave. Do not ask
questions — decide, and say what you decided. If the failure is a real bug in
the intent that you cannot fix, or two intents truly contradict, say so and end
with `ITEM: person <why>`.

End your answer with a `SUMMARY:` paragraph in plain words (what failed, why,
what you changed and why that keeps the rule) and then one last line:
`ITEM: done` — or `ITEM: partial` if a slice is done, or `ITEM: person <why>` if
this needs a person's hands.
"""

ON_MAIN = "This folder has the change's own commits on top of integration main ({head})."

MID_REBASE = """\
This folder is stopped in the middle of `git rebase` onto integration main
({head}), with conflicts in: {conflicted}. Resolve them in the files — leave no
markers (<<<<<<<, =======, >>>>>>>) — keeping both this change's intent and the
landed one's. Do not run `git rebase --continue`, `--skip` or `--abort`, do not
`git add`, commit or reset: eki carries the rebase on once you're done."""

NO_FAILURE = "(no output was kept)"
NOTHING_LANDED = "(no landed change on record touched these files)"


def brief(*, stage: str, goal: str, title: str, spec: str, summary: str, failure: str,
          head: str, landed: str, conflicted: List[str]) -> str:
    where = (MID_REBASE.format(head=head[:12], conflicted=", ".join(conflicted)) if conflicted
             else ON_MAIN.format(head=head[:12]))
    told = (failure or "").strip()
    return BRIEF.format(first=FIRST_LINE, goal=(goal or "").strip(), title=title,
                        spec=(spec or "").strip() or "(no spec)",
                        summary=(summary or "").strip() or "(nothing said)",
                        stage=STAGES.get(stage, stage),
                        failure=f"```\n{told}\n```" if told else NO_FAILURE,
                        where=where, landed=(landed or "").strip() or NOTHING_LANDED, rule=RULE)
