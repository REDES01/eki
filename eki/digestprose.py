"""The digest's patch notes and triage, written by the local model (docs/self-build.md,
"The journal, the score and the digest").

The pages themselves are built without a model (eki/digest.py): a rules-only
short page and the long page beside it. Once they are written, `start` gives
the local model one `digest` chore: patchnotes.brief of the day's changes (no
ids) and the journal's faults, corrections, handoffs and limits in the window,
scrubbed and capped. `tick` finishes done chores. A readable answer puts
`## Triage` on the long page before `## Score`, once; its NOTES replace the
short page's headline and sections only when patchnotes.check finds nothing
wrong, else the rules-only page stands byte-identical and the chore fails with
the reason. Triage only suggests — faults.py stays the only thing that opens
items. The chores table and the pages are all the state there is, so a
restart at any moment loses nothing.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import chores, db, digest, observe, patchnotes, store

KIND = "digest"
#: the most of the journal the model is given
CAP = 20000
JOURNAL_KINDS = ("fault", "correction", "handoff", "limit")
TRIAGE, SCORE = "## Triage", "## Score"

_THINK = re.compile(r"<think>.*?</think>", re.S)
_BLOCK = re.compile(r"^\s*(NOTES|TRIAGE)\s*:\s*(.*)$", re.I)


def material(conn: sqlite3.Connection, since: float, until: float) -> str:
    """The journal rows in the window, scrubbed, at most CAP characters."""
    lines = []
    for kind in JOURNAL_KINDS:
        for e in observe.entries(conn, since=since, until=until, kind=kind):
            data = json.dumps(e.get("data") or {}, ensure_ascii=False, sort_keys=True)
            lines.append((e["t"], f"- {digest._clock(e['t'])} {kind}: {data}"))
    journal = "\n".join(text for _, text in sorted(lines)) or "Nothing."
    text = observe.scrub(f"# The journal\n\n{journal}\n")
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
    brief = patchnotes.brief(digest.changes(conn, since, until))
    prompt = f"{brief}\n---\n\n{material(conn, since, until)}"
    with db.tx(conn):
        return chores.start(conn, KIND, str(page), prompt, "background")


def parse(text: str) -> Optional[Tuple[str, List[str]]]:
    """(notes, triage lines) from the answer, or None when either block is missing or empty."""
    blocks = {}
    current = None
    for line in _THINK.sub("", text or "").splitlines():
        m = _BLOCK.match(line)
        if m:
            current = m.group(1).upper()
            blocks[current] = [m.group(2)] if m.group(2).strip() else []
        elif current:
            blocks[current].append(line)
    if "NOTES" not in blocks or "TRIAGE" not in blocks:
        return None
    notes = "\n".join(trim(ln.strip() for ln in blocks["NOTES"] if ln.strip()))
    triage = [ln.strip() for ln in blocks["TRIAGE"] if ln.strip().startswith(("- ", "* "))]
    triage = ["- " + ln[2:].strip() for ln in triage]
    if not notes or not triage:
        return None
    return notes, triage


def trim(lines) -> List[str]:
    """The notes without what the page adds itself: a leading title (`# eki …`) and a
    closing `Waits for you…` line — a small model writes them anyway, and they are
    not what the check is for."""
    out = [ln for ln in lines]
    while out and out[0].startswith("#"):
        out.pop(0)
    while out and out[-1].lower().startswith("waits for you"):
        out.pop()
    return [ln.lstrip("#").strip() if ln.startswith("#") else ln for ln in out]   # '## Web UI' → 'Web UI'


def weave(page: str, triage: List[str]) -> str:
    """The long page with `## Triage` before `## Score`, or at the end without one."""
    lines = page.split("\n")
    block = [TRIAGE, "", *triage, ""]
    at = next((i for i, ln in enumerate(lines) if ln.strip() == SCORE), None)
    if at is None:
        lines += ([""] if lines and lines[-1] else []) + block
    else:
        lines[at:at] = block
    return "\n".join(lines)


def layout(notes: str) -> str:
    """The notes as the page shows them: the headline, then a blank line before each section."""
    out = []
    for i, ln in enumerate(notes.splitlines()):
        if i and not ln.startswith("- "):
            out.append("")
        out.append(ln)
    return "\n".join(out)


def _triage(page: Path, triage: List[str]) -> None:
    """Put the triage on the long page once, if the long page is there."""
    long = digest.long_of(page)
    try:
        text = long.read_text()
    except OSError:
        return
    if not any(ln.strip() == TRIAGE for ln in text.splitlines()):
        digest._atomic(long, weave(text, triage))


def _finish(conn: sqlite3.Connection, c: sqlite3.Row) -> Tuple[str, Optional[str], str]:
    """(state, result, what was said) for one finished chore; writes the pages if it may."""
    page = Path(c["subject"])
    if c["run_state"] != "done":
        why = c["run_error"] or c["run_state"] or "its run is gone"
        return "failed", why, f"digest notes failed for {page.name}: {why}"
    got = parse(store.answer(conn, c["run_id"]))
    if got is None:
        return "failed", "no NOTES: and TRIAGE: blocks", f"digest notes unreadable for {page.name}"
    notes, triage = got
    _triage(page, triage)
    try:
        text = page.read_text()
    except OSError:
        return "failed", "the page is gone", f"digest notes: {page.name} is gone"
    reason = patchnotes.check(notes, patchnotes.areas_in(text))
    if reason is not None:
        return "failed", reason, f"digest notes kept to the rules for {page.name}: {reason}"
    digest._atomic(page, patchnotes.with_notes(text, layout(notes)))
    return "done", notes, f"digest notes written to {page.name}"


def tick(conn: sqlite3.Connection) -> List[str]:
    """Finish the done digest chores, each once."""
    said = []
    for c in chores.finished(conn, KIND):
        state, result, line = _finish(conn, c)
        with db.tx(conn):
            if chores.close(conn, c["id"], state, result):
                said.append(line)
    return said
