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
import shutil
import signal
import sqlite3
import sys
import threading
import time
import traceback
from typing import Any, Dict

from . import (asks, browsershim, capacity, chatcommit, components, files, db, memnotes, models, notify, observe, paths,
               providers, quota, resettime, routing, skills, stopkind, store, syncstate)
from .components import knowledge, mcpserver, serve, skillcard
from .providers.base import Outcome, Turn
from .routing.needs import is_picture

log = logging.getLogger("eki.worker")

HEARTBEAT = 2.0
#: rows whose provider works on a source picture
SOURCED = ("image-edit", "image-upscale")
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
                cwd=(thread["cwd"] if thread and thread["cwd"] else str(paths.scratch())),
                images=[p for p in store.attachments(r) if is_picture(p)])
    turn.extra["full_history"] = full
    turn.extra["row"] = r["row"]
    carry = bool(sess) and sess["seen_run"] == r["id"]
    if r["row"] in SOURCED and not carry and not turn.images:
        # an edit or upscale with nothing attached works on the thread's last picture
        last = store.last_picture(conn, r["thread_id"], r["seq"])
        turn.images = [last] if last else []
    if r["fresh"] and not carry:
        # a fresh run (a resolve, a chore) never takes over another run's session: its brief
        # is all the context, and an old session can carry that run's background tasks
        turn.history = turn.extra["full_history"] = []
    elif sess:
        turn.resume = sess["session_id"]
        if sess["seen_run"] == r["id"]:
            # this very run was under way in that session: carry on, don't repeat
            turn.prompt, turn.history, turn.images = CARRY_ON, [], []   # the session has the pictures
        else:
            seen = store.run(conn, sess["seen_run"]) if sess["seen_run"] else None
            turn.history = store.transcript_since(conn, r["thread_id"], seen["seq"] if seen else 0, r["seq"])
    turn.extra["claude_plugin"] = [p for p in (skills.claude_plugin(),) if p]   # eki's store first
    if r["row"] != "chore":                     # the project's own skills card, for this run only
        cards = skillcard.cards_for(turn.cwd, conn)
        mine = skillcard.claude_plugin(cards)
        if mine:
            turn.extra["claude_plugin"].append(mine)
        linked = skillcard.codex_dir(cards, r["id"])
        if linked:
            turn.extra["codex_skills"] = linked
    turn.extra["claude_mcp"], turn.extra["codex_config"] = mcpserver.run_config(turn.cwd, r["id"], conn)
    _browser_slots(r, turn)
    if r["model"]:
        turn.extra["model"] = r["model"]
    if r["effort"]:
        turn.extra["effort"] = r["effort"]
    if r["row"] != "chore":                     # which components it can reach, and how to call them
        for line in (components.brief(turn.cwd, conn),
                     knowledge.brief(turn.cwd, conn, context=_item_context(conn, r["thread_id"]))):
            if line:
                turn.extra["note"] = (turn.extra["note"] + "\n\n" + line) if turn.extra.get("note") else line
        _serving_link(conn, r, turn)
        _owner_notes(r, provider, turn)
    return turn


def _item_context(conn: sqlite3.Connection, thread_id: str) -> list[str] | None:
    """The knowledge cards the planner picked for the item built in this thread (its build,
    rebuilds, resolve and PR follow-ups); None — the full brief — for a chat or no choice."""
    try:
        it = conn.execute("SELECT context FROM items WHERE thread_id=? ORDER BY updated_at DESC LIMIT 1",
                          (thread_id,)).fetchone()
        return knowledge.names(it["context"]) if it is not None else None
    except Exception as e:                      # noqa: BLE001 — a run never fails for its brief
        log.warning("no item context for thread %s: %s", thread_id, e)
        return None


def _owner_notes(r: sqlite3.Row, provider: str, turn: Turn) -> None:
    """The owner's Claude notes as an index, and — for a program that isn't Claude Code, which reads
    ~/.claude/CLAUDE.md itself (kept equal to it) — where eki's AGENTS.md is."""
    lines = []
    try:
        lines.append(memnotes.brief())
        md = syncstate.agents_md()
        if md.is_file() and (providers.config().get(provider) or {}).get("kind") != "claude":
            lines.append(f"The owner's instructions for every run: read {md}")
    except Exception as e:                      # noqa: BLE001 — a run never fails for its brief
        log.warning("no owner notes for %s: %s", r["id"], e)
    for line in lines:
        if line:
            turn.extra["note"] = (turn.extra["note"] + "\n\n" + line) if turn.extra.get("note") else line


def _browser_slots(r: sqlite3.Row, turn: Turn) -> None:
    """Every run's Playwright looks up its browsers in the shim, so each browser waits for a slot."""
    try:
        path = str(browsershim.ensure())
    except Exception as e:                      # noqa: BLE001 — a run never fails for its browser slots
        log.warning("no browser shim for %s: %s", r["id"], e)
        return
    turn.extra["env"] = {**(turn.extra.get("env") or {}), "PLAYWRIGHT_BROWSERS_PATH": path}


