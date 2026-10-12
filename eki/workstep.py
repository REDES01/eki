"""What happens next to a goal and its items: the one place the rules live
(docs/work-model.md).

`step(goal, items, settings, event)` is pure — no files, git, runs, clock or
database. Everything it needs to know of the world comes in the event (the
executor looks first: did the run end done, was anything committed, is the
commit in the target); everything it wants done goes out as actions, each
with the words the board prints. eki's own work and a project's differ only
in their Settings, never here. The queue's stages (admit, gate 2 and its
bisect, landing) are in eki/workstep_queue.py; `step` hands them over.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional, Tuple

from . import workstep_queue as queue
from .workmodel import (EVENT_KINDS, FAILURE_KINDS, Action, Event, GoalState, ItemState, Settings, goal as
                        _goal, item as _item, moved)

#: wakes per build try while its card calls are out (eki/callwait.py)
MAX_WAITS = 4
#: a dependency is fit for its dependents once its code is in main, whoever put it there
FIT = ("landed", "live", "applied")
#: an item in one of these has a run going: a person's retry waits
LIVE = ("building", "judging", "reviewing", "waiting on calls", "fixing")
#: resolve runs per item, in all, before it is left for a person (eki/resolve.py)
MAX_RESOLVES = 3
NOTHING = "the agent changed nothing"
NOT_JUDGED = "no check configured — not judged"
NO_REVIEW = "not in this project's workflow"
#: the gates that make gate 1 a run (a card with review only runs one that says so)
GATE1 = ("check", "playtester", "review")
#: the run a working or checking stage waits on, as (action kind, role or gate)
RUN_OF = {"building": ("start_run", "build"), "fixing": ("start_run", "fix"),
          "resolving": ("start_run", "resolve"), "judging": ("run_gate", 1),
          "reviewing": ("start_run", "review"), "rechecking": ("run_gate", 1)}

Out = Tuple[GoalState, Tuple[ItemState, ...], Tuple[Action, ...]]
Moved = Tuple[ItemState, Tuple[Action, ...]]


class Refused(ValueError):
    """A person's decision the item's stage doesn't allow; nothing changes."""


def step(goal: GoalState, items: Tuple[ItemState, ...], settings: Settings, event: Event) -> Out:
    if event.kind not in EVENT_KINDS:
        raise ValueError(f"no event kind {event.kind!r}")
    d = event.data
    if event.kind in ("goal_submitted", "plan_ready") or (event.kind == "person_decided" and not d.get("item")):
        return _GOAL[event.kind](goal, items, settings, event)
    actions: Tuple[Action, ...] = ()
    it = next((x for x in items if x.id == d.get("item")), None)
    if it is not None:
        handler = _ITEM.get(event.kind) or getattr(queue, _QUEUE.get(event.kind, ""), None)
        if handler is not None:
            new, actions = handler(it, items, settings, event)
            items = tuple(new if x.id == it.id else x for x in items)
    if "stack" in d and event.kind in ("merge_result", "check_verdict"):
        items, more = queue.stack(items, settings, d)
        actions += more
    return goal, items, actions


# ---- goals ------------------------------------------------------------------------------------

