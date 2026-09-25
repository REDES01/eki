"""One run, in a process of its own: `python -m eki.worker <run id>`.

The engine starts it in a session of its own and forgets it; the worker
owns the run from there. It routes the run if it isn't routed yet, starts
the provider, writes every event straight to the database, and keeps a
heartbeat so the engine can tell it's alive. The engine going away doesn't
touch it. If the worker itself dies, the engine sees the heartbeat stop and
queues the run again; the next worker resumes the program's session.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
import traceback
from typing import Any, Dict

from . import asks, capacity, files, db, mcp, models, paths, providers, quota, routing, skills, store
from .providers.base import Outcome, Turn

log = logging.getLogger("eki.worker")

HEARTBEAT = 2.0
#: what a program is told when its turn was cut off and it's resumed
CARRY_ON = ("(eki restarted while you were working on this. Carry on with the request "
            "where you left off; don't start over.)")


def claim(conn: sqlite3.Connection, rid: str) -> sqlite3.Row | None:
    with db.tx(conn):
        r = store.run(conn, rid)
        if r is None or r["state"] != "starting":
            return None
        if r["cancel"]:
            store.update_run(conn, rid, state="cancelled", ended_at=db.now())
            return None
        store.update_run(conn, rid, state="running", pid=os.getpid(), heartbeat=db.now(),
                         attempt=r["attempt"] + 1, started_at=r["started_at"] or db.now())
    return store.run(conn, rid)


def build_turn(conn: sqlite3.Connection, r: sqlite3.Row, provider: str) -> Turn:
    thread = store.thread(conn, r["thread_id"])
    full = store.transcript(conn, r["thread_id"], r["seq"])
    sess = store.session(conn, r["thread_id"], provider)
    turn = Turn(prompt=r["prompt"], history=full, run_id=r["id"], thread_id=r["thread_id"],
                cwd=(thread["cwd"] if thread and thread["cwd"] else str(paths.scratch())))
    turn.extra["full_history"] = full
    if sess:
        turn.resume = sess["session_id"]
        if sess["seen_run"] == r["id"]:
            # this very run was under way in that session: carry on, don't repeat
            turn.prompt, turn.history = CARRY_ON, []
        else:
            seen = store.run(conn, sess["seen_run"]) if sess["seen_run"] else None
            turn.history = store.transcript_since(conn, r["thread_id"], seen["seq"] if seen else 0, r["seq"])
    turn.extra["claude_plugin"] = skills.claude_plugin()
    turn.extra["claude_mcp"] = mcp.claude_config()
    turn.extra["codex_config"] = mcp.codex_config()
    return turn


def route(conn: sqlite3.Connection, r: sqlite3.Row) -> str | None:
    d = routing.decide(conn, r)
    with db.tx(conn):
        if d.provider is None:
            store.update_run(conn, r["id"], state="queued", why=d.why, row=d.row, pid=None,
                             retry_at=db.now() + 30, attempt=max(0, r["attempt"] - 1))
            return None
        store.update_run(conn, r["id"], provider=d.provider, row=d.row, why=d.why)
        conn.execute("UPDATE threads SET provider=? WHERE id=?", (d.provider, r["thread_id"]))
    store.add_event(conn, r["id"], r["attempt"], "route", {"provider": d.provider, "row": d.row, "why": d.why})
    return d.provider


def finish(conn: sqlite3.Connection, r: sqlite3.Row, provider: str, out: Outcome) -> None:
    rid, now = r["id"], db.now()
    with db.tx(conn):
        cur = store.run(conn, rid)
        if out.state == "done":               # finished before the stop landed: keep the answer
            store.update_run(conn, rid, state="done", ended_at=now, error=None)
        elif cur["cancel"] or out.state == "cancelled":
            store.update_run(conn, rid, state="cancelled", ended_at=now)
        elif out.state == "handed_off":
            store.update_run(conn, rid, state="handed_off", ended_at=now, error=None)
            nxt = store.create_run(conn, r["thread_id"], r["prompt"], priority=r["priority"], parent=rid,
                                   exclude=[provider], row="code")
            reason = " ".join((out.reason or "needs tools").split())
            reason = reason if len(reason) <= 90 else reason[:89] + "…"
            store.update_run(conn, nxt, why=f"handed off by {provider}: {reason}")
            store.add_event(conn, rid, cur["attempt"], "handoff", {"to_run": nxt, "reason": out.reason})
        elif out.state == "limited":
            until = capacity.limited(conn, provider, out.reset_at)
            store.add_event(conn, rid, cur["attempt"], "note", {"text": f"{provider}: {out.error}"})
            if r["pinned"]:
                store.update_run(conn, rid, state="queued", pid=None, retry_at=until,
                                 why=f"you picked {provider} — waiting for its limit to reset")
            else:
                store.update_run(conn, rid, state="queued", pid=None, provider=None, retry_at=None,
                                 exclude=db.dumps(store.excluded(r) + [provider]),
                                 why=f"{provider} hit its limit; trying the next")
        else:
            store.update_run(conn, rid, state="failed", ended_at=now, error=out.error[:1000])


def main(rid: str) -> int:
    conn = db.connect()
    r = claim(conn, rid)
    if r is None:
        return 0
    stop = threading.Event()
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def beat() -> None:
        hconn = db.connect()
        while not done.wait(HEARTBEAT):
            hconn.execute("UPDATE runs SET heartbeat=? WHERE id=?", (db.now(), rid))
            if hconn.execute("SELECT cancel FROM runs WHERE id=?", (rid,)).fetchone()[0]:
                stop.set()
        hconn.close()

    threading.Thread(target=beat, daemon=True).start()
    asks.withdraw_run(conn, rid)          # what an earlier attempt asked, the program will ask again
    attempt = r["attempt"]
    try:
        provider = r["provider"] if (r["provider"] and not r["pinned"]) else route(conn, r)
        if provider is None:
            return 0
        r = store.run(conn, rid)
        turn = build_turn(conn, r, provider)
        turn.stop = stop

        def emit(kind: str, data: Dict[str, Any]) -> None:
            if kind == "quota":                  # what the program says of its plan: kept, not shown
                quota.record(conn, data.get("provider") or provider, data.get("windows") or {})
                return
            store.add_event(conn, rid, attempt, kind, data)
            if kind == "session" and data.get("id"):
                store.save_session(conn, r["thread_id"], provider, str(data["id"]), rid)

        def ask(kind: str, payload: Dict[str, Any]) -> str:
            aid = asks.create(conn, rid, r["thread_id"], kind, payload)
            emit("ask", {"ask": aid, "kind": kind})
            return aid

        turn.extra.update(ask=ask, answers=lambda ids: asks.take_answers(conn, ids),
                          retract=lambda aid: asks.withdraw(conn, aid))

        turn.extra["on_child"] = lambda pid: conn.execute(
            "UPDATE runs SET child_pid=? WHERE id=?", (pid, rid))
        kind = providers.config().get(provider, {}).get("kind")
        if kind == "local" and not models.ensure(provider):
            emit("note", {"text": f"{provider} didn't come up"})
        if kind == "codex":
            skills.sync_codex()
        out = providers.get(provider).take(turn, emit)
        made = {json.loads(e["data"]).get("path") for e in store.events_after(conn, rid)
                if e["kind"] == "tool"}
        for path in files.named_in(store.answer(conn, rid)):
            if path not in made:              # a file the answer names: shown like one it wrote
                emit("tool", {"name": "file", "detail": path, "path": path})
        if stop.is_set() and out.state != "done":
            out.state = "cancelled"
        finish(conn, r, provider, out)
    except Exception:                                   # noqa: BLE001 — a bug must not loop forever
        err = traceback.format_exc()
        log.error(err)
        with db.tx(conn):
            store.update_run(conn, rid, state="failed", ended_at=db.now(), error=err[-1000:])
    finally:
        done.set()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sys.exit(main(sys.argv[1]))
