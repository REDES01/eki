"""A short title for each new chat of yours, written by a `title` chore.

A chat starts titled from its prompt (`title_by='prompt'`). Once it has a done
run with an answer, `start_due` gives the `chores` role one `title` chore for
it (subject: the thread id), a few per pass and one try per thread. `settle`
writes a done chore's cleaned text over the title only while it is still the
prompt's, so a title you set always wins; a failed, cancelled or empty chore
leaves the prompt title. Threads from before titles existed (`'old'`) and
eki's own are never retitled. The chores table is all the state there is, so
a restart at any moment loses nothing.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import chores, db, store

KIND = "title"
#: the most chores started in one pass
PER_PASS = 3
#: how much of the first prompt and the first answer the model sees
CUT = 1500
MAX = 80

_QUOTES = "\"'“”‘’«»`"

#: chats of yours still titled from the prompt, with a done run and no title chore yet
_DUE = f"""
SELECT t.id FROM threads t
 WHERE t.by IS NULL AND t.title_by='prompt' AND t.deleted_at IS NULL
   AND EXISTS (SELECT 1 FROM runs r WHERE r.thread_id=t.id AND r.state='done')
   AND NOT EXISTS (SELECT 1 FROM chores c WHERE c.kind='{KIND}' AND c.subject=t.id)
   AND t.id NOT IN (SELECT thread_id FROM chores WHERE thread_id IS NOT NULL)
 ORDER BY t.created_at
"""


def prompt_for(conn: sqlite3.Connection, tid: str) -> Optional[str]:
    """The chore's prompt from the thread's first prompt and first answer, or
    None when no done run has said anything yet."""
    runs = conn.execute("SELECT id, prompt, state FROM runs WHERE thread_id=?"
                        " ORDER BY seq", (tid,)).fetchall()
    if not runs:
        return None
    first = (runs[0]["prompt"] or "").strip()
    reply = next((a for a in (store.answer(conn, r["id"]).strip()
                              for r in runs if r["state"] == "done") if a), "")
    if not first or not reply:
        return None
    return ("Here is the start of a conversation.\n\n"
            f"The person wrote:\n{first[:CUT]}\n\n"
            f"The answer began:\n{reply[:CUT]}\n\n"
            "Give it a 3–6 word title in the language the person wrote in, "
            "no quotes, no trailing period, only the title.")


def clean(text: str) -> str:
    """The first non-empty line, outer quotes and a trailing period stripped,
    at most MAX characters; '' when nothing is left."""
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    while len(line) >= 2 and line[0] in _QUOTES and line[-1] in _QUOTES:
        line = line[1:-1].strip()
    line = line.strip(_QUOTES).strip()
    while line.endswith(".") and not line.endswith(".."):
        line = line[:-1].rstrip()
    line = line.strip(_QUOTES).strip()
    return line[:MAX].strip()


def start_due(conn: sqlite3.Connection) -> List[str]:
    """Start a title chore, up to PER_PASS per pass, for each chat of yours titled
    from its prompt with a done answer and no title chore yet."""
    said: List[str] = []
    for row in conn.execute(_DUE).fetchall():
        if len(said) >= PER_PASS:
            break
        prompt = prompt_for(conn, row["id"])
        if prompt is None:
            continue
        with db.tx(conn):
            if chores.latest(conn, KIND, row["id"]) is not None:   # started meanwhile
                continue
            rid = chores.start(conn, KIND, row["id"], prompt, "background")
        said.append(f"title: chore {rid} for thread {row['id']}" if rid
                    else f"title: skipped for thread {row['id']}")
    return said


def settle(conn: sqlite3.Connection) -> List[str]:
    """Write each finished title chore's cleaned text where the title is still
    the prompt's (a title you set always wins), then close the chore."""
    said: List[str] = []
    for c in chores.finished(conn, KIND):
        title = ""
        if c["run_state"] == "done":
            title = clean(store.answer(conn, c["run_id"]))
        with db.tx(conn):
            if not title:
                why = "no title in the answer" if c["run_state"] == "done" else \
                    c["run_error"] or c["run_state"] or "its run is gone"
                if chores.close(conn, c["id"], "failed", why):
                    said.append(f"title: none for thread {c['subject']}: {why}")
                continue
            if not chores.close(conn, c["id"], "done", title):
                continue
            wrote = conn.execute("UPDATE threads SET title=?, title_by='eki'"
                                 " WHERE id=? AND title_by='prompt'",
                                 (title, c["subject"])).rowcount
            if wrote:
                said.append(f"title: thread {c['subject']} is now {title!r}")
    return said
