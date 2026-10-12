"""The queue's stages of the work model (docs/work-model.md): admit, the side
path (resolve), gate 2 and its bisect, landing. Pure, as eki/workstep.py is;
`step` hands these over.

The stack spans goals, so a gate-2 event carries the whole stack as the
executor sees it, front first: `[{"id", "result"}]`, result green | red |
running | None. Each goal fed the event decides for its own items only, so
feeding every goal in the stack gives each action exactly once.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import workstep
from .workmodel import Action, ItemState, Settings, moved

Moved = Tuple[ItemState, Tuple[Action, ...]]


# ---- in and out of the queue ----------------------------------------------------------------

def enqueue(it: ItemState, why: str, back: bool = False) -> Moved:
    """Into the queue, at the back, gate 2 to come (queue.apply, queue._admit, resolve._requeue)."""
    new = moved(it, "queued", why, gate2=None)
    return new, (Action("merge", it.id, why, {"back": back} if back else {}),)


def requeue(it: ItemState, why: str) -> Moved:
    return enqueue(it, why, back=True)


def seen(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Any) -> Moved:
    """What git says of a ready or queued item: in the target already, or can it be queued
    (selfwork._settle_applied, queue._admit, projectqueue._admit / _already_in)."""
    d = ev.data
    where = d.get("where") or "its commit is in the target"
    if d.get("in_target"):
        if it.stage == "proposed":                    # a person merged it
            return moved(it, "applied", f"applied — {where}"), ()
        if it.stage == "locked":
            return moved(it, "landed", f"landed — {where}", gate2="green"), ()
        if it.stage in ("queued", "gate 2"):          # a restart after the target moved
            return land(it, s, d)
        return it, ()
    if it.stage != "proposed" or not d.get("queueable"):
        return it, ()
    # the executor's facts when it gives them (eki: locks.locked_in, autonomy apply), else the settings'
    held = list(d["locked"]) if "locked" in d else locked_in(it.files + it.touched, s.locked)
    if held:
        what = f"locked, touches {', '.join(held)}"
        new = moved(it, "locked", f"locked — touches {', '.join(held)}; needs a person")
        return new, (workstep.needs_you(new, what, f"eki self apply {it.id} --yes"),)
    auto = d["apply"] if "apply" in d else s.merge == "auto"
    if not auto or (it.review or "").startswith("no:"):    # objected twice: a person's call
        return it, ()
    onto = d.get("onto")
    return enqueue(it, f"queued for {onto}" if onto else "queued")


def merged(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Any) -> Moved:
    """The item's rebase onto its predicted head (queue._rebase): on it, conflicted or failed."""
    d = ev.data
    if it.stage not in ("queued", "gate 2"):
        return it, ()
    result = d.get("result")
    if result == "rebased":
        if "batch" not in s.gates:                    # nothing is checked together
            new = moved(it, "queued", d.get("unbatched") or "gate 2 skipped — not batched", gate2="green")
            return new, (Action("land", it.id, new.why),)
        return moved(it, "queued", d.get("line") or f"rebased on {d.get('on') or 'its head'}", gate2=None), ()
    if result == "conflict":
        files = ", ".join(d.get("files") or ()) or "?"
        new = moved(it, "resolving", f"conflict in {files} → resolving", gate2=None)
        return new, (Action("start_run", it.id, new.why, {"role": "resolve", "stage": "resolving", "tries": it.tries,
                                                          "runs": int(d.get("runs") or 0) + 1,
                                                          "files": list(d.get("files") or ())}),)
    if result == "failed":
        return moved(it, "left", f"left for you — the rebase failed: {d.get('error') or '?'}"), ()
    return it, ()


# ---- the side path --------------------------------------------------------------------------

