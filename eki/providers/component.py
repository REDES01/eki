"""A component call, as a run: what `eki use <name> [args…]` makes.

The prompt is JSON — {"component": name, "args": [..], "caller_dir": abs
path, "caller_run": id or null}. The component is found again from the
caller's folder (so a project's own one wins, as in `eki use`) and its
kind's module does the call. The result's text is a `text` event, each
output path a `tool` event; a result error, or an exception, fails the run
with that reason. Built in (`providers.BUILTIN`), never in the routing
table: a run reaches it only when it's picked by name.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .base import Emit, Outcome, Provider, Turn


def parse(prompt: str) -> Dict[str, Any]:
    """The call a prompt asks for; raises ValueError when it isn't one."""
    try:
        got = json.loads(prompt or "")
    except json.JSONDecodeError as e:
        raise ValueError(f"a component call is JSON: {e}")
    if not isinstance(got, dict) or not isinstance(got.get("component"), str) or not got["component"]:
        raise ValueError('a component call names its "component"')
    args = got.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError('a component call\'s "args" is a list of strings')
    if not isinstance(got.get("caller_dir"), str) or not got["caller_dir"]:
        raise ValueError('a component call says its "caller_dir"')
    return {"component": got["component"], "args": args, "caller_dir": got["caller_dir"],
            "caller_run": got.get("caller_run") or None}


class ComponentCall(Provider):
    kind = "component"

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        from .. import components, db
        out = Outcome()
        try:
            want = parse(turn.prompt)
            name = want["component"]
            conn = db.connect()                      # resolve exactly as `eki use` did
            try:
                comp = components.get(name, want["caller_dir"], conn)
            finally:
                conn.close()
            call = components.Call(want["args"], want["caller_dir"], want["caller_run"],
                                   run_id=turn.run_id, emit=emit, extra=turn.extra)
            res = components.kind_module(comp.kind).call(comp, call)
        except KeyError as e:                        # missing or broken: its message is the reason
            out.state, out.error = "failed", str(e.args[0] if e.args else e)
            return out
        except Exception as e:                       # noqa: BLE001 — a component's bug fails its call only
            out.state, out.error = "failed", f"{type(e).__name__}: {e}"
            return out
        if res is None:
            out.state, out.error = "failed", f"component {name} returned nothing"
            return out
        if res.text:
            emit("text", {"text": res.text})
        for p in res.paths or []:
            emit("tool", {"name": f"eki use {name}", "path": str(p)})
        if res.error:
            out.state, out.error = "failed", res.error
        out.finished = True
        return out
