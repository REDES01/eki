"""What the web UI can ask for. Plain functions from a request to JSON-able data;
`server.py` does the HTTP. Everything here is also reachable from the CLI."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from . import asking, asks, attachments, capacity, digest, engine, machine, models, paths, providers, quota, routing, store

TERMINAL = ("done", "failed", "cancelled", "handed_off")


def _run_view(conn: sqlite3.Connection, r: sqlite3.Row, *, full: bool = True) -> Dict[str, Any]:
    view: Dict[str, Any] = {k: r[k] for k in ("id", "seq", "prompt", "state", "provider", "row", "why",
                                              "priority", "attempt", "error", "parent", "created_at",
                                              "ended_at")}
    if full:
        view["answer"] = store.answer(conn, r["id"])
        view["steps"] = [{"kind": e["kind"], **json.loads(e["data"])}
                         for e in store.events_after(conn, r["id"])
                         if e["kind"] in ("tool", "note", "interrupted", "handoff")]
        view["last_event"] = max((e["id"] for e in store.events_after(conn, r["id"])), default=0)
        view["asks"] = [asks.view(a) for a in asks.for_run(conn, r["id"]) if a["state"] != "withdrawn"]
        view["files"] = sorted({e["path"] for e in view["steps"] if e.get("path")})
        view["attachments"] = store.attachments(r)
    return view


def threads(conn: sqlite3.Connection, limit: int = 60) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.*, MAX(r.created_at) AS updated, "
        " SUM(CASE WHEN r.state IN ('queued','starting','running') THEN 1 ELSE 0 END) AS active, "
        " (SELECT COUNT(*) FROM asks a WHERE a.thread_id = t.id AND a.state = 'open') AS waiting "
        "FROM threads t LEFT JOIN runs r ON r.thread_id = t.id "
        "GROUP BY t.id ORDER BY waiting > 0 DESC, active > 0 DESC, updated DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": t["id"], "title": t["title"], "provider": t["provider"], "cwd": t["cwd"],
             "updated": t["updated"] or t["created_at"], "working": bool(t["active"]),
             "needs_you": bool(t["waiting"])} for t in rows]


def thread(conn: sqlite3.Connection, tid: str) -> Dict[str, Any]:
    t = store.thread(conn, tid)
    if t is None:
        raise KeyError(f"no thread {tid}")
    return {"id": t["id"], "title": t["title"], "provider": t["provider"], "cwd": t["cwd"],
            "runs": [_run_view(conn, r) for r in store.thread_runs(conn, t["id"])]}


def events(conn: sqlite3.Connection, tid: str, after: int, wait: float = 20) -> Dict[str, Any]:
    """New events in a thread, waiting up to `wait` seconds for the first one."""
    end = time.time() + wait
    while True:
        rows = conn.execute(
            "SELECT e.* FROM events e JOIN runs r ON r.id = e.run_id "
            "WHERE r.thread_id = ? AND e.id > ? ORDER BY e.id LIMIT 500", (tid, after)).fetchall()
        active = conn.execute("SELECT COUNT(*) FROM runs WHERE thread_id=? AND state IN "
                              "('queued','starting','running')", (tid,)).fetchone()[0]
        waiting = conn.execute("SELECT COUNT(*) FROM asks WHERE thread_id=? AND state='open'",
                               (tid,)).fetchone()[0]
        if rows or time.time() >= end:
            return {"events": [{**json.loads(e["data"]), "id": e["id"], "run": e["run_id"],
                                "kind": e["kind"]} for e in rows],
                    "active": active, "waiting": waiting}
        time.sleep(0.15)


def ask(conn: sqlite3.Connection, body: Dict[str, Any]) -> Dict[str, Any]:
    tid, rid = asking.submit(conn, str(body.get("prompt") or ""), thread=body.get("thread") or None,
                             to=body.get("to") or None, cwd=body.get("cwd") or None,
                             background=bool(body.get("background")),
                             attachments=_paths(body.get("attachments")))
    return {"thread": tid, "run": rid}


def upload(data: bytes, name: str) -> Dict[str, Any]:
    """A picture from the window, saved to send with the next request."""
    return {"path": attachments.save(data, name)}


def _paths(value: Any) -> List[str]:
    """A body's attachments: a list of paths, or one path on its own."""
    if not value:
        return []
    return [str(value)] if isinstance(value, str) else [str(p) for p in value]


def answer(conn: sqlite3.Connection, aid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"result": asks.answer(conn, aid, body)}


def open_asks(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [asks.view(a) for a in asks.open_asks(conn)]


def cancel(conn: sqlite3.Connection, rid: str) -> Dict[str, Any]:
    return {"result": store.cancel(conn, rid)}


def provider_list(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    avail = capacity.status(conn)
    out = []
    for name, cfg in providers.config().items():
        if name in providers.BUILTIN:
            continue
        ok, why = avail.get(name, (False, "?"))
        item = {"name": name, "kind": cfg.get("kind"), "label": cfg.get("label") or name,
                "ok": ok, "why": why}
        if cfg.get("kind") in models.MANAGED_KINDS:
            item["model"] = models.status(name)
        q = quota.reading(conn, name)
        if q:
            item["quota"] = q
        out.append(item)
    return out


def model_action(name: str, action: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if action == "start":
        return models.start(name)
    if action == "stop":
        return models.stop(name, hold=bool((body or {}).get("hold")))
    raise ValueError(f"unknown action {action}")


def status(conn: sqlite3.Connection) -> Dict[str, Any]:
    active = store.runs_in(conn, store.ACTIVE)
    room, why = machine.room()
    page = digest.latest()
    return {"digest": str(page) if page else None,"engine": engine.running_pid(), "pid": os.getpid(), "home": str(paths.home()),
            "running": sum(r["state"] in ("starting", "running") for r in active),
            "queued": sum(r["state"] == "queued" for r in active),
            "room": room, "room_why": why, "memory_pressure": machine.memory_pressure()}


def route(conn: sqlite3.Connection, prompt: Optional[str]) -> Dict[str, Any]:
    if not prompt:
        from .routing.table import rows
        return {"checker": routing.checker_name(), "rows": rows()}
    return routing.explain(conn, prompt)
