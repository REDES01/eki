"""What a Preview links (serve) card shows right now: each running copy with the task it belongs to, and
the person's own copy. serve.live calls `live`; the card's panel (eki/web/cardserve.js) draws it.

    {"links": [{"folder", "url", "task", "run", "started", "stops_at"}],
     "own": {"folder", "url", "up"}}

A copy's task is the run that started or last used its server (its state's last_run / by), else the
newest run whose thread works in that folder, else the item whose worktree it is. `stops_at` is its
last use plus the card's idle_stop minutes; None when the card sets no idle_stop, sets 0, or a run is
using the copy right now. Nothing here starts a server or allots a port.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Optional, Tuple

from . import Component, ports, serve, servers

log = logging.getLogger("eki.components.servelive")

def _title(row) -> str:
    t = (row["title"] or "").strip()
    if not t:
        t = next((ln.strip() for ln in (row["prompt"] or "").splitlines() if ln.strip()), "")
    return t[:120]


def _run(conn: sqlite3.Connection, rid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT r.id, r.prompt, t.title FROM runs r LEFT JOIN threads t ON t.id = r.thread_id "
                        "WHERE r.id=?", (rid,)).fetchone()


def task(conn: sqlite3.Connection, st: Dict[str, Any], folder: str) -> Tuple[str, str]:
    """(title, run id) of what the copy of `folder` belongs to; ("", "") when unknown."""
    for rid in (st.get("last_run"), st.get("by")):
        row = _run(conn, rid) if rid else None
        if row is not None:
            return _title(row), row["id"]
    row = conn.execute("SELECT r.id, r.prompt, t.title FROM runs r JOIN threads t ON t.id = r.thread_id "
                       "WHERE t.cwd IN (?, ?) ORDER BY r.created_at DESC LIMIT 1",
                       (folder, folder.rstrip("/") + "/")).fetchone()
    if row is not None:
        return _title(row), row["id"]
    row = conn.execute("SELECT title, run_id FROM items WHERE worktree IN (?, ?) ORDER BY updated_at DESC LIMIT 1",
                       (folder, folder.rstrip("/") + "/")).fetchone()
    if row is not None:
        return (row["title"] or "").strip(), row["run_id"] or ""
    return "", ""


def _idle_minutes(comp: Component) -> float:
    try:
        return float(comp.spec.get("idle_stop") or 0)
    except (TypeError, ValueError):
        return 0.0


def stops_at(conn: sqlite3.Connection, comp: Component, st: Dict[str, Any]) -> Optional[float]:
    minutes = _idle_minutes(comp)
    if minutes <= 0:
        return None
    last = servers._last_use(conn, st)
    return None if last is None else last + minutes * 60


def own(comp: Component, root) -> Dict[str, Any]:
    """The person's own copy: the project folder's recorded port, never a new one."""
    folder = ports.norm(root)
    port = ports.get(folder)
    if not port:
        return {"folder": folder, "url": "", "up": False}
    return {"folder": folder, "url": serve.entry(comp, f"http://127.0.0.1:{port}{serve._prefix(comp)}"),
            "up": serve.answers(port, folder)}


def live(comp: Component, conn: sqlite3.Connection, root) -> Dict[str, Any]:
    """Each running copy of this card with its task, and the person's own copy. Never raises."""
    out: Dict[str, Any] = {"links": [], "own": {"folder": "", "url": "", "up": False}}
    try:
        mine = {st["served"]: st for st in serve._mine(comp)}
        for x in serve.links(comp):
            if ports.norm(x["folder"]) == ports.norm(root):
                continue                              # the person's own copy is `own`, not a preview link
            st = mine.get(x["folder"], {})
            title, rid = task(conn, st, x["folder"])
            started = st.get("started_at")
            out["links"].append({"folder": x["folder"], "url": x["url"], "task": title, "run": rid,
                                 "started": float(started) if started else None,
                                 "stops_at": stops_at(conn, comp, st)})
        out["links"].sort(key=lambda l: -(l["started"] or 0))
    except Exception as e:                            # noqa: BLE001 — the panel shows nothing rather than break
        log.warning("live links of %s: %s", comp.name, e)
        out["links"] = []
    try:
        out["own"] = own(comp, root)
    except Exception as e:                            # noqa: BLE001
        log.warning("own copy of %s: %s", comp.name, e)
        out["own"] = {"folder": str(root), "url": "", "up": False}
    return out