def resolved(it: ItemState, s: Settings, d: Dict[str, Any]) -> Moved:
    """A resolve run ended (resolve._conclude_resolve, _resolved, _failed)."""
    if d.get("calls") and int(d.get("wakes") or 0) < workstep.MAX_WAITS:
        return moved(it, "waiting on calls", f"waiting on calls — {d.get('cards') or d['calls']}"), ()
    if d.get("ended", "done") != "done":
        return leave(it, s, d, f"the resolve run ended {d['ended']}: {d.get('error') or ''}".strip())
    if d.get("person"):                               # the resolver asked for one: no fixer
        why = d.get("why") or "the resolver asked for a person"
        new = moved(it, "left", f"left for you — {why}")
        return new, (workstep.needs_you(new, f"left for you: {why}", f"eki self show {it.id}"),)
    outcome = d.get("outcome")
    files = ", ".join(d.get("files") or ()) or "?"
    if outcome == "resolved":
        return requeue(it, f"resolved onto {d.get('on') or 'its head'}; back in the queue — gate 2 judges it")
    if outcome == "stopped":
        if int(d.get("runs") or 0) >= workstep.MAX_RESOLVES:
            return leave(it, s, d, f"still conflicted after {workstep.MAX_RESOLVES} resolve runs: {files}")
        return _resolve(it, f"stopped again on {files}; resolving", d)
    if outcome == "bad":
        what = d.get("why") or "no ITEM line"
        if d.get("retried"):
            return leave(it, s, d, f"{what} after a second resolve run")
        return _resolve(it, f"resolver {d.get('who') or 'gave no ITEM line'}; second try", d, retry=what)
    return leave(it, s, d, d.get("why") or "the resolve went wrong")


def _resolve(it: ItemState, why: str, d: Dict[str, Any], retry: Optional[str] = None) -> Moved:
    new = moved(it, "resolving", why)
    data: Dict[str, Any] = {"role": "resolve", "stage": "resolving", "tries": it.tries,
                            "runs": int(d.get("runs") or 0) + 1, "files": list(d.get("files") or ())}
    if retry:
        data["retry"] = retry
    return new, (Action("start_run", it.id, why, data),)


def leave(it: ItemState, s: Settings, d: Dict[str, Any], why: str) -> Moved:
    """Left for a person, unless it gets its one fixer run first (resolve._leave)."""
    if workstep.can_fix(it, s, d):
        return workstep.fixing(it, "resolve", {**d, "tail": why})
    return moved(it, "left", f"left for you — {why}"), ()


# ---- gate 2 ---------------------------------------------------------------------------------

def bisect(results: List[Optional[str]]) -> Tuple[int, Optional[str], int, int]:
    """(the last green, what next, lo, top) for the stack's gate-2 results, front first
    (queue._judge). What next: None (wait for the running check or all green), 'start'
    (check stack[lo..top] as one batch), 'waits' (red at lo with greens under it: they land
    first) or 'bad' (red at the front: the bad one)."""
    green = max((i for i, r in enumerate(results) if r == "green"), default=-1)
    lo = green + 1
    if "running" in results or lo >= len(results):
        return green, None, lo, lo
    red = next((i for i in range(lo, len(results)) if results[i] == "red"), None)
    if red is None:
        return green, "start", lo, len(results) - 1
    if red == lo:
        return green, ("waits" if lo else "bad"), lo, lo
    return green, "start", lo, (lo + red - 1) // 2


