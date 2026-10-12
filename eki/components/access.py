"""Who may call a component now: queue, exclusive, memory and conflicts.

The engine asks `admit` for each queued run whose provider is 'component'.
Nothing is kept in memory: queues and holds are read from the runs table and
component.json every time, so they are the same after a restart.

- access "queue": at most `slots` calls in starting/running, in created_at
  order. A card on one of eki's providers (a picture card's target
  `comfyui:<workflow>`, or the old `"provider"`) shares that provider: every
  working call of any card on it and the provider's own starting/running runs
  count, for slots, memory and conflicts alike, so two picture cards on
  different workflows wait for each other. A picture card is a queue unless it
  says otherwise. An agent card's target is the model it runs on, not a queue
  it shares: its slots count only its own working calls.
- access "exclusive": the caller run that first used it holds it until that
  run ends; a call with no caller holds it only while it runs.
- "memory_gb": a call that would start it waits while the Mac has less free.
- "conflicts" (both ways): wait while a conflicting one is in use; an idle one
  that is up is stopped first.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

from .. import components, db, machine, store, targets
from . import slotwait

ACTIVE = ("queued", "starting", "running", "interrupted")
WORKING = ("starting", "running")
WAITS = "waits for "


def _call(run) -> Optional[dict]:
    try:
        c = json.loads(run["prompt"])
    except (TypeError, ValueError):
        return None
    return c if isinstance(c, dict) and isinstance(c.get("component"), str) else None


def _calls(conn: sqlite3.Connection, name: str, where: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Call runs of component `name` matching the extra SQL condition on c (the call) / p (its parent)."""
    rows = conn.execute("SELECT c.*, p.state AS parent_state FROM runs c LEFT JOIN runs p ON p.id=c.parent "
                        f"WHERE c.provider='component' AND ({where}) ORDER BY c.created_at", params).fetchall()
    return [r for r in rows if (_call(r) or {}).get("component") == name]


def holders(conn: sqlite3.Connection, name: str) -> List[str]:
    """The runs holding an exclusive component: callers still active whose call has started,
    and a running call with no caller."""
    marks = ",".join("?" * len(ACTIVE))
    rows = _calls(conn, name, f"(c.started_at IS NOT NULL OR c.state IN ('starting','running')) AND "
                              f"((c.parent IS NOT NULL AND p.state IN ({marks})) OR "
                              f"(c.parent IS NULL AND c.state IN ('starting','running')))", ACTIVE)
    out: List[str] = []
    for r in rows:
        h = r["parent"] or r["id"]
        if h not in out:
            out.append(h)
    return out


def provider_of(spec: Optional[dict]) -> str:
    """The eki provider a card runs on: its "provider", or its picture target's; "" for the others."""
    spec = spec or {}
    if spec.get("kind") == "agent":
        return ""               # its target is the model it runs on, not a queue it shares
    if isinstance(spec.get("provider"), str):
        return spec["provider"].strip()
    if "target" in spec and not any(k in spec for k in ("run", "serve")):
        try:
            return targets.parse(spec["target"]).provider
        except ValueError:
            return ""
    return ""


def _access(spec: dict) -> str:
    if "access" in spec:
        return spec["access"]
    return "queue" if "target" in spec and (provider_of(spec) or spec.get("kind") == "agent") else "shared"


Specs = Dict[str, Dict[str, components.Component]]


def _on(conn: sqlite3.Connection, call: dict, specs: Specs) -> str:
    """The provider of the component a call names, found from the call's folder (cached in `specs`)."""
    folder = call.get("caller_dir")
    key = str(folder or "")
    if key not in specs:
        try:
            specs[key] = components.find(folder, conn)[0]
        except Exception:
            specs[key] = {}
    c = specs[key].get(call["component"])
    return provider_of(c.spec) if c is not None else ""


def _peers(conn: sqlite3.Connection, name: str, provider: str, where: str, params: tuple = (),
           specs: Optional[Specs] = None) -> List[sqlite3.Row]:
    """Call runs of `name`, and of any component on the same provider, matching `where`."""
    if not provider:
        return _calls(conn, name, where, params)
    specs = {} if specs is None else specs
    rows = conn.execute("SELECT c.*, p.state AS parent_state FROM runs c LEFT JOIN runs p ON p.id=c.parent "
                        f"WHERE c.provider='component' AND ({where}) ORDER BY c.created_at", params).fetchall()
    out = []
    for r in rows:
        call = _call(r)
        if call and (call["component"] == name or _on(conn, call, specs) == provider):
            out.append(r)
    return out


def _working(conn: sqlite3.Connection, name: str, provider: str = "",
             specs: Optional[Specs] = None) -> List[sqlite3.Row]:
    return _peers(conn, name, provider, "c.state IN ('starting','running')", (), specs)


