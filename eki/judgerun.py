"""Start one judge run: its own backend, its own thread, a read-only worktree.

The first judge is the `judge` role's pick (roles.pick) — by default the code
row's first target, the same provider that most likely built the change: the
person's choice (2026-10-12, "no use the same provider"). It is still never
the builder's run: a run of its own, on a new thread, with no context of the
build. Only the second opinion on an uncertain check (scope `second`) is kept
off the providers in `avoid` (the builder's and the first judge's) through
roles.pick_other, when another can run.
The run is an agent run on a new thread with `fresh=True`, in the worktree
`tree` (`judge-<item id>` or `accept-<goal id>`) at `ref`, and it is
registered in `judgings` in the same transaction that creates it — so a
restart finds either both or neither.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional, Tuple

from . import roles, store, verdicts, workspace
from .workspace import WorkspaceError

#: the scope of a second opinion, the one judge kept off the backends before it
SECOND = "second"


def start(conn: sqlite3.Connection, *, goal_id: str, item_id: Optional[str], scope: str,
          checks: Iterable[str], round: int, avoid: Iterable[str], tree: str, repo: str, ref: str,
          prompt: str, background: bool) -> Tuple[Optional[str], str]:
    """(run id, '') or (None, why). Works inside the caller's tx, or opens its own."""
    avoid = sorted({a for a in avoid if a})
    if scope == SECOND:        # an uncertain check: once more on another backend than before
        pick = roles.pick_other("judge", conn, set(avoid), background=background)
    else:                      # the first judge: the judge role's pick, the builder's provider or not
        pick = roles.pick("judge", conn, background=background)
    if pick.provider is None:
        return None, f"no judge: {pick.why}"
    try:
        wt = workspace.add(repo, tree, base=ref, branch=f"eki/{tree}")
    except (WorkspaceError, OSError) as e:
        return None, f"no worktree for the judge: {e}"
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        tid = store.create_thread(conn, f"judge ({scope}): {item_id or goal_id}", str(wt), by="eki")
        rid = store.create_run(conn, tid, prompt, provider=pick.provider, model=pick.model, effort=pick.effort,
                               row="code", priority="background" if background else "now", fresh=True)
        if pick.why:
            store.update_run(conn, rid, why=f"judge: {pick.why}")
        verdicts.register(conn, rid, goal_id, item_id, scope, checks, round, avoid)
    except BaseException:
        if own:
            conn.execute("ROLLBACK")
        raise
    if own:
        conn.execute("COMMIT")
    return rid, ""


def ended(conn: sqlite3.Connection, run_id: Optional[str]) -> bool:
    """No run, or it is no longer going."""
    run = store.run(conn, run_id) if run_id else None
    return run is None or run["state"] not in store.ACTIVE