def _submitted(goal: GoalState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Out:
    d = ev.data
    if d.get("plan", True) and s.plan == "on":
        role = "draft" if d.get("draft") else "plan"
        stage = "drafting" if role == "draft" else "planning"
        return _goal(stage, stage), items, (Action("start_run", None, stage, {"role": role}),)
    one = _item(d["item"], files=tuple(d.get("files") or ()))
    return _goal("planned", "one item as it stands"), items + (one,), ()


def _plan_ready(goal: GoalState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Out:
    """A draft or plan run ended (selfdraft.conclude, selfplan.conclude_plan)."""
    d = ev.data
    ended, of = d.get("ended", "done"), d.get("of", "plan")
    if goal.stage != ("drafting" if of == "draft" else "planning"):
        return goal, items, ()
    if ended != "done":
        return _goal("failed", f"the {of} run ended {ended}: {d.get('error') or ''}".strip()), items, ()
    if of == "draft":
        if d.get("left"):
            return _goal("left", f"left — {d['left']}"), items, ()
        if d.get("error"):
            return _goal("failed", d["error"]), items, ()
        return _goal("planning", "drafted → planning"), items, (Action("start_run", None, "planning",
                                                                       {"role": "plan"}),)
    if d.get("error"):
        return _goal("failed", d["error"]), items, ()
    planned = tuple(_item(p["id"], deps=tuple(p.get("deps") or ()), files=tuple(p.get("files") or ()))
                    for p in d.get("items") or ())
    why = (f"{len(planned)} item(s) planned" if planned
           else f"nothing worth doing now — {d.get('why') or 'the plan found nothing worth doing now'}")
    return _goal("planned", why), items + planned, ()


def _goal_decided(goal: GoalState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Out:
    """A person runs a failed or left goal again (selfdraft.retry)."""
    if ev.data.get("decision") != "retry" or goal.stage not in ("failed", "left"):
        raise Refused(f"goal is {goal.stage}")
    role = "plan" if ev.data.get("plan", True) else "draft"
    stage = "planning" if role == "plan" else "drafting"
    return _goal(stage, stage), items, (Action("start_run", None, stage, {"role": role}),)


_GOAL = {"goal_submitted": _submitted, "plan_ready": _plan_ready, "person_decided": _goal_decided}


# ---- starting -------------------------------------------------------------------------------

def blocked_by(items: Tuple[ItemState, ...], it: ItemState) -> Optional[str]:
    """The first dependency of `it` not yet fit (selfwork.FIT), or None: it may start."""
    done = {x.id for x in items if x.stage in FIT}
    return next((dep for dep in it.deps if dep not in done), None)


def _run(it: ItemState, why: str, again: bool = False) -> Tuple[Action, ...]:
    """The run the item's stage waits on, started (again: the same try)."""
    kind, what = RUN_OF[it.stage]
    data: Dict[str, Any] = ({"role": what, "stage": it.stage, "tries": it.tries} if kind == "start_run"
                            else {"gate": what, "tries": it.tries})
    if again:
        data["again"] = True
    return (Action(kind, it.id, why, data),)


def _started(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Moved:
    """The scheduler has room for it (scheduler.start_ready), or its calls are back (callwait.tick)."""
    if it.hold_until is not None and ev.at < it.hold_until:
        return it, ()
    if it.stage == "waiting":
        dep = blocked_by(items, it)
        if dep is not None:
            return replace(it, why=f"after {dep}"), ()
        new = moved(it, "building", "building", tries=it.tries + 1, hold_until=None)
        return new, _run(new, "building")
    if it.stage == "waiting on calls":
        stage = ev.data.get("stage") if ev.data.get("stage") in ("building", "resolving") else None
        if stage is None:
            return moved(it, "left", "left — waiting on calls, but what it waits for can't be read"), ()
        new = moved(it, stage, f"calls {ev.data.get('why') or 'all back'}; woke the "
                               f"{'build' if stage == 'building' else 'resolve'}")
        return new, _run(new, new.why, again=True)
    if it.hold_until is not None and it.stage in RUN_OF:       # held, and now it may go on
        new = replace(it, hold_until=None, why="the hold is over; started again")
        return new, _run(new, new.why, again=True)
    return it, ()


# ---- a run ended ----------------------------------------------------------------------------

def _finished(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Moved:
    d = ev.data
    if it.stage == "building":
        if d.get("calls") and int(d.get("wakes") or 0) < MAX_WAITS:
            return moved(it, "waiting on calls", f"waiting on calls — {d.get('cards') or d['calls']}"), ()
        if d.get("ended", "done") != "done":
            return moved(it, "left", f"left — the build run ended {d['ended']}"), ()
        return built(it, s, d)
    if it.stage == "fixing":
        if d.get("ended", "done") != "done":
            return moved(it, "left", f"left for you — the fixer run ended {d['ended']}"), ()
        if d.get("leave"):
            return moved(it, "left", f"left for you — {d['leave']}"), ()
        new, actions = built(it, s, d)
        return replace(new, why=f"{new.why} (after the fixer)"), actions
    if it.stage == "resolving":
        return queue.resolved(it, s, d)
    if it.stage == "rechecking":            # an older build's gate 1 after a resolve: not counted
        return queue.requeue(it, "resolved; back in the queue — gate 2 judges it")
    return it, ()


def built(it: ItemState, s: Settings, d: Dict[str, Any]) -> Moved:
    """A done build or fixer run, its work committed (selfwork._built): left, proposed, or judged."""
    if d.get("commit_error"):
        return moved(it, "left", f"left for you — couldn't commit: {str(d['commit_error'])[:120]}"), ()
    verdict, changed = d.get("verdict"), d.get("changed", True)
    touched = tuple(d.get("touched") or it.touched)
    if verdict == "person" or not changed:
        why = d.get("reason") or (NOTHING if not changed else "")
        if verdict != "person" and d.get("ended_early") and can_fix(it, s, d):
            return fixing(it, "nothing", d)
        new = moved(it, "left", f"left for you — {why}", touched=touched)
        if verdict != "person":             # "changed nothing" is a failed try, not a question
            return new, ()
        return new, (needs_you(new, f"left for you: {why}", f"eki self show {it.id}"),)
    branch = d.get("branch") or "its branch"
    if not any(g in s.gates for g in GATE1):
        said = d.get("said") or NOT_JUDGED
        return moved(it, "proposed", f"built — proposed on {branch} ({said})", touched=touched, gate1=None,
                     review=f"none: {d['no_review']}" if d.get("no_review") else it.review), ()
    reviewing = "review" in s.gates
    new = moved(it, "judging", f"built ({len(touched)} files); judging" + (" and reviewing" if reviewing else ""),
                touched=touched, gate1=None, review=None if reviewing else f"none: {d.get('no_review') or _no_review(s)}")
    actions = _run(new, new.why)
    if reviewing:
        actions += (Action("start_run", it.id, "reviewing", {"role": "review", "stage": "judging",
                                                             "tries": it.tries}),)
    return new, actions


def _no_review(s: Settings) -> str:
    return "self.review is off" if s.kind == "eki" else NO_REVIEW


def needs_you(it: ItemState, what: str, act: str) -> Action:
    """A push to the person's phone, once per item, stage and try (selfwork.needs_you)."""
    return Action("notify", it.id, what, {"key": f"item:{it.id}:{it.stage}:{it.tries}", "kind": "needs_you",
                                          "what": what, "act": act})


def can_fix(it: ItemState, s: Settings, d: Dict[str, Any]) -> bool:
    """One fixer run per item, eki's own only (fixer.offer); the executor says whether it can start one."""
    return s.kind == "eki" and not it.fixer_used and bool(d.get("can_fix", True))


def fixing(it: ItemState, stage: str, d: Dict[str, Any]) -> Moved:
    """The item's one fixer run (fixer.offer): it leaves the queue while the fixer works."""
    new = moved(it, "fixing", f"{stage} failed — the fixer is on it", fixer_used=True, gate2=None)
    return new, (Action("start_run", it.id, new.why, {"role": "fix", "stage": stage, "tries": it.tries,
                                                     "failure": d.get("tail") or d.get("fix") or ""}),)


# ---- judging --------------------------------------------------------------------------------

def _verdict(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Moved:
    """Gate 1 and the review have both ended (selfjudge.conclude, review.conclude)."""
    d = ev.data
    if d.get("gate") == 2 or "stack" in d:
        return it, ()                       # the queue's: eki/workstep_queue.py
    seen = d.get("review", it.review)
    branch = d.get("branch") or "its branch"
    if it.stage == "reviewing":             # a legacy review run, after gate 1
        if seen is None or str(seen).startswith("none:"):
            why = str(seen or "none: ?")[6:]
            return moved(it, "proposed", f"fit — proposed on {branch} (not reviewed: {why})", review=seen), ()
        if seen == "ok":
            return moved(it, "proposed", f"reviewed ok — proposed on {branch}", review="ok"), ()
        return judged(it, seen, branch)
    if it.stage != "judging":
        return it, ()
    if d.get("result") == "green":
        return judged(replace(it, gate1="green"), seen, branch)
    return judgment(replace(it, gate1="red", review=seen), s, d)


def judged(it: ItemState, seen: Optional[str], branch: str) -> Moved:
    """Gate 1 green: the review's word decides (selfjudge.judged)."""
    if not (seen or "").startswith("no:"):
        how = "reviewed ok" if seen == "ok" else f"fit (not reviewed: {(seen or 'none: ?')[6:]})"
        return moved(it, "proposed", f"{how} — proposed on {branch}", review=seen), ()
    why = seen[3:].strip()
    if not it.reviews:
        return moved(it, "waiting", f"the review says no — {why[:120]}; rebuilding once", review=seen,
                     reviews=1), ()
    return moved(it, "proposed", f"the review says no again — proposed; `eki self apply {it.id}` if you agree",
                 review=seen), ()


def judgment(it: ItemState, s: Settings, d: Dict[str, Any]) -> Moved:
    """Gate 1 red: one more build (a review's rebuild is free), then one fixer run, then unfit."""
    ended = d.get("ended", "failed")
    if it.tries - (it.reviews or 0) < 2 and ended == "failed":
        return moved(it, "waiting", "checks failed; one more try"), ()
    if can_fix(it, s, d):
        return fixing(it, "gate1", d)
    return moved(it, "unfit", "unfit — left for you"), ()


# ---- failures -------------------------------------------------------------------------------

def _failed(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Moved:
    """A run failed, its kind said (eki/workmodel.py FAILURE_KINDS)."""
    d = ev.data
    kind = d.get("kind") if d.get("kind") in FAILURE_KINDS else "judgment"
    why = d.get("why") or kind
    if kind == "lost":
        return _lost(it, items, s, ev)
    if kind in ("quota", "transient") and d.get("retry_at") is not None:
        return hold(it, float(d["retry_at"]), kind)
    if kind == "environment":               # a person's hands, never the fixer's
        new = moved(it, "left", f"left for you — {why}")
        return new, (Action("ask_person", it.id, why, {"key": f"item:{it.id}:left:{it.tries}"}),)
    if it.stage == "judging":
        return judgment(it, s, d)
    if it.stage == "resolving":
        return queue.leave(it, s, d, why)
    if it.stage == "rechecking":
        return queue.requeue(it, "resolved; back in the queue — gate 2 judges it")
    if it.stage in ("building", "fixing", "waiting on calls", "queued", "gate 2", "reviewing"):
        return moved(it, "left", f"left for you — {why}"), ()
    return it, ()


def hold(it: ItemState, until: float, kind: str) -> Moved:
    """No run until `until`: a build goes back to waiting, its try not counted; others keep their stage."""
    why = f"{kind}: held until its retry time"
    if it.stage == "building":
        new = moved(it, "waiting", why, tries=max(it.tries - 1, 0), hold_until=until)
    else:
        new = replace(it, hold_until=until, why=why)
    return new, (Action("hold_until", it.id, why, {"until": until}),)


def _lost(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Moved:
    """A run lost to a restart or a lease: the same run again, never a try."""
    if it.stage not in RUN_OF:
        return it, ()
    new = replace(it, why="the run was lost; started again")
    return new, _run(new, new.why, again=True)


# ---- a person -------------------------------------------------------------------------------

def _decided(it: ItemState, items: Tuple[ItemState, ...], s: Settings, ev: Event) -> Moved:
    """`eki self retry / drop / apply` (selfwork.retry, selfwork.drop, queue.apply)."""
    d = ev.data
    what = d.get("decision")
    if what == "drop":
        return moved(it, "dropped", "dropped"), ()
    if what == "retry":
        if it.stage in LIVE:
            raise Refused(f"item {it.id} is {it.stage}")
        fresh: Dict[str, Any] = (dict(tries=0, reviews=0, review=None, gate1=None, gate2=None, touched=())
                                 if d.get("fresh") else {})
        return moved(it, "waiting", "retried by you", fixer_used=False, hold_until=None, **fresh), ()
    if what == "apply":
        if it.stage == "locked" and not d.get("yes"):
            files = ", ".join(queue.locked_in(it.files + it.touched, s.locked)) or "?"
            raise Refused(f"item {it.id} touches locked files: {files}, needs --yes")
        if it.stage not in ("proposed", "locked"):
            raise Refused(f"item {it.id} is {it.stage}, not proposed")
        return queue.enqueue(it, "queued")
    raise Refused(f"no decision {what!r}")


_ITEM = {"item_started": _started, "item_finished": _finished, "check_verdict": _verdict,
         "item_failed": _failed, "run_lost": _lost, "person_decided": _decided}
#: the queue's, looked up when called (eki/workstep_queue.py imports this module)
_QUEUE = {"merge_result": "merged", "seen": "seen", "landed": "landed"}