def _serving_link(conn: sqlite3.Connection, r: sqlite3.Row, turn: Turn) -> None:
    """A run whose project has a serve card gets its own copy's URL: EKI_SERVE_URL and one brief line."""
    try:
        url = serve.for_run(turn.cwd, conn, by=r["id"]) if turn.cwd else ""
    except Exception as e:                      # noqa: BLE001 — a run never fails for its link
        log.warning("no serving link for %s: %s", r["id"], e)
        url = ""
    if not url:
        return
    turn.extra["env"] = {**(turn.extra.get("env") or {}), "EKI_SERVE_URL": url}
    line = (f"Your own copy of the project is served at {url} (EKI_SERVE_URL); open that in a browser "
            "for screenshots and tests, not the person's own server (e.g. :4200). To ask the playtester "
            "for a part, give it a start path/query, never a URL: `eki use playtester \"?jump=s25 <focus>\"`; "
            "eki joins it onto the build it serves for that call.")
    turn.extra["note"] = (turn.extra["note"] + "\n\n" + line) if turn.extra.get("note") else line


def route(conn: sqlite3.Connection, r: sqlite3.Row) -> str | None:
    d = routing.decide(conn, r)
    if r["row"]:
        d.row = r["row"]                       # a row set up front (a chore, a build) stays
    with db.tx(conn):
        if d.provider is None:
            store.update_run(conn, r["id"], state="queued", why=d.why, row=d.row, pid=None,
                             retry_at=db.now() + 30, attempt=max(0, r["attempt"] - 1))
            return None
        # a model or effort the run came with is kept; one an earlier route wrote is routing's to change
        before = _routed(conn, r["id"])
        model = r["model"] if r["model"] and r["model"] != before.get("model") else d.model
        effort = r["effort"] if r["effort"] and r["effort"] != before.get("effort") else d.effort
        store.update_run(conn, r["id"], provider=d.provider, row=d.row, why=d.why, model=model, effort=effort)
        conn.execute("UPDATE threads SET provider=? WHERE id=?", (d.provider, r["thread_id"]))
    store.add_event(conn, r["id"], r["attempt"], "route",
                    {"provider": d.provider, "row": d.row, "why": d.why, "model": d.model,
                     "effort": d.effort})
    return d.provider


def _routed(conn: sqlite3.Connection, rid: str) -> Dict[str, Any]:
    """What the run's last route wrote ({} if it was never routed)."""
    got = conn.execute("SELECT data FROM events WHERE run_id=? AND kind='route' ORDER BY id DESC LIMIT 1",
                       (rid,)).fetchone()
    return json.loads(got[0] or "{}") if got else {}


def stopped(conn: sqlite3.Connection, cur: sqlite3.Row, state: str, error: str = "", reset_at=None,
            provider: str = "") -> stopkind.Stop:
    """Classify a failed, limited or lost run and write runs.stop (inside the caller's transaction).
    The one way eki's own runs and project runs alike learn what kind of stop they had."""
    now = db.now()
    prev = json.loads(cur["stop"]) if cur["stop"] else None
    stop = stopkind.after(prev, stopkind.Failure(state=state, error=error or "", reset_at=reset_at,
                                                 provider=provider or "", now=now))
    store.update_run(conn, cur["id"], stop=db.dumps(stopkind.record(prev, stop, now)))
    return stop


def _others(conn: sqlite3.Connection, r: sqlite3.Row, provider: str) -> bool:
    """Whether a provider other than this one (and those tried already) can take the run now."""
    try:
        skip = set(store.excluded(r)) | {provider}
        avail = capacity.status(conn, background=r["priority"] == "background")
        return any(ok for name, (ok, _) in avail.items() if name not in skip)
    except Exception as e:                      # noqa: BLE001 — can't tell: wait for this one
        log.warning("can't tell who else takes %s: %s", r["id"], e)
        return False


def _heads_up(conn: sqlite3.Connection, provider: str, error: str, stop: stopkind.Stop) -> None:
    """A long quota wait, once per provider per reset: information, not a question."""
    name = stopkind._NAMES.get(provider, provider)
    when = time.strftime("%a %H:%M", time.localtime(stop.retry_at))
    notify.queue(conn, f"quota:{provider}:{int(stop.retry_at)}", "heads_up", "eki · a long wait",
                 f"{name}'s {resettime.label(error)} resets {when}; work for it waits till then.")


