# SPDX-License-Identifier: Apache-2.0
"""What applying a change really is, shown (docs/self-build.md, "Watching it
go in").

On 2026-09-24 several changes could all say "applying" at once, and the only
way to tell what each was doing was to read ~/.eki/self/swap.log. Between a
change finishing and it running there are many stages — its turn in the merge
queue, a rebase, conflicts fixed by an agent, the check again on top, the
build boarding the release train, the go-live, the watch — and each is now
said by name, with what it waits on:

    in line behind self/a, self/b     not its turn yet
    rebasing · checking again · building
    fixing conflicts in x.py (run r, 4 min)
    landed, goes live at 21:45 with 2 others
    going live · watching (90 s left) · live · rolled back (why)

A stage is in progress only while its worker or step is live in this engine;
one a restart cut off says so — "waiting to be resumed" — rather than spin.

Every step's outcome was already written down (eki/steps.py, the merge queue,
the train, swap.json), but not *when* a change passed each point. That is this
journal, ~/.eki/self/pipeline.jsonl — one line per point a change passed:

    queued · turn · rebasing · rebased · conflicts · fixed · checking ·
    rechecked · landed · stopped · leaving · live · rolled back

from which each change's short timeline is read:
queued → rebased → conflicts fixed → rechecked → landed → live.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import steps

HOME = Path("~/.eki/self").expanduser()
#: the supervisor's watch after a swap (builds.swap's default)
WATCH = 180
#: when this engine started: a swap's watch is counted from here
STARTED = time.time()
#: a change that went live or was rolled back stays in view this long —
#: apart from what is still on its way in, under "Went in"
SETTLED_SHOWN = 3600
#: the stages a change has finished at: it's no longer "going in"
SETTLED = ("live", "rolled back")
#: a person's apply (no merge queue) seen mid-stage this recently still counts
STALE = 3600
#: the journal is trimmed to this many lines, oldest first
KEEP_LINES = 4000

#: the points of a change's timeline, and what each is called there
MILESTONES = {"queued": "queued", "rebased": "rebased", "conflicts": "conflicts",
              "fixed": "conflicts fixed", "rechecked": "rechecked", "landed": "landed",
              "leaving": "left to go live", "live": "live", "rolled back": "rolled back",
              "stopped": "stopped"}
#: what a change is doing right after passing a point, while its turn lasts
DOING = {"turn": "starting its turn", "queued": "starting its turn", "rebasing": "rebasing",
         "rebased": "checking again", "checking": "checking again", "rechecked": "building",
         "fixed": "building"}
#: the short name of each stage, for a badge
LABEL = {"in line": "In line", "rebasing": "Rebasing", "starting its turn": "Starting",
         "checking again": "Checking again", "building": "Building", "fixing": "Fixing conflicts",
         "conflicts": "Conflicts", "landed": "Landed", "going live": "Going live",
         "watching": "Watching", "live": "Live", "rolled back": "Rolled back"}

_lock = threading.Lock()


# ---- a title for a list -------------------------------------------------------------------

#: a title in a list is at most this long; the whole of it opens in place
TITLE_MAX = 90


def short_title(text: str, n: int = TITLE_MAX) -> str:
    """One line for a list, never cut mid-word: the whole text when it fits,
    else its first sentence (or the part before a "Topic: …" colon) when
    that fits and says something, else as many whole words as fit and "…"."""
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    m = re.match(r"(.{20,%d}?)[.!?:;](?=\s)" % (n - 1), t)
    if m:
        return m.group(1).rstrip(" ,—-")
    head = t[:n - 1]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;:—-(") + "…"


def full_title(text: str) -> str:
    """The first paragraph of a request, whole: what a short title opens to."""
    t = str(text or "").strip().split("\n\n")[0]
    return " ".join(t.split())


# ---- the journal ------------------------------------------------------------------------

def _path(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "pipeline.jsonl"


def journal(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    try:
        text = _path(home).read_text()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("change") and e.get("event"):
            out.append(e)
    return out


def mark(cid: str, event: str, home: Optional[Path] = None, **fields: Any) -> Dict[str, Any]:
    """The change passed a point. Passing the same point again straight away
    (a restart doing a step again) isn't written twice. Never raises: the
    journal only shows the work, it never stops it."""
    if not cid:
        return {}
    e = {"change": cid, "event": event, "at": int(time.time()), "boot": steps.BOOT,
         **{k: v for k, v in fields.items() if v not in (None, "", [], {})}}
    try:
        with _lock:
            mine = events(cid, home)
            if mine and mine[-1]["event"] == event:
                return mine[-1]
            path = _path(home)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            _trim(path)
    except OSError:
        pass
    return e


def _trim(path: Path) -> None:
    lines = path.read_text().splitlines()
    if len(lines) > KEEP_LINES:
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-KEEP_LINES // 2:]) + "\n")
        tmp.replace(path)


def events(cid: str, home: Optional[Path] = None, all_: Optional[List[Dict[str, Any]]] = None
           ) -> List[Dict[str, Any]]:
    """The change's points since it last went in line (an earlier go that
    was rolled back is history)."""
    mine = [e for e in (journal(home) if all_ is None else all_) if e["change"] == cid]
    starts = [n for n, e in enumerate(mine) if e["event"] == "queued"]
    return mine[starts[-1]:] if starts else mine


def timeline(cid: str, *, evs: Optional[List[Dict[str, Any]]] = None, queued_at: int = 0,
             landed_at: int = 0, live_at: int = 0, home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """[{"step", "at", "note"}] — each point the change passed, in order.
    `queued_at` / `landed_at` / `live_at` fill in what the merge queue, the
    train and the change's own record knew about it from before the journal
    (or from a path that didn't write to it): asked to apply, landed alone,
    applied."""
    evs = events(cid, home) if evs is None else evs
    out: List[Dict[str, Any]] = []
    for e in evs:
        step = MILESTONES.get(e["event"])
        if step is None:
            continue
        note = ""
        if e["event"] == "conflicts":
            note = ", ".join(e.get("files") or []) or str(e.get("why") or "")
        elif e["event"] == "rechecked" and e.get("fit") is False:
            step, note = "rechecked ✗", "didn't pass on top of your checkout"
        elif e["event"] in ("rolled back", "stopped"):
            note = str(e.get("why") or "")
        elif e["event"] == "leaving" and e.get("with"):
            note = "with " + ", ".join("self/" + x for x in e["with"])
        out.append({"step": step, "at": int(e.get("at") or 0), "note": note[:200]})
    if queued_at and not any(r["step"] == "queued" for r in out):
        out.insert(0, {"step": "queued", "at": int(queued_at), "note": ""})
    if landed_at and not any(r["step"] == "landed" for r in out):
        out.append({"step": "landed", "at": int(landed_at), "note": ""})
    if live_at and not any(r["step"] in ("live", "rolled back") for r in out):
        out.append({"step": "live", "at": int(live_at), "note": ""})
    out.sort(key=lambda r: r["at"])                     # stable: same-second points keep their order
    return out


def partial(tl: List[Dict[str, Any]]) -> bool:
    """A change that went live but whose way in wasn't written down whole —
    applied before the journal, or by a path that didn't mark its steps."""
    steps_ = {r["step"] for r in tl}
    return "live" in steps_ and not ({"queued", "landed"} <= steps_)


# ---- the stage ----------------------------------------------------------------------------

def _hm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _mins(since: float, now: float) -> str:
    return f"{max(1, int((now - since) // 60))} min" if since else ""


def _stage(key: str, text: str, live: bool = False, stalled: bool = False, **more: Any) -> Dict[str, Any]:
    """{"stage", "label", "text", "live", "stalled"} — live: its worker or
    step is going now (a spinner); stalled: it was, and a restart cut it off."""
    if stalled:
        text += " — waiting to be resumed"
    return {"stage": key, "label": LABEL.get(key, key), "text": text, "live": live, "stalled": stalled, **more}


def stage(c: Dict[str, Any], *, evs: List[Dict[str, Any]], queue: List[Dict[str, Any]],
          running: Iterable[str], train: Dict[str, Any], leaves_at: Optional[float],
          swap: Dict[str, Any], swap_alive: bool, running_build: str,
          now: Optional[float] = None, boot: str = "") -> Dict[str, Any]:
    """Where a change stands between finishing and running, and what it
    waits on — {} for one that isn't on its way in (proposed, unfit, …).

    `queue` the merge queue (selfloop.merge_queue), `running` the runs live
    in this engine, `train` builds.train(), `leaves_at` when the train leaves,
    `swap` builds.last_swap() with `swap_alive` its supervisor still there,
    `running_build` the build this engine runs."""
    now = now or time.time()
    running = set(running)
    cid, state = c["id"], c.get("state") or ""
    last = evs[-1] if evs else {}

    # rolled back / live: said by the change, or by a go-live not yet read
    departed = train.get("departed") or {}
    gone_with = cid in (departed.get("cars") or [])
    gone_swap = gone_with and swap.get("target") == departed.get("build")
    if state == "rolled back" or (state == "applying" and gone_swap and swap.get("state") in ("rolled back", "failed")):
        why = c.get("why") if state == "rolled back" else swap.get("why")
        return _stage("rolled back", f"rolled back ({why or 'it wasn’t healthy'})")
    if state == "applied" or (state == "applying" and gone_swap and swap.get("state") == "healthy"):
        return _stage("live", "live")

    # its conflicts being fixed, by a run of its own
    if state == "conflicts" and c.get("resolving"):
        files = (c.get("rebase") or {}).get("files") or next(
            (e.get("files") for e in reversed(evs) if e["event"] == "conflicts"), []) or []
        where = f" in {', '.join(files[:4])}{' …' if len(files) > 4 else ''}" if files else ""
        live = c["resolving"] in running
        took = _mins(float(c.get("resolving_at") or 0), now)
        return _stage("fixing", f"fixing conflicts{where} (run {c['resolving']}{', ' + took if took else ''})",
                      live=live, stalled=not live, run=c["resolving"])

    # its turn in the merge queue, or in line for it
    mine = next((r for r in queue if r.get("change") == cid), None)
    if mine is not None and state not in ("applying", "applied"):
        live = mine.get("run") in running
        if mine.get("applying"):
            doing = DOING.get(last.get("event") or "", "starting its turn")
            return _stage(_key(doing), doing, live=live, stalled=not live)
        ahead = [r for r in queue[:queue.index(mine)] if r.get("run") in running]
        if not live:
            return _stage("in line", "in line" + (" behind " + ", ".join("self/" + r["change"] for r in ahead)
                                                  if ahead else ""), stalled=True)
        return _stage("in line", "in line behind " + ", ".join("self/" + r["change"] for r in ahead)
                      if ahead else "in line — next")

    # a person's own apply: no queue, just the journal (and only this engine's)
    if state in ("proposed", "conflicts", "rolled back") and last.get("event") in DOING \
            and now - float(last.get("at") or 0) < STALE:
        doing = DOING[last["event"]]
        live = last.get("boot") == boot and bool(boot)
        return _stage(_key(doing), doing, live=live, stalled=not live)

    if state != "applying":
        return {}
    # landed: aboard the train, or gone to go live
    cars = [x.get("self") for x in train.get("cars") or []]
    if cid in cars:
        others = len([x for x in cars if x and x != cid])
        at = f"at {_hm(leaves_at)}" if leaves_at is not None and leaves_at > now else "now"
        return _stage("landed", f"landed, goes live {at}" + (f" with {others} other{'s' if others != 1 else ''}"
                                                            if others else ""),
                      leaves_at=int(leaves_at) if leaves_at is not None else None)
    if gone_swap or (swap.get("self") == cid and swap.get("target") == c.get("build")):
        return go_live_stage(swap, swap_alive, running_build, now)
    return _stage("landed", "landed, waiting for its go-live", stalled=True)


def _key(doing: str) -> str:
    return {"rebasing": "rebasing", "checking again": "checking again", "building": "building"}.get(
        doing, "starting its turn")


def go_live_stage(swap: Dict[str, Any], swap_alive: bool, running_build: str,
                  now: Optional[float] = None) -> Dict[str, Any]:
    """Where a go-live stands: the supervisor waiting for a quiet moment,
    swapping, watching the new engine — or how it came out."""
    now = now or time.time()
    st = swap.get("state") or ""
    if st == "healthy":
        return _stage("live", "live")
    if st in ("rolled back", "failed"):
        return _stage("rolled back", f"rolled back ({swap.get('why') or 'it wasn’t healthy'})")
    if st not in ("waiting", "swapping"):
        return _stage("going live", "going live", stalled=True)
    if not swap_alive:
        return _stage("going live", "going live", stalled=True)
    if st == "waiting":
        left = int(swap.get("deadline") or 0) - now
        return _stage("going live", "going live" + (f" by {_hm(float(swap['deadline']))} (at once if nothing runs)"
                                                    if left > 60 else ""), live=True)
    target = Path(str(swap.get("target") or "")).name
    if target and target == running_build:
        left = int(max(float(swap.get("at") or 0), STARTED) + WATCH - now)
        return _stage("watching", f"watching ({max(0, left)} s left)", live=True, left=max(0, left))
    return _stage("going live", "going live — restarting on the new build", live=True)


# ---- the view -----------------------------------------------------------------------------

def view(rows: List[Dict[str, Any]], *, queue: List[Dict[str, Any]], running: Iterable[str],
         train: Dict[str, Any], leaves_at: Optional[float], swap: Dict[str, Any], swap_alive: bool,
         running_build: str, boot: str = "", now: Optional[float] = None,
         home: Optional[Path] = None) -> Dict[str, Any]:
    """{"changes": [{"id", "title", "stage", "label", "text", "live", "stalled",
    "timeline"}], "golive": {"next", "last"}} — every change on its way in (or
    in in the last hour), and the release train as one group: the next go-live,
    and the last one with how it came out."""
    now = now or time.time()
    running = set(running)
    all_ = journal(home)
    by_id = {c["id"]: c for c in rows}
    queued_at = {r["change"]: int(r.get("at") or 0) for r in queue}
    landed_at = {x.get("self"): int(x.get("at") or 0) for x in train.get("cars") or []}
    out = []
    for c in rows:
        evs = events(c["id"], all_=all_)
        st = stage(c, evs=evs, queue=queue, running=running, train=train, leaves_at=leaves_at,
                   swap=swap, swap_alive=swap_alive, running_build=running_build, now=now, boot=boot)
        if not st:
            continue
        if st["stage"] in ("live", "rolled back") and now - float(c.get("state_at") or 0) > SETTLED_SHOWN \
                and c.get("state") != "applying":
            continue
        tl = timeline(c["id"], evs=evs,
                      queued_at=queued_at.get(c["id"]) or int(c.get("asked_at") or 0),
                      landed_at=landed_at.get(c["id"]) or int(c.get("alone_at") or 0),
                      live_at=int(c.get("state_at") or c.get("at") or 0) if c.get("state") == "applied" else 0)
        row = {"id": c["id"], "title": c.get("title") or "", **st, "timeline": tl,
               "settled": st["stage"] in SETTLED}
        if c.get("title_full"):
            row["title_full"] = c["title_full"]
        if partial(tl):
            row["partial"] = True
        out.append(row)
    order = {r["change"]: n for n, r in enumerate(queue)}
    # still on its way first (nearest to live first), then what went in, newest first
    rank = {"watching": 1, "going live": 1, "landed": 2}
    out.sort(key=lambda r: (1, -int((r["timeline"] or [{}])[-1].get("at") or 0)) if r["settled"]
             else (0, rank.get(r["stage"], 3), order.get(r["id"], -1)))
    return {"changes": out, "golive": golive(by_id, train=train, leaves_at=leaves_at, swap=swap,
                                            swap_alive=swap_alive, running_build=running_build, now=now)}


def golive(by_id: Dict[str, Dict[str, Any]], *, train: Dict[str, Any], leaves_at: Optional[float],
           swap: Dict[str, Any], swap_alive: bool, running_build: str,
           now: Optional[float] = None) -> Dict[str, Any]:
    """The release train as one group. "next": what the next go-live
    carries and when it leaves; "last": the one that left last — what it
    carried, when, and where it stands or how it came out."""
    now = now or time.time()

    def carried(ids: Iterable[str]) -> List[Dict[str, str]]:
        return [{"id": x, "title": (by_id.get(x) or {}).get("title") or ""} for x in ids if x]

    out: Dict[str, Any] = {}
    cars = [x.get("self") or "" for x in train.get("cars") or []]
    if cars:
        out["next"] = {"carrying": carried(cars), "leaves_at": int(leaves_at) if leaves_at is not None else None,
                       "in": max(0, int(leaves_at - now)) if leaves_at is not None else None}
    gone = train.get("departed") or {}
    if gone.get("build"):
        mine = swap.get("target") == gone["build"]
        st = go_live_stage(swap if mine else {}, swap_alive and mine, running_build, now)
        if not mine:                        # superseded, or its outcome no longer on file
            st = _stage("live", "went live with a later one") if swap.get("state") == "healthy" else st
        out["last"] = {"carrying": carried(gone.get("cars") or []), "left_at": int(gone.get("at") or 0),
                       "build": Path(gone["build"]).name, **st,
                       "settled_at": int(swap.get("at") or 0) if mine and st["stage"] in ("live", "rolled back") else None}
    return out


def lines(v: Dict[str, Any], now: Optional[float] = None) -> List[str]:
    """The pipeline in words, for `eki self` and `eki self --watch`."""
    now = now or time.time()
    out: List[str] = []
    g = v.get("golive") or {}
    nxt, last = g.get("next") or {}, g.get("last") or {}
    if nxt:
        when = f"at {_hm(nxt['leaves_at'])}" if nxt.get("in") else "now"
        out.append(f"next go-live {when}, carrying " + ", ".join(f"self/{c['id']}" for c in nxt["carrying"])
                   + "   (go now: eki self release)")
    if last and (last.get("stage") not in ("live", "rolled back") or now - float(last.get("settled_at") or 0) < SETTLED_SHOWN):
        out.append(f"last go-live left {_hm(last['left_at'])}, carrying "
                   + ", ".join(f"self/{c['id']}" for c in last["carrying"]) + f" — {last['text']}")
    rows = v.get("changes") or []
    going = [r for r in rows if not _settled(r)]
    went = [r for r in rows if _settled(r)]
    for head, group in (("going in", going), ("went in, last hour", went)):
        if not group:
            continue
        out.append("")
        out.append(f"{head} · {len(group)}:")
        for r in group:
            mark_ = "▸ " if r.get("live") else "  "
            out.append(f"{mark_}self/{r['id']}  {r['title'][:60]} — {r['text']}")
            if r.get("timeline"):
                out.append("      " + short(r["timeline"])
                           + ("   (earlier steps weren't recorded)" if r.get("partial") else ""))
    return out


def _settled(r: Dict[str, Any]) -> bool:
    return bool(r.get("settled")) if "settled" in r else r.get("stage") in SETTLED


def short(tl: List[Dict[str, Any]]) -> str:
    """queued 21:02 → rebased 21:05 → landed 21:10"""
    return " → ".join(f"{r['step']} {_hm(r['at'])}" for r in tl)
