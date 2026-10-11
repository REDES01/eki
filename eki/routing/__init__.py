"""Where a run goes, and why — a stack, each layer saying why:

1. you picked a provider (`--to`): it goes there, or waits for it;
2. the thread already has one: it stays, unless that one can't take the
   request (it lacks what the request needs, or it's at a limit);
3. constraints: what the request needs (the row's needs, plus vision for
   an attached picture) against what each target can do, availability,
   cooldowns and quota;
4. intent: the prompt check picks a row — rules first, a model for the rest;
5. preference and failover: the row's targets in order, a subscription
   being saved goes last, and moving down the row is failover.

A why reads "rule: names a path → code → claude (codex last: five_hour 82%)".
A request that lands on `code` by rule or check, while a `code-easy` row
exists, is asked once more whether it's small and well-defined (check.narrow):
"rule: has a folder → code; check: easy → code-easy → codex-local". A run
whose row was set beforehand is never narrowed.

A row's targets may name a tier, a model and an effort (eki/targets.py):
needs and capacity are checked by the target's provider, the decision
carries the model and effort the target resolves to (targets.choose, against
the program's read list), and the why names them with the program's own
label: "→ answer → claude · Opus 5.5 · high". A model or effort the program
no longer offers falls back to its default, and the note says so. The
picture rows name ComfyUI workflows the same way ("→ image-hq → comfyui ·
Qwen-Image 2.1 — high quality"); a bare `comfyui` draws with the row's. The check
and narrow are asked of the router role (eki/roles.py); each call's time
goes into role_calls.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .. import capacity, modellist, models, providers, roles, store, targets as targeting
from . import needs, rules
from .check import check, narrow
from .table import row as table_row
from .table import rows as table_rows


@dataclass
class Decision:
    provider: Optional[str]      # None: nothing can take it now
    row: str
    why: str
    model: Optional[str] = None  # the model the target names; None: the provider's default
    effort: Optional[str] = None  # the effort the target names; None: the model's default


def checker_name() -> str:
    return roles.get("router")


def resolve(conn: sqlite3.Connection, target: Any, row: Optional[str] = None) -> Tuple[targeting.Choice, str]:
    """(the choice, its label) for a row target, against providers.json and
    the read model list: `claude · Opus 5.5 · high`; a bare provider
    whose list was never read is just its name. A ComfyUI target always
    names its workflow: a bare one the row's (`row`), one no longer listed
    the default — "→ image → comfyui · FLUX.2 klein — draft"."""
    name = targeting.provider_of(target)
    listed = modellist.listed(conn, name)
    cfg = providers.config().get(name) or {}
    try:
        c = targeting.choose(target, cfg, listed)
    except ValueError as e:                  # a hand-written target settings would refuse
        c = targeting.Choice(None, None, str(e))
    if cfg.get("kind") == "comfyui" and c.model is None:
        from ..providers import comfyui_workflows
        wid = comfyui_workflows.for_row(row if not c.note else None)
        c = targeting.Choice(wid, c.effort, c.note)
        return c, targeting.label(name, wid, c.effort, listed)
    if ":" not in targeting.key(target) and not listed:   # a bare provider, no list read: its name
        return c, name
    return c, targeting.label(name, c.model, c.effort, listed)


def _record(conn: sqlite3.Connection, spent: List[Tuple]) -> None:
    for target, model, ms, ok in spent:
        roles.record(conn, "router", target, model, ms, ok)


def decide(conn: sqlite3.Connection, run: sqlite3.Row) -> Decision:
    avail = capacity.status(conn, background=run["priority"] == "background")
    skip = set(store.excluded(run))

    def can(name: str) -> Tuple[bool, str]:
        if name in skip:
            return False, "tried already"
        return avail.get(name, (False, "no such provider"))

    if run["pinned"] and run["provider"]:
        ok, why = can(run["provider"])
        cfg = providers.config().get(run["provider"], {})
        off = not ok or why == capacity.STARTS
        if off and cfg.get("kind") in models.MANAGED_KINDS and cfg.get("serve") and run["provider"] not in skip:
            return Decision(run["provider"], "picked", f"you picked {run['provider']} (starting it)")
        return Decision(run["provider"] if ok else None, "picked",
                        f"you picked {run['provider']}" + ("" if ok else f" — waiting: {why}"))

    thread = store.thread(conn, run["thread_id"])
    cwd = thread["cwd"] if thread else None
    off = _router_off(conn, cwd, can)
    if off is not None:
        return off
    atts = store.attachments(run)
    prev = None if run["row"] else _previous_answer(conn, run)
    pic = None if run["row"] else previous_picture(conn, run["thread_id"], run["seq"])
    note = ""
    ruled = None
    if thread and thread["provider"] and not run["row"]:
        # only the rules and the attachments count here: staying costs no model call
        ruled = rules.match(run["prompt"], previous=prev, cwd=cwd, attachments=atts,
                            keys={r["key"] for r in table_rows()}, last_picture=pic)
        want = needs.request_needs(table_row(ruled[0]) if ruled else {"needs": []}, atts)
        mine = thread["provider"]
        lack = needs.lacking(mine, want)
        ok, why = can(mine)
        if providers.config().get(mine, {}).get("kind") == "comfyui":
            # a picture thread never answers as "thread": the rule's row picks the graph
            if not ruled:
                note = f"{mine} draws pictures, this isn't one; "
        elif lack:
            note = f"{mine} can't take it (needs {', '.join(lack)}); "
        elif ok:
            return Decision(mine, "thread", f"thread stays with {mine}")
        else:
            note = f"{mine} can't take it ({why}); "

    spent: List[Tuple] = []
    pick = None if run["row"] or ruled else roles.pick("router", conn)
    if run["row"]:
        key, reason = run["row"], (run["why"] or f"row {run['row']}")
    elif ruled:
        key, reason = ruled
    else:
        key, reason = check(run["prompt"], previous=prev, cwd=cwd, pick=pick,
                            attachments=atts, last_picture=pic, spent=spent)
    if not run["row"]:
        key, reason = _narrowed(conn, key, reason, run["prompt"], cwd, pick, spent)
    _record(conn, spent)
    entry = table_row(key)
    want = needs.request_needs(entry, atts)
    targets = list(entry["targets"])
    # a subscription near the end of its plan goes last in its row
    saved = {targeting.key(t): capacity.saving(conn, targeting.provider_of(t)) for t in targets}
    order = ([t for t in targets if not saved[targeting.key(t)]]
             + [t for t in targets if saved[targeting.key(t)]])
    last = "".join(f" ({targeting.key(t)} last: {saved[targeting.key(t)]})" for t in targets
                   if saved[targeting.key(t)]) if len(targets) > 1 else ""
    refused: List[str] = []
    for target in order:
        name = targeting.provider_of(target)
        lack = [] if name in skip else needs.lacking(target, want)
        ok, why = (False, needs.cant(lack)) if lack else can(name)
        if ok:
            c, shown = resolve(conn, target, entry["key"])
            tail = f"; skipped {', '.join(refused)}" if refused else ""
            tail += f"; {c.note}" if c.note else ""
            return Decision(name, entry["key"],
                            f"{note}{reason} → {entry['key']} → {shown}{last}{tail}", c.model, c.effort)
        refused.append(f"{targeting.key(target)}: {why}")
    return Decision(None, entry["key"], f"{note}{reason} → {entry['key']}: waiting — "
                    + ", ".join(refused))


def _router_off(conn: sqlite3.Connection, cwd: Optional[str], can) -> Optional[Decision]:
    """The project's workflow card says router off: straight to its target, no rule or
    model asked. None when there's no card, router is on, or the card can't be read."""
    from .. import workflow
    wf = workflow.of_folder(conn, cwd)
    if wf is None or wf.router != "off" or not wf.target:
        return None
    name = targeting.provider_of(wf.target)
    ok, why = can(name)
    if not ok:
        return Decision(None, "workflow", f"router off (workflow card) → {wf.target}: waiting — {why}")
    c, shown = resolve(conn, wf.target)
    tail = f"; {c.note}" if c.note else ""
    return Decision(name, "workflow", f"router off (workflow card) → {shown}{tail}", c.model, c.effort)


def _narrowed(conn: sqlite3.Connection, key: str, reason: str, prompt: str, cwd: Optional[str],
              pick: Optional[roles.Pick], spent: List[Tuple]) -> Tuple[str, str]:
    """`code` narrowed to `code-easy` when that row exists and the check says easy;
    otherwise `code` stays, and a check that couldn't run leaves the why as it was."""
    if key != "code" or not any(r["key"] == "code-easy" for r in table_rows()):
        return key, reason
    got = narrow(prompt, cwd=cwd, pick=pick or roles.pick("router", conn), spent=spent)
    if got is None:
        return key, reason
    return got[0], f"{reason} → code; {got[1]}"


