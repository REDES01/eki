# SPDX-License-Identifier: Apache-2.0
"""eki's daily digest: one short page a day instead of a stream.

Every change eki made to itself since the last digest, one line each with a
link to it — what landed, what went wrong, what waits for you — and what
helped. Built from what eki wrote down (the changes to itself, the
self-work items, the finished runs), never from a model's words, so it
costs nothing and can't make anything up; and nothing is left out, so a
line missing from it means it didn't happen. It is kept under
~/.eki/self/digests, shown on the board (Goals → Self) and by
`eki self digest`, and sent as one notification. Per-change notifications
are left for what needs you (docs/self-build.md, "The daily digest").
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

HOME = Path("~/.eki/self").expanduser()
DAY = 86400
#: when the page is written, local time, unless `digest_at` says otherwise
AT = "09:00"

#: where a change came from, in the words the page uses for "why"
WHY = {"asked": "you asked", "fault": "a fault in eki's own code", "roadmap": "the next ROADMAP item",
       "note": "the weekly note", "undo": "you took a change back"}
#: states that mean it changed eki (or took a change back out)
LANDED = ("applying", "applied", "undoing", "undone")
#: states that mean it was tried and didn't land
MISSED = ("unfit", "stopped", "rolled back", "not started", "no change", "discarded", "gone")
#: states that wait for a person
WAITING = ("proposed", "conflicts")
#: how a state reads on the page, where the state is the news
SAID = {"applying": "going live", "undoing": "being taken back", "undone": "taken back",
        "unfit": "didn't pass its checks", "stopped": "didn't finish", "rolled back": "rolled back",
        "not started": "didn't start", "no change": "made no change", "discarded": "discarded",
        "gone": "gone", "proposed": "didn't pass its checks", "conflicts": "doesn't fit on top of your checkout"}
#: a link on the page to the change on the board: [title](#/self/<id>)
LINK = re.compile(r"\[([^\]]*)\]\(#/self/([^)\s]+)\)")


def _dir(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "digests"


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _minutes(at: str) -> int:
    """"09:30" → 570; anything unreadable is the default time."""
    try:
        h, m = (int(x) for x in str(at).split(":", 1))
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except ValueError:
        pass
    return 9 * 60


def saved(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every digest kept, oldest first."""
    d = _dir(home)
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def latest(home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    got = saved(home)
    return got[-1] if got else None


def due(now: Optional[float] = None, at: str = AT, home: Optional[Path] = None) -> bool:
    """Today's page isn't written yet and it's past the time it's written at."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    if lt.tm_hour * 60 + lt.tm_min < _minutes(at):
        return False
    last = latest(home)
    return not last or last.get("id") != _day(now)


def _first_line(text: Any) -> str:
    return next((x.strip("-•* ").strip() for x in str(text or "").splitlines() if x.strip("-•* ").strip()), "")


def _cut(text: str, n: int = 160) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _link(c: Dict[str, Any]) -> str:
    """The change's title, linked to it on the board (Goals → Self → the change)."""
    title = _cut(" ".join(str(c.get("title") or f"self/{c['id']}").split()), 90)
    return f"[{title.replace('[', '(').replace(']', ')')}](#/self/{c['id']})"


def plain(text: str) -> str:
    """The page for a terminal: a link reads "title (self/<id>)"."""
    return LINK.sub(lambda m: f"{m.group(1)} (self/{m.group(2)})", text or "")


def build(changes: Iterable[Dict[str, Any]], items: Iterable[Dict[str, Any]] = (), *,
          since: float, now: Optional[float] = None, work: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The page for what happened between `since` and `now`.

    `changes`: eki's changes to itself (selfwork.changes()); `items`: the
    self-work items (selfloop.items(), as dicts); `work`: what helped, in
    numbers — {"local_hours", "week_hours_a_day", "goal_turns"}.
    What waits for you is what waits now, however old; the rest is the window's."""
    now = time.time() if now is None else now
    changes = list(changes)
    fresh = [c for c in changes if since <= float(c.get("state_at") or c.get("at") or 0) < now + 1]
    waiting = [c for c in changes if c.get("state") in WAITING and c.get("fit")]
    landed = [c for c in fresh if c.get("state") in LANDED]
    # everything else of the window didn't land — a state added later included
    missed = [c for c in fresh if c.get("state") not in LANDED and c not in waiting]
    left = [i for i in items if i.get("state") in ("person", "gave up")
            and since <= float(i.get("updated_at") or 0)]

    lines: List[str] = []
    if landed:
        lines += ["", "**Landed**"]
        for c in landed:
            said = _first_line(c.get("summary"))
            why = WHY.get(str(c.get("source") or ""), "")
            lines.append(f"- {_link(c)}" + _cut((f" — {said}" if said and said != c.get("title") else "")
                                                 + (f" (why: {why})" if why else "")
                                                 + (f" · {SAID[c['state']]}" if c.get("state") in SAID else ""), 200))
    if missed:
        lines += ["", "**Went wrong**"]
        for c in missed:
            state = str(c.get("state") or "")
            said = _first_line(c.get("why") or c.get("verdict"))
            lines.append(f"- {_link(c)} — " + _cut(SAID.get(state, state) + (f": {said}" if said else ""), 160))
    wants = [f"- {_link(c)} — "
             + ("touches what eki may not change alone; read it line by line" if c.get("protected")
                else "conflicts with your checkout" if c.get("state") == "conflicts"
                else "proposed, apply or discard it")
             for c in waiting]
    wants += [f"- {_link({'id': i['change'], 'title': i.get('title')}) if i.get('change') else _cut(str(i.get('title')), 90)}"
              f" — {_cut(str(i.get('note') or 'left for you'), 120)}" for i in left]
    if wants:
        lines += ["", "**Waits for you**"] + wants
    helped = []
    w = work or {}
    if w.get("local_hours") is not None:
        hours = float(w.get("local_hours") or 0)
        week = w.get("week_hours_a_day")
        helped.append(f"- This Mac's own models did {hours:.1f} h of work"
                      + (f" (the week's average: {float(week):.1f} h a day)" if week is not None else ""))
    if w.get("goal_turns"):
        helped.append(f"- {w['goal_turns']} goal turn{'s' if w['goal_turns'] != 1 else ''} finished")
    if helped:
        lines += ["", "**What helped**"] + helped
    quiet = not (landed or missed or wants)
    if quiet:
        lines += ["", "A quiet day: eki didn't change itself and nothing waits for you."]
    return {"id": _day(now), "at": int(now), "since": int(since), "text": "\n".join(lines).strip(),
            "changed": len(landed), "missed": len(missed), "waiting": len(wants),
            "quiet": quiet}


def save(page: Dict[str, Any], home: Optional[Path] = None) -> Dict[str, Any]:
    d = _dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{page['id']}.json").write_text(json.dumps(page, indent=2, ensure_ascii=False))
    return page


def since(now: Optional[float] = None, home: Optional[Path] = None) -> float:
    """Where the next page starts: where the last one ended, at most a week back."""
    now = time.time() if now is None else now
    last = latest(home)
    start = float(last.get("at") or 0) if last else 0.0
    return max(start, now - 7 * DAY) if start else now - DAY


def notification(page: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The one notification for a page — none for a quiet day."""
    if page.get("quiet"):
        return None
    bits = []
    if page.get("changed"):
        bits.append(f"{page['changed']} change{'s' if page['changed'] != 1 else ''}")
    if page.get("missed"):
        bits.append(f"{page['missed']} went wrong")
    if page.get("waiting"):
        bits.append(f"{page['waiting']} wait{'s' if page['waiting'] == 1 else ''} for you")
    return {"title": "eki's day", "body": " · ".join(bits) + " — Goals → Self"}


def needs_you(change: Dict[str, Any], item_state: str = "") -> bool:
    """Whether a change is worth a notification of its own, rather than a
    line in the digest: it waits for a person, or was left to one."""
    if item_state in ("person", "gave up"):
        return True
    return change.get("state") in WAITING and bool(change.get("fit"))
