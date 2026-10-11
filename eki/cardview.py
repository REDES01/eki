"""What a card's panel says before any settings, built on the server: `live` (the kind's own live state,
from its module's optional `live(comp, conn, root) -> dict`) and `overview` (Used by: the workflow stages and
recent runs that used it; Right now: its state in words). api_cards.py adds both to each card. Neither raises:
a kind's trouble leaves its part empty."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from . import cardusedby, components
from .components import access, uses


def live(conn: sqlite3.Connection, root: Path, comp: components.Component) -> Dict[str, Any]:
    """The kind's live(comp, conn, root); {} when the kind has none, it raises, or it gives back no dict."""
    try:
        fn = getattr(components.kind_module(comp.kind), "live", None)
        got = fn(comp, conn, root) if callable(fn) else {}
    except Exception:  # a kind's live state must never break the page
        return {}
    return got if isinstance(got, dict) else {}


#: a provider's name as a person reads it
_PROVIDER = {"comfyui": "ComfyUI"}


def _titled(conn: sqlite3.Connection, rid: str) -> str:
    r = cardusedby._run(conn, rid)
    return f"'{cardusedby.title(conn, r)}'" if r is not None else "a run"


def _state(conn: sqlite3.Connection, comp: components.Component) -> List[str]:
    """Who is using it, who holds it, a busy provider, and how many wait; [] when none of these."""
    out: List[str] = []
    working = access._working(conn, comp.name)
    for rid in dict.fromkeys(r["parent"] or r["id"] for r in working):
        out.append(f"in use by {_titled(conn, rid)}")
    if not working and comp.spec.get("access") == "exclusive":
        held = access.holders(conn, comp.name)
        if held:
            out.append(f"{comp.name} is busy: held by {_titled(conn, held[0])}")
    provider = access.provider_of(comp.spec)              # a picture card's target's, or the old "provider"
    if not working and provider and access._provider_busy(conn, provider):
        out.append(f"{_PROVIDER.get(provider, provider)} is busy")
    n = uses.waiting(conn, comp.name)
    if n:
        out.append(f"{n} waiting")
    return out


def _links(links: Any) -> List[str]:
    good = [k for k in links if isinstance(k, dict)] if isinstance(links, list) else []
    if not good:
        return ["no preview links running"]
    out = [f"{len(good)} preview link{'s' if len(good) != 1 else ''} running"]
    for k in good:
        name = k.get("task") or Path(str(k.get("folder") or "")).name or "a copy"
        out.append(f"{cardusedby.short(str(name))} → {k.get('url') or ''}".rstrip(" →"))
    return out


def _comfy(c: Any) -> List[str]:
    if not isinstance(c, dict):
        return []
    if c.get("up"):
        return ["ComfyUI is up" + (f", last used {c['model']}" if c.get("model") else "")]
    return ["ComfyUI isn't running — it starts on the next picture"]


def _now(conn: sqlite3.Connection, comp: components.Component, live: Dict[str, Any]) -> List[str]:
    """The card's state in words, never a run id."""
    try:
        out = _state(conn, comp)
    except Exception:  # a broken DB: say what live knows
        out = []
    live = live if isinstance(live, dict) else {}
    if "links" in live:
        return out + _links(live["links"])
    extra = _comfy(live.get("comfy")) if "comfy" in live else []
    if not out:
        try:
            up = bool(components.kind_module(comp.kind).is_up(comp))
        except Exception:  # a kind's is_up must never break the page
            up = False
        out = ["running" if up and not extra else "idle"]
    return out + extra


def overview(conn: sqlite3.Connection, root: Path, comp: components.Component, live: Dict[str, Any]) -> Dict[str, Any]:
    """{"used_by": {"stages", "line", "runs"}, "now": [lines]} for the card's panel."""
    try:
        used = cardusedby.used_by(conn, root, comp)
    except Exception:  # never breaks the panel
        used = {"stages": [], "line": "", "runs": []}
    try:
        now = _now(conn, comp, live)
    except Exception:
        now = []
    return {"used_by": used, "now": now}


def broken(why: str) -> Dict[str, Any]:
    """The overview of a card that can't be read."""
    return {"used_by": {"stages": [], "line": "", "runs": []}, "now": [f"broken: {why}"]}
