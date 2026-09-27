"""`eki self loop on|off` and `eki self pick` (not a command of its own): the
switch for the loop, and a dry run of what it would pick now (eki/selfpick.py)."""
from __future__ import annotations

from .. import selfpick, selfwork
from .common import err


def loop(words) -> int:
    """`eki self loop on|off`: the only thing that writes routing.json `self.loop`."""
    if len(words) != 1 or words[0] not in ("on", "off"):
        err("usage: eki self loop on|off")
        return 2
    selfpick.set_loop(words[0] == "on")
    print(f"loop {'on' if selfwork.settings().get('loop') else 'off'}")
    return 0


def pick(c) -> int:
    """What the picker would do right now. Writes nothing."""
    s = selfpick.settings()
    print(f"loop: {'on' if s['loop'] else 'off'}")
    ok, why = selfpick.idle(c)
    print(f"idle: {'yes' if ok else 'no'} — {why}")
    print(f"waiting on you: {selfpick.waiting(c)} of review_max {s['review_max']}")
    print(f"picked today: {selfpick.picked_today(c)} of picks_per_day {s['picks_per_day']}")
    p = selfpick.choose(c)
    gate = selfpick.stopped(c)
    but = f"  (but {gate} stops it)" if gate else ""
    if p is None:
        print("would pick: nothing")
    elif p.stage == "fault":
        print(f"would pick: fault {p.payload[0]} — {p.why}{but}")
    elif p.stage == "journal":
        print(f"would pick: journal {p.payload.key} — {p.why}{but}")
    else:
        print(f"would pick: roadmap — rank these:{but}")
        for e in p.payload:
            print(f"  {e.key}  {e.section} — {e.title}")
    return 0
