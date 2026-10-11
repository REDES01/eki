"""One card in full, for its side panel: everything the grid (api_cards.cards_of) leaves out —
Used by and Right now (cardview), the kind's live state, notes, preview links, the stored pool size,
settings, choices, skills, picture and instructions.

GET /api/projects/<pid>/cards/<name> builds only that card and keeps it 5 s per (root, name, the
card's folder key), so the panel's 5 s reload costs one build. `build` itself is never cached: the
edit routes (through api_cards.card_of) return it right after a change.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import api_cards, cardview, components
from .components import access, cardcache, kindtext, uses

#: how long an opened card's details are kept, in seconds
KEEP = 5.0
_lock = threading.Lock()
_kept: Dict[tuple, Tuple[float, Dict[str, Any]]] = {}


def _state(conn: sqlite3.Connection, comp: components.Component) -> str:
    state = access.in_use(conn, comp.name, comp.spec)
    if not state:
        try:
            up = bool(components.kind_module(comp.kind).is_up(comp))
        except Exception:  # a kind's is_up must never break the page
            up = False
        state = "up" if up else "idle"
    return state


def _links(c: components.Component) -> List[dict]:
    """The serving links that are up for a `serve` card; [] for anything else or on any trouble."""
    if c.kind != "serve":
        return []
    try:
        from .components import serve
        return list(serve.links(c))
    except Exception:  # a dead server or odd state must never break the page
        return []


def _size(c: components.Component) -> str:
    """An `assets` card's pool size as housekeeping last measured it; '' for other kinds or never measured."""
    try:
        from .components import assets
        return assets.size_text(c)
    except Exception:  # never breaks the page
        return ""


def _waiting(conn: sqlite3.Connection, name: str, state: str) -> str:
    n = uses.waiting(conn, name)
    return f"{state} · {n} waiting" if n else state


def _shown(kind: str, spec: Any) -> Any:
    """What of a card's spec may leave the server: an mcp card's server.env values masked, and a
    service card's mcp.env values the same way."""
    if kind == "mcp" and isinstance(spec, dict):
        from .components import mcpserver
        return mcpserver.public(spec)
    if kind == "service" and isinstance(spec, dict) and isinstance(spec.get("mcp"), dict):
        from .components import mcpserver
        return {**spec, "mcp": mcpserver.public({"server": spec["mcp"]})["server"]}
    return spec


def _locked(kind: str, root: Path) -> Dict[str, Any]:
    """A workflow card's checks that can't be turned off: workflow.LOCKED on eki's own project, none elsewhere."""
    if kind != "workflow":
        return {}
    from . import workflow
    return {"locked": list(workflow.LOCKED) if workflow.is_eki(root) else []}


def _good(conn: sqlite3.Connection, root: Path, c: components.Component) -> Dict[str, Any]:
    prompt = None
    if c.kind == "agent":
        try:
            from .components import agent
            prompt = agent.prompt_text(c)
        except OSError:
            prompt = ""
    out = {"name": c.name, "kind": c.kind, "about": c.spec.get("about", ""), "scope": c.scope,
           "ref": c.name if c.card else "", "card": str(c.card or c.folder), "folder": str(c.folder),
           "error": "", "notes": components._notes(c), "spec": _shown(c.kind, c.spec), "prompt": prompt,
           "instructions": None, "editable": not c.card, "state": _waiting(conn, c.name, _state(conn, c)),
           "last_use": uses.last_use(conn, c.name, root), "links": _links(c), "size": _size(c),
           "choices": components.choices(c.kind), "choice_defaults": components.choice_defaults(c.kind),
           "choice_help": components.choice_help(c.kind),
           "field_help": components.field_help(c.kind), **_locked(c.kind, root),
           "title": kindtext.title(c.kind), "explain": kindtext.explain(c.kind)}
    out["live"] = cardview.live(conn, root, c)
    out["overview"] = cardview.overview(conn, root, c, out["live"])
    if c.kind == "skills":
        from .components import skillcard
        good, broken = skillcard.held(c.folder)
        out["skills"] = [{"name": s["name"], "description": s["description"]} for s in good]
        out["broken_skills"] = [{"path": where, "error": why} for where, why in broken]
    if c.kind == "service":
        from . import api_cardpicture
        pic = api_cardpicture.part(conn, root, c)
        if pic is not None:
            out["picture"] = pic
    if c.kind == "instructions":
        from .components import instructions
        out["instructions"] = instructions.summary(root, c.spec.get("file", "AGENTS.md"))
    return out


def _broken(conn: sqlite3.Connection, root: Path, where: str, why: str) -> Dict[str, Any]:
    name, (kind, about, ref, spec) = Path(where).name, api_cards.broken_parts(where)
    return {"name": name, "kind": kind, "about": about, "scope": "project", "ref": ref, "card": where,
            "folder": where, "error": why, "notes": [], "spec": _shown(kind, spec), "prompt": None,
            "instructions": None, "editable": spec is not None and not ref, "state": _waiting(conn, name, "broken"),
            "last_use": uses.last_use(conn, name, root), "links": [], "size": "",
            "choices": components.choices(kind), "choice_defaults": components.choice_defaults(kind),
            "choice_help": components.choice_help(kind),
            "field_help": components.field_help(kind), **_locked(kind, root),
            "title": kindtext.title(kind), "explain": kindtext.explain(kind), "live": {},
            "overview": cardview.broken(why)}


def build(conn: sqlite3.Connection, root: Path, name: str) -> Dict[str, Any]:
    """The full card `name` in root/components, built afresh; KeyError when there is none."""
    found, errors = components.own(root)
    if name in found:
        return _good(conn, root, found[name])
    for where, why in errors:
        if Path(where).parent == root / "components" and Path(where).name == name:
            return _broken(conn, root, where, why)
    raise KeyError(f"no card {name} in {root}")


def _kept_build(conn: sqlite3.Connection, root: Path, name: str) -> Dict[str, Any]:
    """build(), kept KEEP seconds; a changed card folder or component.json is built again at once."""
    key = (str(root), name, cardcache._key(root / "components" / name))
    now = time.monotonic()
    with _lock:
        got = _kept.get(key)
        if got is not None and now - got[0] < KEEP:
            return got[1]
    card = build(conn, root, name)
    with _lock:
        for k in [k for k, (t, _) in _kept.items() if now - t >= KEEP]:
            del _kept[k]
        _kept[key] = (now, card)
    return card


def card_detail(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    root: Optional[Path] = api_cards.project_root_of(conn, pid)
    if root is None:
        raise KeyError(f"project {pid} has no card {name}")
    try:
        return {"card": _kept_build(conn, root, name)}
    except KeyError:
        raise KeyError(f"project {pid} has no card {name}") from None


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/projects/([\w.-]+)/cards/([\w.-]+)", card_detail),
]
