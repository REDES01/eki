"""A build or resolve turn that ended with card calls still running
(docs/self-build.md, "Items").

An agent may start `eki use <card>` in the background and end its turn. Its
outstanding calls are the component runs whose parent is that turn and that
are still active (MCP holds end done at once; an exclusive card's hold ends
with its caller, so neither counts). Such an item is not concluded: it goes
to `waiting on calls`, `items.waits` saying what it waits for. `tick` wakes
it once the calls have ended — or after the cap, an hour or the largest
`wait_minutes` among the cards — with a next turn of the same session that
carries their answers. Calls still out keep running; they are the old turn's,
so the woken turn never waits for them again. At most MAX_WAITS wakes per
build try; past that the item is concluded as it would have been.

Everything is in `items.waits` and the runs table: a restart loses nothing.
"""
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from . import components, db, store
from .providers.component import parse

FIRST_LINE = "The calls you started have come back."
MAX_WAITS = 4
CAP_MINUTES = 60
STATE = "waiting on calls"
#: characters of a call's answer the wake message carries
ANSWER_CHARS = 2000
LAST_LINE = "Carry on with your item and end with the usual SUMMARY: and ITEM: lines."


def waits_of(it: sqlite3.Row) -> Optional[dict]:
    """The item's `waits` JSON; None when there is none or it can't be read."""
    try:
        got = json.loads(it["waits"] or "null")
    except (IndexError, KeyError, ValueError, TypeError):
        return None
    return got if isinstance(got, dict) else None


def outstanding(conn: sqlite3.Connection, run_id: str) -> List[sqlite3.Row]:
    """The card calls this very run started that are still active."""
    marks = ",".join("?" * len(store.ACTIVE))
    return conn.execute(f"SELECT * FROM runs WHERE provider='component' AND parent=? AND state IN ({marks})"
                        " ORDER BY created_at", (run_id, *store.ACTIVE)).fetchall()


def _call(r: sqlite3.Row) -> dict:
    try:
        return parse(r["prompt"])
    except ValueError:
        return {"component": "?", "caller_dir": ""}


def card(r: sqlite3.Row) -> str:
    return _call(r)["component"]


def wakes(conn: sqlite3.Connection, it: sqlite3.Row) -> int:
    """Wakes since the item's latest build or resolve run (this build try)."""
    n = 0
    for r in reversed(store.thread_runs(conn, it["thread_id"])):
        if not (r["prompt"] or "").startswith(FIRST_LINE):
            break
        n += 1
    return n


def hold(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row, stage: str) -> Optional[str]:
    """Under the caller's tx: park the item while `run`'s calls are out. None: conclude it as usual."""
    calls = outstanding(conn, run["id"])
    if not calls or wakes(conn, it) >= MAX_WAITS:
        return None
    waits = {"stage": stage, "run": run["id"], "since": db.now(), "calls": [c["id"] for c in calls]}
    conn.execute("UPDATE items SET state=?, waits=?, updated_at=? WHERE id=?",
                 (STATE, db.dumps(waits), db.now(), it["id"]))
    return f"item {it['id']}: {STATE} — " + ", ".join(f"{card(c)} ({c['id'][:8]})" for c in calls)


def stopped(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row) -> List[str]:
    """The board line (and a note in the run) when the bound, not the calls, let a done turn conclude."""
    if run["state"] != "done" or not outstanding(conn, run["id"]) or wakes(conn, it) < MAX_WAITS:
        return []
    line = f"item {it['id']}: stopped waiting for calls after {MAX_WAITS} wakes"
    store.add_event(conn, run["id"], run["attempt"], "note", {"text": line})
    return [line]


def cap_seconds(conn: sqlite3.Connection, calls: List[sqlite3.Row]) -> float:
    """CAP_MINUTES, or the largest positive `wait_minutes` among the calls' cards, in seconds."""
    best = None
    for c in calls:
        want = _call(c)
        try:
            comp = components.get(want["component"], want["caller_dir"], conn)
        except Exception:               # noqa: BLE001 — a card gone or broken says nothing
            continue
        m = comp.spec.get("wait_minutes") if isinstance(comp.spec, dict) else None
        if isinstance(m, (int, float)) and not isinstance(m, bool) and m > 0:
            best = m if best is None else max(best, m)
    return float(best if best is not None else CAP_MINUTES) * 60


def _paths(conn: sqlite3.Connection, rid: str) -> List[str]:
    seen: List[str] = []
    for ev in store.events_after(conn, rid):
        p = json.loads(ev["data"] or "{}").get("path") if ev["kind"] == "tool" else None
        if p and str(p) not in seen:
            seen.append(str(p))
    return seen


def wake_prompt(conn: sqlite3.Connection, calls: List[sqlite3.Row], timed_out: bool) -> str:
    """The next turn's message: what each call came back with."""
    out = [FIRST_LINE]
    for c in calls:
        name = card(c)
        if c["state"] in store.ACTIVE:
            out.append(f"\n{name} run {c['id']}: still running — not waited for any more; `eki show {c['id']}` later")
            continue
        block = [f"\n{name} — run {c['id']}: {c['state']}"]
        said = store.answer(conn, c["id"]).strip()
        if len(said) > ANSWER_CHARS:
            said = said[:ANSWER_CHARS] + f"\n… the whole answer: eki show {c['id']}"
        if said:
            block.append(said)
        block += [f"output: {p}" for p in _paths(conn, c["id"])]
        if c["state"] != "done" and c["error"]:
            block.append(f"error: {c['error']}")
        out.append("\n".join(block))
    out.append("\n" + LAST_LINE)
    return "\n".join(out)


def tick(conn: sqlite3.Connection) -> List[str]:
    """Wake each waiting item whose calls have all ended, or that has waited past its cap."""
    said: List[str] = []
    for it in conn.execute("SELECT * FROM items WHERE state=? ORDER BY created_at", (STATE,)).fetchall():
        said += _wake(conn, it)
    return said


def _wake(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    w = waits_of(it)
    ended = store.run(conn, w["run"]) if w and isinstance(w.get("run"), str) else None
    if w is None or ended is None or w.get("stage") not in ("building", "resolving"):
        with db.tx(conn):
            conn.execute("UPDATE items SET state='left', waits=NULL, error=?, updated_at=? WHERE id=? AND state=?",
                         ("waiting on calls, but what it waits for can't be read", db.now(), it["id"], STATE))
        return [f"item {it['id']}: left — waiting on calls, but what it waits for can't be read"]
    calls = [r for r in (store.run(conn, c) for c in w.get("calls") or [] if isinstance(c, str)) if r is not None]
    out = [c for c in calls if c["state"] in store.ACTIVE]
    timed_out = bool(out)
    if out and db.now() - float(w.get("since") or 0) < cap_seconds(conn, calls):
        return []
    prompt = wake_prompt(conn, calls, timed_out)
    with db.tx(conn):
        cur = conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()
        if cur is None or cur["state"] != STATE:
            return []
        rid = store.create_run(conn, it["thread_id"], prompt, provider=ended["provider"], model=ended["model"],
                               effort=ended["effort"], row=ended["row"], priority=ended["priority"],
                               parent=ended["id"])
        conn.execute("UPDATE items SET state=?, run_id=?, waits=NULL, updated_at=? WHERE id=?",
                     (w["stage"], rid, db.now(), it["id"]))
    why = f"{len(out)} still out after the cap" if timed_out else "all back"
    return [f"item {it['id']}: calls {why}; woke the {'build' if w['stage'] == 'building' else 'resolve'} ({rid[:8]})"]
