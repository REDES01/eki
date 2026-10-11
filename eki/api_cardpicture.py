"""A picture card's own part of its JSON (api_cards adds it as "picture"): what the panel shows.

A picture card is a service card {"target": "comfyui:<workflow>", "out": "…"}; the old form
{"provider": "<a comfyui provider>"} reads as target "<provider>:draft" (comfyui_workflows.DEFAULT)
and out "assets/generated/<card>/". The part says which workflow it draws with (describe(),
from the ComfyUI provider's entry), the absolute out folder, and the last pictures its uses made:
the paths each call run reported ("tool" events named "eki use <card>"), with the prompt it was
given and the task that asked — the caller run's thread title, else its prompt, else 'by hand'.
Nothing new is recorded; everything is read from the runs and events tables.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import store
from .components import Component, uses

RECENT = 12
BY_HAND = "by hand"


def _kind_of(provider: str) -> str:
    """Its kind in providers.json, else in eki's defaults (comfyui is listed only once installed)."""
    from . import providers
    cfg = providers.config().get(provider) or providers.DEFAULTS.get(provider) or {}
    return str(cfg.get("kind") or "")


def target_of(spec: dict) -> str:
    """The card's picture target, "" when it isn't a picture card."""
    from .providers import comfyui_workflows as wf
    if not isinstance(spec, dict) or any(k in spec for k in ("run", "serve")):
        return ""
    if isinstance(spec.get("provider"), str):
        name = spec["provider"].strip()
        return f"{name}:{wf.DEFAULT}" if name and _kind_of(name) == "comfyui" else ""
    t = spec.get("target")
    return t if isinstance(t, str) and t.strip() and "out" in spec else ""


def _prompt(args: List[Any]) -> str:
    """The prompt a call was given: --prompt's value, else its words that aren't --flags or their values."""
    words = [str(a) for a in args or []]
    for i, a in enumerate(words):
        if a == "--prompt" and i + 1 < len(words):
            return words[i + 1]
        if a.startswith("--prompt="):
            return a.split("=", 1)[1]
    out, skip = [], False
    for a in words:
        if skip:
            skip = False
        elif a.startswith("--"):
            skip = "=" not in a
        else:
            out.append(a)
    return " ".join(out)


def _task(conn: sqlite3.Connection, caller: Optional[str]) -> str:
    r = store.run(conn, caller) if caller else None
    if r is None:
        return BY_HAND
    t = store.thread(conn, r["thread_id"])
    title = (t["title"] if t is not None else "") or ""
    if title.strip():
        return title.strip()
    line = next((ln.strip() for ln in (r["prompt"] or "").splitlines() if ln.strip()), "")
    return line[:200] or BY_HAND


def recent(conn: sqlite3.Connection, root: Path, name: str, limit: int = RECENT) -> List[Dict[str, Any]]:
    """The last `limit` pictures the card's calls from `root` made, newest first."""
    out: List[Dict[str, Any]] = []
    tool = f"eki use {name}"
    for r in uses._rows(conn, name, root):
        try:
            c = json.loads(r["prompt"])
        except ValueError:
            continue
        made = []
        for ev in store.events_after(conn, r["id"]):
            if ev["kind"] != "tool":
                continue
            data = json.loads(ev["data"] or "{}")
            p = data.get("path")
            if data.get("name") == tool and p and p not in made and os.path.isfile(p):
                made.append(p)
        if not made:
            continue
        prompt, task = _prompt(c.get("args") or []), _task(conn, c.get("caller_run") or r["parent"])
        at = r["ended_at"] or r["started_at"] or r["created_at"]
        for p in reversed(made):
            out.append({"path": p, "prompt": prompt, "task": task, "run": r["id"], "at": at})
            if len(out) >= limit:
                return out
    return out


def part(conn: sqlite3.Connection, root: Path, c: Component) -> Optional[Dict[str, Any]]:
    """The "picture" key of a card's JSON; None for a card that isn't a picture card."""
    from . import targets
    from .providers import comfyui_workflows as wf
    target = target_of(c.spec)
    if not target:
        return None
    provider, workflow = "", None
    try:
        t = targets.parse(target)
        provider, wid = t.provider, t.model or wf.DEFAULT
        if _kind_of(provider) != "comfyui":
            workflow = {"error": f"{provider!r} isn't a ComfyUI provider; pick ComfyUI → a workflow"}
        else:
            workflow = wf.describe(wid)
    except (KeyError, ValueError) as e:
        workflow = {"error": str(e.args[0] if e.args else e)}
    out = c.spec.get("out") if isinstance(c.spec.get("out"), str) and c.spec.get("out").strip() \
        else f"assets/generated/{c.name}/"
    return {"target": target, "provider": provider, "workflow": workflow, "out": str((root / out).resolve()),
            "recent": recent(conn, root, c.name)}