def finish(conn: sqlite3.Connection, r: sqlite3.Row, provider: str, out: Outcome) -> None:
    rid, now = r["id"], db.now()
    noted = None                               # what the journal is told, once the run is written
    others = None
    if out.state in ("limited", "failed") and not r["pinned"] and not store.run(conn, rid)["cancel"]:
        others = _others(conn, r, provider)    # asked before the transaction: it looks at programs
    with db.tx(conn):
        cur = store.run(conn, rid)
        if out.state == "done":               # finished before the stop landed: keep the answer
            store.update_run(conn, rid, state="done", ended_at=now, error=None)
        elif cur["cancel"] or out.state == "cancelled":
            store.update_run(conn, rid, state="cancelled", ended_at=now)
        elif out.state == "handed_off" and r["row"] == "chore":   # a chore never reaches Claude
            reason = " ".join((out.reason or "").split())
            store.update_run(conn, rid, state="failed", ended_at=now,
                             error=f"chore handed off: {reason}"[:1000])
        elif out.state == "handed_off":
            store.update_run(conn, rid, state="handed_off", ended_at=now, error=None)
            nxt = store.create_run(conn, r["thread_id"], r["prompt"], priority=r["priority"], parent=rid,
                                   exclude=[provider], row="code", attachments=store.attachments(r))
            reason = " ".join((out.reason or "needs tools").split())
            reason = reason if len(reason) <= 90 else reason[:89] + "…"
            store.update_run(conn, nxt, why=f"handed off by {provider}: {reason}")
            store.add_event(conn, rid, cur["attempt"], "handoff", {"to_run": nxt, "reason": out.reason})
            noted = ("handoff", {"reason": out.reason, "to_run": nxt})
        else:
            noted = _stop(conn, r, cur, provider, out, others)
    if noted:
        observe.record(conn, noted[0], run_id=rid, thread_id=r["thread_id"], provider=provider,
                       data=noted[1])
    observe.run_ended(conn, rid)                # a run still queued (a limit, a retry) isn't written down
    drop_run_skills(rid)


def _stop(conn: sqlite3.Connection, r: sqlite3.Row, cur: sqlite3.Row, provider: str, out: Outcome,
          others) -> tuple | None:
    """A limited or failed run, by the kind of its stop; what the journal is told, if anything.
    Quota and transient wait queued and never touch an item's tries; only environment and
    judgment fail the run."""
    rid, now, error = r["id"], db.now(), out.error or ""
    stop = stopped(conn, cur, out.state, error, out.reset_at, provider)
    if stop.kind == "quota":
        until = capacity.limited(conn, provider, stop.retry_at)
        store.add_event(conn, rid, cur["attempt"], "note", {"text": f"{provider}: {error}"})
        if others:
            store.update_run(conn, rid, state="queued", pid=None, provider=None, retry_at=None,
                             exclude=db.dumps(store.excluded(r) + [provider]),
                             why=f"{provider} hit its limit ({stop.fix}); trying the next")
        else:
            who = f"you picked {provider}" if r["pinned"] else "no other program can take it"
            store.update_run(conn, rid, state="queued", pid=None, retry_at=until,
                             why=f"{who} — waiting for its limit to reset ({stop.fix})")
        if stop.long_wait:
            _heads_up(conn, provider, error, stop)
        return ("limit", {"error": error, "reset_at": stop.retry_at, "pinned": bool(r["pinned"]),
                          "fix": stop.fix})
    if stop.kind == "transient":
        # a dropped connection or a crash says nothing about the work: queue it again on the same
        # provider, so the next worker resumes the same session with CARRY_ON
        wait, tries = int(round(stop.retry_at - now)), (cur["retries"] or 0) + 1
        store.update_run(conn, rid, state="queued", pid=None, retry_at=stop.retry_at, retries=tries,
                         why=f"{provider}: transient error, trying again in {wait} s")
        store.add_event(conn, rid, cur["attempt"], "note",
                        {"text": f"transient error, trying again in {wait} s: {error[:300]}"})
        return ("transient", {"error": error, "retry_in": wait, "retries": tries})
    if stop.kind == "environment":
        store.add_event(conn, rid, cur["attempt"], "note", {"text": f"broken setup: {stop.fix}"})
        error = f"{stop.fix}\n{error}"
    store.update_run(conn, rid, state="failed", ended_at=now, error=error[:1000])
    return None


def drop_run_skills(rid: str) -> None:
    """Remove the run's folder of project skill links (rebuilt if it runs again); never raises."""
    if not rid or "/" in rid or rid.startswith("."):
        return
    try:
        shutil.rmtree(paths.home() / "run-skills" / rid, ignore_errors=True)
    except Exception:                                   # noqa: BLE001 — cleanup is best effort
        pass


def main(rid: str) -> int:
    conn = db.connect()
    r = claim(conn, rid)
    if r is None:
        return 0
    if r["attempt"] == 1 and r["parent"] is None:
        observe.check_correction(conn, rid)
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
        if kind in models.MANAGED_KINDS and not models.ensure(provider):
            emit("note", {"text": f"{provider} didn't come up"})
            finish(conn, r, provider, Outcome(state="limited", error="didn't come up"))
            return 0
        if kind == "codex":
            skills.sync_codex()
        chatcommit.before_turn(conn, r)        # the person's hand edits first, then the turn's own
        out = providers.get(provider).take(turn, emit)
        chatcommit.after_turn(conn, r)
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
        observe.fault(conn, "worker", err, run_id=rid, thread_id=r["thread_id"])
        observe.run_ended(conn, rid)
        drop_run_skills(rid)
    finally:
        done.set()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sys.exit(main(sys.argv[1]))
