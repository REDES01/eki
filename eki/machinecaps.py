"""Machine caps: how much eki runs at once on this Mac, across all projects.

routing.json's "machine" block holds three whole numbers, each with its own
upper bound in MOST: `agents` (1..64), how many items may build at once across
every project (each mostly waits on the model, so this costs subscription quota
rather than CPU), `checks` (1..32), how many test runs may run at once
(judges, and the agents' own test runs inside build turns) so load stays near
the core count, and `browsers` (1..16), how many Playwright browsers may be
open at once across every run (each renders a page — a WebGL game costs
cores and memory; browserslots.py holds the slots). Two more set the memory
floor (eki/memadmit.py): `floor_gb` (1..64, default 6), the free + reclaimable
GB below which no new work starts, and `swap_max` (1..100, default 60), the
percent of swap in use above which no new work starts. A project's
workflow card `parallel` is only that project's cap; this one is separate and wins.

They used to live in the "self" block as `parallel` and `check_slots`; the
first read moves them here once (eki's own workflow card keeps its `parallel`
as eki's project cap).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import paths

#: the default for agents at once
AGENTS = 12
#: the default for browsers at once
BROWSERS = 3
#: the default memory floor, GB free + reclaimable
FLOOR_GB = 6
#: the default most swap in use, percent
SWAP_MAX = 60
#: each cap's upper bound
MOST = {"agents": 64, "checks": 32, "browsers": 16, "floor_gb": 64, "swap_max": 100}
#: the old "self" keys and where they go
MOVED = {"parallel": "agents", "check_slots": "checks"}


def checks_default() -> int:
    return max(2, (os.cpu_count() or 6) // 6)


def _defaults() -> Dict[str, int]:
    return {"agents": AGENTS, "checks": checks_default(), "browsers": BROWSERS,
            "floor_gb": FLOOR_GB, "swap_max": SWAP_MAX}


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _clamp(key: str, v: Any) -> int:
    """v as a whole number within key's bounds; TypeError/ValueError if it isn't one."""
    return min(MOST[key], max(1, int(v)))


def _bad(key: str, v: Any) -> Optional[str]:
    if _whole(v) and 1 <= v <= MOST[key]:
        return None
    return f"machine.{key} must be a whole number from 1 to {MOST[key]}, not {v!r}"


def _read() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(paths.config("routing").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(data: Dict[str, Any]) -> None:
    path = paths.config("routing")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """self.parallel / self.check_slots → machine.agents / machine.checks, once."""
    me = data.get("self")
    if "machine" in data or not isinstance(me, dict) or not any(k in me for k in MOVED):
        return data
    block: Dict[str, Any] = {}
    for old, new in MOVED.items():
        if old in me:
            v = me.pop(old)
            try:
                block[new] = _clamp(new, v)
            except (TypeError, ValueError):
                pass                                    # a bad old value: the default
    data["machine"] = block
    try:
        _write(data)
    except OSError:
        pass
    return data


def settings() -> Dict[str, int]:
    """{"agents", "checks", "browsers", "floor_gb", "swap_max"}: the saved values clamped to their bounds, or the defaults."""
    out = _defaults()
    data = _read()
    if data is None:
        return out
    block = _migrate(data).get("machine")
    if not isinstance(block, dict):
        return out
    for k in out:
        try:
            out[k] = _clamp(k, block[k])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def save(agents: Optional[int] = None, checks: Optional[int] = None,
         browsers: Optional[int] = None, floor_gb: Optional[int] = None,
         swap_max: Optional[int] = None) -> Dict[str, int]:
    """Set the given caps in routing.json's machine block; the new settings()."""
    given = {k: v for k, v in (("agents", agents), ("checks", checks), ("browsers", browsers),
                               ("floor_gb", floor_gb), ("swap_max", swap_max))
             if v is not None}
    for k, v in given.items():
        if _bad(k, v):
            raise ValueError(_bad(k, v))
    settings()                                          # migrate first, so old values aren't lost
    data = _read()
    if data is None:
        raise ValueError(f"{paths.config('routing').name} can't be read; fix it first")
    block = data.get("machine")
    data["machine"] = {**(block if isinstance(block, dict) else {}), **given}
    _write(data)
    return settings()


def problems(block: Any) -> List[str]:
    """Every problem with a "machine" block, in words."""
    if not isinstance(block, dict):
        return ["machine must be an object"]
    found = (_bad(k, block[k]) for k in MOST if k in block)
    return [why for why in found if why]
