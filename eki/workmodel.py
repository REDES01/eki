"""The nouns of the work model (docs/work-model.md): phases, states, settings,
events and actions. Nothing here does anything; eki/workstep.py decides,
eki/workrun.py saves and carries out.

An item's `stage` is finer than its `phase`: it is today's `items.state`
word (plus 'gate 2', which the board still calls 'queued'), so `label`
gives back exactly what `eki self`, the board, the digest and notify read.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

GOAL_PHASES = ("shaping", "planned", "failed")
ITEM_PHASES = ("waiting", "working", "checking", "ready", "merging", "done", "stopped")

#: goal stage (today's goals.state) -> its phase
GOAL_STAGES = {"drafting": "shaping", "planning": "shaping", "planned": "planned",
               "accepting": "planned", "accepted": "planned", "judgment": "planned",   # final acceptance (eki/acceptance.py)
               "failed": "failed", "left": "failed"}
#: item stage -> its phase; every stage but 'gate 2' is today's items.state word
ITEM_STAGES = {
    "waiting": "waiting",
    "building": "working", "fixing": "working", "resolving": "working", "waiting on calls": "working",
    "judging": "checking", "reviewing": "checking", "gate 2": "checking", "rechecking": "checking",
    "proposed": "ready", "locked": "ready",
    "queued": "merging",
    "applied": "done", "landed": "done", "live": "done",
    "left": "stopped", "unfit": "stopped", "dropped": "stopped", "rolled back": "stopped",
}

PLANS = ("on", "off", "single")
GATES = ("check", "review", "playtester", "batch")
MERGES = ("auto", "person")
KINDS = ("eki", "local", "github")

EVENT_KINDS = ("goal_submitted", "plan_ready", "item_started", "item_finished", "check_verdict",
               "item_failed", "run_lost", "merge_result", "seen", "landed", "person_decided")
ACTION_KINDS = ("start_run", "run_gate", "merge", "land", "notify", "hold_until", "ask_person")
ROLES = ("build", "fix", "plan", "draft", "review", "resolve")
FAILURE_KINDS = ("quota", "transient", "environment", "judgment", "lost")
DECISIONS = ("retry", "drop", "apply")


@dataclass(frozen=True)
class GoalState:
    phase: str
    stage: str
    why: str = ""


@dataclass(frozen=True)
class ItemState:
    id: str
    phase: str = "waiting"
    stage: str = "waiting"
    deps: Tuple[str, ...] = ()
    files: Tuple[str, ...] = ()        # the declared write-set (globs)
    touched: Tuple[str, ...] = ()      # the files really changed
    tries: int = 0                     # builds started
    reviews: int = 0                   # rebuilds a review's "no" asked for
    fixer_used: bool = False
    gate1: Optional[str] = None        # green | red | None
    gate2: Optional[str] = None        # green | red | None
    review: Optional[str] = None       # ok | no: <why> | none: <why>
    hold_until: Optional[float] = None
    why: str = ""


@dataclass(frozen=True)
class Settings:
    plan: str = "on"                   # on | off | single
    gates: Tuple[str, ...] = ("check", "review", "batch")
    merge: str = "person"              # auto | person
    parallel: int = 4                  # 0: no cap of its own
    locked: Tuple[str, ...] = ()       # paths an item may not change on eki's own say
    kind: str = "eki"                  # eki | local | github: where it lands and who gets a fixer


@dataclass(frozen=True)
class Event:
    kind: str
    at: float                          # the time, given from outside
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    kind: str
    item: Optional[str]
    why: str
    data: Dict[str, Any] = field(default_factory=dict)


def goal(stage: str, why: str = "") -> GoalState:
    return GoalState(phase=GOAL_STAGES[stage], stage=stage, why=why)


def item(iid: str, stage: str = "waiting", **fields: Any) -> ItemState:
    return ItemState(id=iid, phase=ITEM_STAGES[stage], stage=stage, **fields)


def moved(it: ItemState, stage: str, why: str, **fields: Any) -> ItemState:
    """The item in `stage`, its phase with it."""
    return replace(it, phase=ITEM_STAGES[stage], stage=stage, why=why, **fields)


def label(it: ItemState) -> str:
    """Today's items.state word for the item."""
    return "queued" if it.stage == "gate 2" else it.stage


def goal_label(g: GoalState) -> str:
    """Today's goals.state word for the goal."""
    return g.stage


def from_label(iid: str, state: str, **fields: Any) -> ItemState:
    """An item in today's items.state word."""
    return item(iid, state, **fields)