def _provider_runs(conn: sqlite3.Connection, provider: Optional[str]) -> List[sqlite3.Row]:
    if not provider:
        return []
    return conn.execute("SELECT * FROM runs WHERE provider=? AND state IN ('starting','running') "
                        "ORDER BY created_at", (provider,)).fetchall()


def _provider_busy(conn: sqlite3.Connection, provider: Optional[str]) -> int:
    return len(_provider_runs(conn, provider))


def in_use(conn: sqlite3.Connection, comp_name: str, spec: Optional[dict] = None,
           specs: Optional[Specs] = None) -> str:
    """'' when idle, else who is using it."""
    prov = provider_of(spec)
    w = _working(conn, comp_name, prov, specs)
    if w:
        return f"in use by {w[0]['parent'] or w[0]['id']}"
    if (spec or {}).get("access") == "exclusive":
        h = holders(conn, comp_name)
        if h:
            return f"held by {h[0]}"
    if _provider_busy(conn, prov):
        return f"{prov} busy"
    return ""


def _conflicting(comp, found: Dict[str, components.Component]) -> Set[str]:
    names = set(comp.spec.get("conflicts") or [])
    names |= {n for n, c in found.items() if comp.name in (c.spec.get("conflicts") or [])}
    names.discard(comp.name)
    return names


def _is_up(comp) -> bool:
    try:
        return bool(components.kind_module(comp.kind).is_up(comp))
    except Exception:
        return False


def gate(conn: sqlite3.Connection, run) -> Tuple[bool, str]:
    """(may start, why not). A call that can't be parsed or found may start: the provider fails it."""
    call = _call(run)
    if call is None:
        return True, ""
    name, caller = call["component"], run["parent"]
    folder = call.get("caller_dir")
    try:
        found, _ = components.find(folder, conn)
        comp = found.get(name) or components.get(name, folder, conn)
    except Exception:
        return True, ""
    spec = comp.spec
    prov = provider_of(spec)
    specs: Specs = {str(folder or ""): found}
    access = _access(spec)
    if access == "queue":
        slots = int(spec.get("slots", 1))
        counted = list(_working(conn, name, prov, specs)) + _provider_runs(conn, prov)
        if len(counted) >= slots:
            words, _ = slotwait.why(conn, name, slots, counted, run["why"] or "")
            return False, f"{WAITS}{name}: {words}"
        ahead = [r for r in _peers(conn, name, prov, "c.state='queued' AND c.created_at < ?",
                                   (run["created_at"],), specs) if r["id"] != run["id"]]
        if ahead:
            return False, f"{WAITS}{name}: queued behind {ahead[0]['id']}"
    elif access == "exclusive":
        held = [h for h in holders(conn, name) if h != caller]
        if held:
            return False, f"{WAITS}{name}: held by {held[0]}"
    conflicts = _conflicting(comp, found)
    for other in sorted(conflicts):
        busy = in_use(conn, other, found[other].spec if other in found else None, specs)
        if busy:
            return False, f"{WAITS}{name}: conflicts with {other}, {busy}"
    for other in sorted(conflicts):
        if other in found and _is_up(found[other]):
            try:
                stopped = components.kind_module(found[other].kind).stop(found[other])
            except Exception:
                stopped = False
            if not stopped or _is_up(found[other]):
                return False, f"{WAITS}{name}: conflicts with {other}, which won't stop"
    need = spec.get("memory_gb")
    if need and (spec.get("run") or not _working(conn, name, prov, specs) and not _is_up(comp)):
        free = machine.free_gb()
        if free < need:
            return False, f"{WAITS}{name}: needs {need:g} GB, {free:.0f} free"
    return True, ""


def admit(conn: sqlite3.Connection, run) -> bool:
    """Gate a queued call run; write its why only when it changes, and note the first wait
    on the caller's run so the wait shows there. Never raises."""
    try:
        ok, why = gate(conn, run)
    except Exception as e:  # a bug here must not hold calls forever or break the engine
        ok, why = True, ""
        print(f"component access: {type(e).__name__}: {e}", flush=True)
    before = run["why"] or ""
    if ok:
        if before.startswith(WAITS):
            store.update_run(conn, run["id"], why=None)
        return True
    if why != before:
        with db.tx(conn):
            store.update_run(conn, run["id"], why=why)
            p = conn.execute("SELECT id, attempt FROM runs WHERE id=?", (run["parent"],)).fetchone() \
                if run["parent"] else None
            if p is not None and not conn.execute(
                    "SELECT 1 FROM events WHERE run_id=? AND kind='note' AND data LIKE ?",
                    (p["id"], f'%"call": "{run["id"]}"%')).fetchone():
                store.add_event(conn, p["id"], p["attempt"], "note", {"text": why, "call": run["id"]})
    return False
