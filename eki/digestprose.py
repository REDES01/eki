"""The digest's prose and triage, written by the local model (docs/self-build.md,
"The journal, the score and the digest").

The page itself is still built without a model (eki/digest.py). Once it is
written, `start` gives the local model one `digest` chore for it: the page and
the journal's faults, corrections, handoffs and limits in its window, scrubbed
and capped. `tick` finishes done chores: a readable answer adds `## In short`
under the title and `## Triage` before `## Score`, once, atomically; anything
else leaves the page byte-identical. Triage only suggests — faults.py stays
the only thing that opens items. The chores table and the page are all the
state there is, so a restart at any moment loses nothing.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import chores, db, digest, observe, store

KIND = "digest"
#: the most of the page and the journal the model is given
CAP = 20000
JOURNAL_KINDS = ("fault", "correction", "handoff", "limit")
SHORT, TRIAGE, SCORE = "## In short", "## Triage", "## Score"

ASK = """\
Below is today's digest of eki, a program that runs and improves itself, and \
the journal's faults, corrections, handoffs and limits over the same time.

Answer with exactly two blocks and nothing else:

PROSE:
A few plain sentences on the day: what went well, what went wrong, what waits.

TRIAGE:
- <what> — fix | watch | ignore — <why>
One line per thing worth a look. A `fix` line names the wish to run, as \
eki self "<the wish>". Write "- nothing — ignore — a quiet day" if there is none.
"""

_THINK = re.compile(r"<think>.*?</think>", re.S)
_BLOCK = re.compile(r"^\s*(PROSE|TRIAGE)\s*:\s*(.*)$", re.I)


def material(conn: sqlite3.Connection, page: Path, since: float, until: float) -> str:
    """The page and the journal rows in its window, scrubbed, at most CAP characters."""
    lines = []
    for kind in JOURNAL_KINDS:
        for e in observe.entries(conn, since=since, until=until, kind=kind):
            data = json.dumps(e.get("data") or {}, ensure_ascii=False, sort_keys=True)
            lines.append((e["t"], f"- {digest._clock(e['t'])} {kind}: {data}"))
    journal = "\n".join(text for _, text in sorted(lines)) or "Nothing."
    text = observe.scrub(f"{page.read_text()}\n\n# The journal\n\n{journal}\n")
    if len(text) > CAP:
        cut = len(text) - CAP
        text = text[:CAP] + f"\n[… {cut} more characters of the journal cut here]\n"
    return text


def start(conn: sqlite3.Connection, page: Path, since: float, until: float) -> Optional[str]:
    """One `digest` chore for this page (the run's id), unless one is open or done
    for it already; None when there is no local model or one exists."""
    prev = chores.latest(conn, KIND, str(page))
    if prev is not None and prev["state"] in ("open", "done"):
        return None
    prompt = f"{ASK}\n---\n\n{material(conn, page, since, until)}"
    with db.tx(conn):
        return chores.start(conn, KIND, str(page), prompt, "background")


def parse(text: str) -> Optional[Tuple[str, List[str]]]:
    """(prose, triage lines) from the answer, or None when either block is missing."""
    blocks = {}
    current = None
    for line in _THINK.sub("", text or "").splitlines():
        m = _BLOCK.match(line)
        if m:
            current = m.group(1).upper()
            blocks[current] = [m.group(2)] if m.group(2).strip() else []
        elif current:
            blocks[current].append(line)
    if "PROSE" not in blocks or "TRIAGE" not in blocks:
        return None
    prose = " ".join(" ".join(blocks["PROSE"]).split())
    triage = [ln.strip() for ln in blocks["TRIAGE"] if ln.strip().startswith(("- ", "* "))]
    triage = ["- " + ln[2:].strip() for ln in triage]
    if not prose or not triage:
        return None
    return prose, triage


def weave(page: str, prose: str, triage: List[str]) -> str:
    """The page with `## In short` under the title and `## Triage` before `## Score`."""
    lines = page.split("\n")
    title = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1)
    short = ["", SHORT, "", prose]
    lines[title + 1:title + 1] = short
    block = [TRIAGE, "", *triage, ""]
    at = next((i for i, ln in enumerate(lines) if ln.strip() == SCORE), None)
    if at is None:
        lines += ([""] if lines and lines[-1] else []) + block
    else:
        lines[at:at] = block
    return "\n".join(lines)


def _finish(conn: sqlite3.Connection, c: sqlite3.Row) -> Tuple[str, Optional[str], str]:
    """(state, result, what was said) for one finished chore; writes the page if it may."""
    page = Path(c["subject"])
    if digest.long_of(page).exists():
        page = digest.long_of(page)        # the short page is patch notes; the prose goes beside it
    if c["run_state"] != "done":
        why = c["run_error"] or c["run_state"] or "its run is gone"
        return "failed", why, f"digest prose failed for {page.name}: {why}"
    got = parse(store.answer(conn, c["run_id"]))
    if got is None:
        return "failed", "no PROSE: and TRIAGE: blocks", f"digest prose unreadable for {page.name}"
    prose, triage = got
    try:
        text = page.read_text()
    except OSError:
        return "failed", "the page is gone", f"digest prose: {page.name} is gone"
    if any(ln.strip() == SHORT for ln in text.splitlines()):
        return "done", prose, f"digest prose: {page.name} already has it"
    digest._atomic(page, weave(text, prose, triage))
    return "done", prose, f"digest prose added to {page.name}"


def tick(conn: sqlite3.Connection) -> List[str]:
    """Finish the done digest chores, each once."""
    said = []
    for c in chores.finished(conn, KIND):
        state, result, line = _finish(conn, c)
        with db.tx(conn):
            if chores.close(conn, c["id"], state, result):
                said.append(line)
    return said
