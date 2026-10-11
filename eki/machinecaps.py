"""Machine caps: how much eki runs at once on this Mac, across all projects.

routing.json's "machine" block holds two numbers, each a whole number 1..MOST:
`agents`, how many items may build at once across every project (each mostly
waits on the model, so this costs subscription quota rather than CPU), and
`checks`, how many test runs may run at once (judges, and the agents' own test
runs inside build turns) so load stays near the core count. A project's
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
#: the upper bound for both caps
MOST = 32
#: the old "self" keys and where they go
MOVED = {"parallel": "agents", "check_slots": "checks"}


def checks_default() -> int:
    return max(2, (os.cpu_count() or 6) // 6)


def _defaults() -> Dict[str, int]:
    return {"agents": AGENTS, "checks": checks_default()}


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


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
                block[new] = min(MOST, max(1, int(v)))
            except (TypeError, ValueError):
                pass                                    # a bad old value: the default
    data["machine"] = block
    try:
        _write(data)
    except OSError:
        pass
    return data


def settings() -> Dict[str, int]:
    """{"agents", "checks"}: the saved values clamped to 1..MOST, or the defaults."""
    out = _defaults()
    data = _read()
    if data is None:
        return out
    block = _migrate(data).get("machine")
    if not isinstance(block, dict):
        return out
    for k in out:
        try:
            out[k] = min(MOST, max(1, int(block[k])))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def save(agents: Optional[int] = None, checks: Optional[int] = None) -> Dict[str, int]:
    """Set the given caps in routing.json's machine block; the new settings()."""
    given = {k: v for k, v in (("agents", agents), ("checks", checks)) if v is not None}
    for k, v in given.items():
        if not _whole(v) or not 1 <= v <= MOST:
            raise ValueError(f"machine.{k} must be a whole number from 1 to {MOST}, not {v!r}")
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
    return [f"machine.{k} must be a whole number from 1 to {MOST}, not {block[k]!r}"
            for k in ("agents", "checks")
            if k in block and not (_whole(block[k]) and 1 <= block[k] <= MOST)]
