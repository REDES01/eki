"""What a resolve run is told (docs/self-build.md, "The queue"): an item stopped
mid-rebase on a conflict, what it is for, what the head did to the conflicted
files and what each landed change was for (eki/landed.py). The same brief for
eki and for a person's project; only who it is and how to check differ. The
answer is read by eki/selfbrief.py outcome."""
from __future__ import annotations

import json
from typing import List, Optional

from . import projects

#: the brief's first line, the same for eki and for projects: eki/resolve.py
#: resolve_runs counts a thread's runs whose prompt starts with it
FIRST_LINE = "You are resolving a rebase conflict in a change stopped mid-rebase."
#: the second line of a retry's brief: eki/resolve.py reads a run's prompt for it
#: to know the run was the second try
RETRY_LINE = "This is your second and last try at this conflict."

WHO_EKI = """\
The change is to eki — the program that is running you right now. This folder
is its worktree, stopped in the middle of `git rebase` onto commit {head}: the
head that changed since the change was built. Never touch anything outside
this folder."""

WHO_PROJECT = """\
The change is to {name}, a project at {path}. This folder is the change's own
worktree, stopped in the middle of `git rebase` onto commit {head}: the tip of
the project's branch, which changed since the change was built. Never touch
anything outside this folder — not {path}, not ~/.eki."""

CHECK_EKI = """\
Then run the tests for the files you touched, e.g. `bin/check
tests/test_x.py` (bin/check passes its arguments to pytest) — don't run the
whole suite (plain `bin/check`): eki runs the whole check right after your
turn and sends any failure back."""

CHECK_PROJECT = """\
Then run the project's check, `{cmd}`, and leave it green — eki runs it again
right after your turn."""

CHECK_NONE = """\
The project has no check; run the project's own tests for what you touched
and leave them green."""

BRIEF = """\
{first}
{who}

What the change is for — the goal: {goal}

The change: **{title}**

{spec}

What its builder said it did:

{summary}

The rebase stopped with a conflict in: {conflicted}.

What the head changed in those files:

{head_changes}

What the other side was for — each change that landed on the head since this
one was built and touched those files:

{landed}

Resolve the conflicts so every intent survives: what this change is for, and
what each landed change above is for — not only the text of the head. Leave no
conflict markers (<<<<<<<, =======, >>>>>>>) in any file. {check}

Edit the conflicted files yourself. Describing the fix in your answer is not
enough: eki checks the files for markers right after your turn, and a file
that still has them is a failed resolve.

Append-only files — logs, notes, decision logs, changelogs — where both sides
added lines: keep both sides' lines, the head's first, then this change's,
and remove the markers.

If two intents truly contradict — no file can do both — do not pick one
silently: say what contradicts and end with `ITEM: person <what contradicts>`.

Do not commit, do not run `git rebase --continue`, `--skip` or `--abort`, do
not `git add` or reset — eki carries the rebase on once you're done. Do not
touch anything outside this folder; do not ask questions — decide, and say
what you decided.

End your answer with a `SUMMARY:` paragraph in plain words (how you resolved
each file, how each intent — this change's and each landed one's — survived,
what to check) and then one last line:
`ITEM: done` — or `ITEM: person <why>` if this needs a person's hands.
An answer with no `SUMMARY:` and `ITEM:` line counts as a failed resolve.
"""

RETRY = """What went wrong on the first try: {failure}.

The hunks still marked:

{hunks}
"""

NO_HUNKS = "(none — no file has markers left; the answer is what failed)"

NOTHING_LANDED = "(nothing known beyond the diff above: no landed change on record touched these files)"


def check_cmd(project) -> Optional[str]:
    """The command inside the project's check argv (workflow card or its own check), or None."""
    argv = projects.check_argv(project)
    if not argv:
        return None
    parts = json.loads(argv)
    return parts[2] if len(parts) > 2 else " ".join(parts)


def resolve(*, goal: str, title: str, spec: str, summary: str, head: str, conflicted: List[str],
            head_changes: str, landed: str, project=None) -> str:
    if project is None:
        who, check = WHO_EKI.format(head=head[:12]), CHECK_EKI
    else:
        who = WHO_PROJECT.format(name=project["name"], path=project["path"], head=head[:12])
        cmd = check_cmd(project)
        check = CHECK_PROJECT.format(cmd=cmd) if cmd else CHECK_NONE
    return BRIEF.format(first=FIRST_LINE, who=who, check=check, goal=goal.strip(), title=title,
                        spec=spec.strip() or "(no spec)",
                        summary=(summary or "").strip() or "(nothing said)",
                        conflicted=", ".join(conflicted) or "(none named)",
                        head_changes=head_changes.strip() or "(nothing found)",
                        landed=(landed or "").strip() or NOTHING_LANDED)


def retry(*, previous_failure: str, hunks: str, **kw) -> str:
    """The brief for the second and last resolve run: the first's failure and what is still marked."""
    text = resolve(**kw)
    rest = text[len(FIRST_LINE) + 1:]
    told = RETRY.format(failure=previous_failure.strip().rstrip("."), hunks=hunks.strip() or NO_HUNKS)
    return f"{FIRST_LINE}\n{RETRY_LINE}\n\n{told}\n{rest}"