def _previous_answer(conn: sqlite3.Connection, run: sqlite3.Row) -> Optional[str]:
    before = [r for r in store.thread_runs(conn, run["thread_id"]) if r["seq"] < run["seq"]]
    return store.answer(conn, before[-1]["id"]) if before else None


def previous_picture(conn: sqlite3.Connection, tid: str, seq: int) -> Optional[str]:
    """The picture the thread's previous run drew or was given, or None
    (an older picture doesn't make a follow-up an edit)."""
    before = [r for r in store.thread_runs(conn, tid) if r["seq"] < seq]
    pic = store.last_picture(conn, tid, seq) if before else None
    if pic is None:
        return None
    last = before[-1]
    drawn = [json.loads(e["data"] or "{}") for e in store.events_after(conn, last["id"])
             if e["kind"] == "tool"]
    mine = store.attachments(last) + [str(d.get("path")) for d in drawn if d.get("name") == "image"]
    return pic if pic in mine else None


def explain(conn: sqlite3.Connection, prompt: str, *, cwd: Optional[str] = None,
            attachments: Optional[List[str]] = None,
            thread_id: Optional[str] = None) -> Dict[str, Any]:
    """What would happen to a new request, without making a run: the row,
    the rule or model's reason, the request's needs, and for each target
    whether it may take it and why not. With a thread, as its next request."""
    pic = None
    if thread_id:
        nxt = max([r["seq"] for r in store.thread_runs(conn, thread_id)] or [0]) + 1
        pic = previous_picture(conn, thread_id, nxt)
    spent: List[Tuple] = []
    pick = roles.pick("router", conn)
    key, reason = check(prompt, cwd=cwd, pick=pick, attachments=attachments,
                        last_picture=pic, spent=spent)
    key, reason = _narrowed(conn, key, reason, prompt, cwd, pick, spent)
    _record(conn, spent)
    entry = table_row(key)
    want = needs.request_needs(entry, attachments)
    avail = capacity.status(conn)
    cfgs = providers.config()
    targets = []
    for t in entry["targets"]:
        name = targeting.provider_of(t)
        lack = needs.lacking(t, want)
        ok, why = (False, needs.cant(lack)) if lack else avail.get(name, (False, "no such provider"))
        cfg = cfgs.get(name)
        c, shown = resolve(conn, t, entry["key"]) if cfg is not None else (targeting.Choice(None, None, ""), targeting.key(t))
        targets.append({"name": targeting.key(t), "provider": name, "model": c.model, "effort": c.effort,
                        "label": shown, "note": c.note,
                        "ok": bool(ok), "why": why,
                        "can": providers.capabilities(name, cfg) if cfg is not None else [],
                        "about": str((cfg or {}).get("about") or "")})
    return {"row": entry["key"], "title": entry["title"], "why": reason, "needs": want,
            "targets": targets, "providers": sorted(cfgs)}
