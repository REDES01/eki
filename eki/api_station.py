"""The Station page's API: what `eki self`, `eki builds`, `eki observe` and the digest show,
and the self actions — each one the function the CLI calls, nothing decided here.

Nothing nudges the engine after an action: this runs inside the engine, whose
next tick picks the work up.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from . import asks, builds, db, digest, observe, queue, score, selfview, selfwork, traincheck, train


# ---- what it shows ------------------------------------------------------------------------

def station(conn: sqlite3.Connection) -> Dict[str, Any]:
    """The self board, and the open questions its draft, plan, build and gate-2 runs ask."""
    board = selfview.board(conn)
    runs = {r for g in selfview.goals(conn) for r in (g["draft_run"], g["plan_run"]) if r}
    runs |= {r for it in conn.execute("SELECT run_id, gate2_run FROM items") for r in it if r}
    return {"self": board, "asks": [asks.view(a) for a in asks.open_asks(conn) if a["run_id"] in runs]}


def builds_view(conn: sqlite3.Connection) -> Dict[str, Any]:
    st = builds.status()
    verdicts = selfview.verdicts(conn)
    out = [{"id": b["id"], "commit": b.get("commit", ""), "made_at": b.get("made_at"),
            "healthy": bool(b.get("healthy")), "check": traincheck.label(Path(b["path"])),
            "verdict": verdicts.get(b["id"], "-"), "path": b["path"],
            "current": b["path"] == st.get("current"), "previous": b["path"] == st.get("previous")}
           for b in st["builds"]]
    return {"running": builds.running_id(), "builds": out,
            "swap": st.get("swap"), "rollback": st.get("rollback")}


def journal(conn: sqlite3.Connection, since: Optional[str] = "24h",
            kind: Optional[str] = None) -> Dict[str, Any]:
    """What `eki observe` prints, newest first; with no kind, the `run` rows are left out."""
    from .cli.observe import summary
    start = db.now() - observe.parse_since(since or "24h")
    if kind in (None, "", "all"):
        kind = None
    elif kind not in observe.KINDS:
        raise ValueError(f"kind is one of {', '.join(observe.KINDS)} or all, not {kind!r}")
    rows = observe.entries(conn, since=start, kind=kind)
    if kind is None:
        rows = [e for e in rows if e["kind"] != "run"]
    return {"entries": [{"t": e["t"], "kind": e["kind"], "run_id": e.get("run_id"),
                         "provider": e.get("provider"), "summary": summary(e), "data": e.get("data")}
                        for e in reversed(rows)]}


def digests(conn: sqlite3.Connection) -> Dict[str, Any]:
    pages = sorted(digest.folder().glob("????-??-??.md"), reverse=True)
    return {"pages": [str(p) for p in pages], "latest": pages[0].read_text() if pages else None}


# ---- what it does --------------------------------------------------------------------------

def write_digest(conn: sqlite3.Connection) -> Dict[str, Any]:
    return {"path": str(digest.write(conn))}


def submit(conn: sqlite3.Connection, body: Dict[str, Any]) -> Dict[str, Any]:
    """A wish (drafted first), or with `as_is` a goal planned as it stands."""
    gid = selfwork.submit(conn, str(body.get("text") or ""), plan=True, draft=not body.get("as_is"))
    g = conn.execute("SELECT state FROM goals WHERE id=?", (gid,)).fetchone()
    return {"goal": gid, "state": g["state"]}


def _item(conn: sqlite3.Connection, iid: str) -> str:
    it = selfwork.store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    return it["id"]


def apply(conn: sqlite3.Connection, iid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"result": queue.apply(conn, _item(conn, iid), yes=bool(body.get("yes")))}


def drop(conn: sqlite3.Connection, iid: str) -> Dict[str, Any]:
    return {"result": selfwork.drop(conn, _item(conn, iid))}


def retry(conn: sqlite3.Connection, iid: str) -> Dict[str, Any]:
    return {"result": selfwork.retry(conn, _item(conn, iid))}


def item_action(conn: sqlite3.Connection, iid: str, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if action == "apply":
        return apply(conn, iid, body)
    return {"drop": drop, "retry": retry}[action](conn, iid)


def release(conn: sqlite3.Connection) -> Dict[str, Any]:
    return {"lines": train.release(conn)}


def autonomy(conn: sqlite3.Connection, body: Dict[str, Any]) -> Dict[str, Any]:
    queue.set_autonomy(str(body.get("mode") or ""))
    return {"autonomy": selfwork.settings()["autonomy"]}


def undo(conn: sqlite3.Connection, build_id: str) -> Dict[str, Any]:
    return {"goal": score.undo(conn, build_id)}
