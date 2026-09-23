# SPDX-License-Identifier: Apache-2.0
"""Goals: things you ask eki to keep doing (ROADMAP, Stage 3).

A goal is what you'd type in a chat, left running:

    Every morning, look at my X and propose 3 posts in my voice.
    Make 20 NPCs for Ashfall — each with a bio, a portrait and three lines.
    Keep the tests of ~/eki passing; open a branch when one breaks.

Plus, optionally, when (once until it's done, every day, every week, every few
hours), a folder to work in, and whether it may use your subscriptions. That's
all. eki doesn't plan the work — the agent that gets it does, the same way it
would in a chat: the routing table picks it (a harness when tools are needed,
Qwen with Codex's hands for local work), and it writes files, draws through
eki, and says where things stand.

Each turn is an ordinary request in the goal's own thread, so it remembers
what it did and what you said; you answer it there like any chat. A turn ends
with one line — `GOAL: done`, `GOAL: continue` or `GOAL: waiting` — and that
line is the whole protocol: done stops a one-off goal (a repeating one waits
for its next time), continue asks for another turn, waiting waits for your
reply. Turns run when the machine has room (eki/shift.py).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import schedules as schedules_mod

PATH = Path("~/.eki/goals/goals.json").expanduser()
LOG = Path("~/.eki/goals/log.jsonl").expanduser()
#: a one-off goal that hasn't said it's done after this many turns stops, to be looked at
MAX_TURNS = 30
#: a turn that failed is tried again after this long; this many failures in a row stops it
RETRY_SECONDS = 1800
MAX_FAILURES = 3
#: after stepping aside, a goal waits this long before its next try — no thrashing
STEP_OUT_PAUSE = 60
STATES = ("active", "waiting", "paused", "done", "stuck")
MARK = re.compile(r"^\s*\**\s*GOAL\s*:\s*(done|continue|waiting)\b", re.I | re.M)


class GoalError(ValueError):
    pass


@dataclass
class Goal:
    id: str
    text: str
    when: Dict[str, Any] = field(default_factory=lambda: {"kind": "once"})
    folder: str = ""
    spare: bool = False                 # may use subscriptions, within their spare room
    state: str = "active"
    conversation: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    turns: int = 0
    last_turn_at: int = 0
    last_turn: int = 0                  # the thread's turn id of the last goal prompt
    next_at: int = 0                    # a repeating goal's next time; a retry's
    failures: int = 0
    note: str = ""                      # what it's waiting on, why it stopped

    @property
    def repeats(self) -> bool:
        return self.when.get("kind") != "once"

    def to_json(self) -> Dict[str, Any]:
        out = asdict(self)
        out["repeats"] = self.repeats
        out["when_text"] = describe(self.when)
        return out


def describe(when: Dict[str, Any]) -> str:
    if when.get("kind") == "once":
        return "once, until it's done"
    return schedules_mod.describe(when)


def check_when(when: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    when = dict(when or {"kind": "once"})
    kind = when.get("kind")
    if kind == "once":
        return {"kind": "once"}
    if kind == "interval":
        minutes = int(when.get("minutes") or 0)
        if minutes < 15:
            raise GoalError("a repeating goal runs at most every 15 minutes")
        return {"kind": "interval", "minutes": minutes}
    if kind == "daily":
        at = str(when.get("at") or "09:00")
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
            raise GoalError(f"not a time of day: {at!r}")
        days = sorted({int(d) for d in (when.get("days") or range(7)) if 0 <= int(d) <= 6})
        return {"kind": "daily", "at": at, "days": days or list(range(7))}
    raise GoalError("when is once, daily (at, days) or interval (minutes)")


# ---- the store ------------------------------------------------------------------------

def _load_raw() -> List[Dict[str, Any]]:
    try:
        return list(json.loads(PATH.read_text()).get("goals") or [])
    except (OSError, ValueError):
        return []


def all_goals() -> List[Goal]:
    out = []
    for raw in _load_raw():
        try:
            out.append(Goal(**{k: v for k, v in raw.items() if k in Goal.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def _save(goals: List[Goal]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"goals": [asdict(g) for g in goals]}, indent=2, ensure_ascii=False))
    tmp.replace(PATH)


def get(gid: str) -> Goal:
    """By id, or by the start of it (the CLI)."""
    goals = all_goals()
    exact = next((g for g in goals if g.id == gid), None)
    if exact:
        return exact
    starts = [g for g in goals if g.id.startswith(gid)] if gid else []
    if len(starts) == 1:
        return starts[0]
    raise GoalError(f"no goal {gid!r}" if not starts else f"{gid!r} could be several goals")


def create(text: str, when: Optional[Dict[str, Any]] = None, folder: str = "",
           spare: bool = False, now: Optional[float] = None) -> Goal:
    text = text.strip()
    if not text:
        raise GoalError("say what eki should keep doing")
    folder = str(Path(folder).expanduser()) if folder.strip() else ""
    if folder and not Path(folder).is_dir():
        raise GoalError(f"no folder {folder}")
    # its first turn as soon as there's room, so you see it work; then on its schedule
    g = Goal(id=uuid.uuid4().hex[:10], text=text, when=check_when(when), folder=folder, spare=bool(spare))
    goals = all_goals()
    goals.append(g)
    _save(goals)
    return g


def update(gid: str, **fields: Any) -> Goal:
    goals = all_goals()
    g = next((x for x in goals if x.id == gid), None)
    if g is None:
        raise GoalError(f"no goal {gid!r}")
    if "text" in fields:
        fields["text"] = str(fields["text"]).strip()
        if not fields["text"]:
            raise GoalError("say what eki should keep doing")
    if "folder" in fields:
        f = str(fields["folder"] or "").strip()
        fields["folder"] = str(Path(f).expanduser()) if f else ""
        if fields["folder"] and not Path(fields["folder"]).is_dir():
            raise GoalError(f"no folder {fields['folder']}")
    if "when" in fields:
        fields["when"] = check_when(fields["when"])
    if "state" in fields and fields["state"] not in STATES:
        raise GoalError(f"a goal is {', '.join(STATES)}")
    for k, v in fields.items():
        if k in Goal.__dataclass_fields__ and k != "id":
            setattr(g, k, v)
    if "when" in fields:
        g.next_at = _next(g, time.time())
    _save(goals)
    return g


def remove(gid: str) -> Goal:
    goals = all_goals()
    g = next((x for x in goals if x.id == gid), None)
    if g is None:
        raise GoalError(f"no goal {gid!r}")
    _save([x for x in goals if x.id != gid])
    return g


# ---- when a goal wants a turn ----------------------------------------------------------------

def _next(g: Goal, after: float) -> int:
    if not g.repeats:
        return 0
    t = schedules_mod.next_time(g.when, after, None)
    return int(t) if t else 0


def due(g: Goal, now: float) -> bool:
    if g.state != "active":
        return False
    return now >= (g.next_at or 0)


def prompt(g: Goal, now: Optional[float] = None) -> str:
    """What a turn says to the agent: the goal, and the one line to end with."""
    folder = f" Work in {g.folder}." if g.folder else ""
    if g.repeats:
        when = datetime.fromtimestamp(now or time.time()).strftime("%A %Y-%m-%d %H:%M")
        return (f"[eki · goal · {when}] {g.text}\n\n"
                f"[eki: this is this time's run of a goal you keep doing in the background.{folder} "
                "Do this time's part in one go. End your reply with one line: `GOAL: done` when "
                "this time's part is finished, or `GOAL: waiting` if you need me to decide "
                "something (ask it just above that line).]")
    if g.turns == 0:
        return (f"[eki · goal] {g.text}\n\n"
                f"[eki: you're working on this in the background, whenever this machine has "
                f"room.{folder} Do as much as makes sense in one go. End your reply with one line: "
                "`GOAL: done` when the goal is met, `GOAL: continue` if there's more to do and you "
                "want another turn, or `GOAL: waiting` if you need me to decide something (ask it "
                "just above that line).]")
    return (f"[eki · goal] Carry on with the goal: {g.text}\n\n"
            "[eki: pick up where you left off. End with `GOAL: done`, `GOAL: continue` or "
            "`GOAL: waiting`, as before.]")


def outcome(answer: str) -> str:
    """done | continue | waiting — the last marker in the answer; none means continue."""
    found = MARK.findall(answer or "")
    return found[-1].lower() if found else "continue"


def after_turn(g: Goal, answer: str, ok: bool, now: Optional[float] = None) -> Goal:
    """Where the goal stands once a turn has ended."""
    now = now or time.time()
    fields: Dict[str, Any] = {"last_turn_at": int(now)}
    if not ok:
        failures = g.failures + 1
        fields["failures"] = failures
        if failures >= MAX_FAILURES:
            fields.update(state="stuck", note=f"{failures} turns in a row failed")
        else:
            fields["next_at"] = int(now + RETRY_SECONDS)
        return update(g.id, **fields)
    fields["failures"] = 0
    said = outcome(answer)
    if said == "waiting":
        fields.update(state="waiting", note="waiting for your reply")
    elif g.repeats:
        fields.update(next_at=_next(g, now), note="")
    elif said == "done":
        fields.update(state="done", note="")
    elif g.turns >= MAX_TURNS:
        fields.update(state="stuck", note=f"{g.turns} turns without saying it's done")
    else:
        fields.update(next_at=0, note="")
    return update(g.id, **fields)


def replied(g: Goal, turns: List[Dict[str, Any]]) -> bool:
    """Has someone answered in the thread since the goal last asked?"""
    return any(t["role"] == "user" and t["id"] > g.last_turn
               and not str(t.get("content") or "").startswith("[eki · goal")
               for t in turns)


# ---- what the shift did -------------------------------------------------------------------

def note(entry: Dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def history(since: float) -> List[Dict[str, Any]]:
    try:
        lines = LOG.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if float(e.get("at") or 0) >= since:
            out.append(e)
    return out