def stack(items: Tuple[ItemState, ...], s: Settings, d: Dict[str, Any]) -> Tuple[Tuple[ItemState, ...],
                                                                                    Tuple[Action, ...]]:
    """Gate 2 on the stack as a batch: green clears an item and all under it; red bisects.
    A red one at lo waits for the greens under it to land, so its fixer starts on the main
    they made — eki's rule, and a project's too (docs/work-model.md, difference c)."""
    entries = [e for e in d.get("stack") or () if isinstance(e, dict) and e.get("id")]
    if "batch" not in s.gates or not entries:
        return items, ()
    ids = [e["id"] for e in entries]
    green, nxt, lo, top = bisect([e.get("result") for e in entries])
    mine = {x.id: x for x in items}
    out: Dict[str, ItemState] = {}
    actions: List[Action] = []
    for i in range(green + 1):
        it = mine.get(ids[i])
        if it is not None and it.stage in ("queued", "gate 2") and it.gate2 != "green":
            note = "" if i == green else f" (in the batch checked on {ids[green]})"
            out[it.id] = moved(it, "queued", f"gate 2 green{note}", gate2="green")
            actions.append(Action("land", it.id, out[it.id].why))
    if nxt == "start" and ids[top] in mine:
        batch = ids[lo:top + 1]
        what = f"item {ids[top]}" if len(batch) == 1 else f"a batch of {len(batch)} ({batch[0]}..{ids[top]})"
        why = f"gate 2 on {what}"
        for iid in batch:
            if iid in mine and mine[iid].stage in ("queued", "gate 2"):
                out[iid] = moved(mine[iid], "gate 2", why)
        actions.append(Action("run_gate", ids[top], why, {"gate": 2, "batch": batch}))
    elif nxt == "waits" and ids[lo] in mine:
        it = mine[ids[lo]]
        out[it.id] = moved(it, "queued", "gate 2 red; waits for the green items under it to land")
    elif nxt == "bad" and ids[lo] in mine:
        it = mine[ids[lo]]
        tails = d.get("tails") or {}
        if workstep.can_fix(it, s, d):
            new, more = workstep.fixing(it, "gate2", {"tail": tails.get(it.id, "")})
            out[it.id] = new
            actions += more
        else:
            out[it.id] = moved(it, "unfit", "unfit — gate 2 failed", gate2="red")
    return tuple(out.get(x.id, x) for x in items), tuple(actions)


# ---- landing --------------------------------------------------------------------------------

def land(it: ItemState, s: Settings, d: Dict[str, Any]) -> Moved:
    """In the target: eki's lands as `landed` and the train makes it live; a project's is
    `applied` straight away, on its branch (docs/work-model.md, difference a)."""
    sha = (d.get("sha") or "")[:12]
    if s.kind == "eki":
        return moved(it, "landed", f"landed ({sha})" if sha else "landed", gate2="green"), ()
    branch = d.get("branch") or "its branch"
    return moved(it, "applied", f"landed on {branch} ({sha})" if sha else f"landed on {branch}",
                 gate2="green"), ()


def landed(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Any) -> Moved:
    """The queue moved the target (queue._land, projectland.land), or the train says how a
    landed item's build went (train.settle)."""
    d = ev.data
    went = d.get("went", "target")
    if went == "target" and it.stage in ("queued", "gate 2"):
        if d.get("waits"):
            why = f"waits: {d['waits']}"
            return (it, ()) if it.why == why else (moved(it, it.stage, why), ())
        return land(it, s, d)
    if went in ("live", "rolled back") and it.stage == "landed":
        return moved(it, went, d.get("why") or went), ()
    return it, ()


# ---- locks ----------------------------------------------------------------------------------

def locked_in(files: Tuple[str, ...], locked: Tuple[str, ...]) -> List[str]:
    """The locked paths among `files` (a glob counts when it matches one), sorted (locks.locked_in)."""
    pats = [f[2:] if f.startswith("./") else f for f in files]
    return sorted(p for p in locked if any(p == f or match(p, f) for f in pats))


def match(name: str, pat: str) -> bool:
    """fnmatch's rules (*, ?, [seq], [!seq]; * crosses '/'), without importing it."""
    if not pat:
        return not name
    c = pat[0]
    if c == "*":
        return any(match(name[k:], pat[1:]) for k in range(len(name) + 1))
    if not name:
        return False
    if c == "?":
        return match(name[1:], pat[1:])
    if c == "[":
        end = pat.find("]", 2 if pat[1:2] in ("!", "]") else 1)
        if end > 0:
            body = pat[1:end]
            neg = body.startswith("!")
            return _in(name[0], body[1:] if neg else body) != neg and match(name[1:], pat[end + 1:])
    return c == name[0] and match(name[1:], pat[1:])


def _in(ch: str, body: str) -> bool:
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            if body[i] <= ch <= body[i + 2]:
                return True
            i += 3
        else:
            if body[i] == ch:
                return True
            i += 1
    return False
