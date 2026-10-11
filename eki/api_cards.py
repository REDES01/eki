"""A project's Cards area over HTTP, read side: its cards and the library to add from.

A card is one entry of the project's own components/ folder: a component of its own, or a
`{"pool": name}` card reaching a pool component. Nothing else is a card: a project assumes
nothing. The leaf modules (api_cardadd, api_cardedit, api_cardrun, api_cardsessions) use the
helpers here to find a project's root and its cards. A project with no folder has none, and
can't be changed until it has one (READONLY says why).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import cardview, components, projectchats
from .components import access, kindtext, uses

READONLY = "pick or clone a folder first"


def project_root_of(conn: sqlite3.Connection, pid: str) -> Optional[Path]:
    """The folder whose components/ holds the project's cards; None with no folder.
    KeyError for an unknown project."""
    return components.project_root(projectchats.folder_of(conn, pid), conn)


def writable_root(conn: sqlite3.Connection, pid: str) -> Path:
    root = project_root_of(conn, pid)
    if root is None:
        raise ValueError(READONLY)
    return root


def _read(folder: str) -> Optional[dict]:
    try:
        got = json.loads((Path(folder) / "component.json").read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


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
    """An `assets` card's pool size as a human string; '' for other kinds or a missing pool."""
    if c.kind != "assets":
        return ""
    try:
        from .components import assets
        p = assets.root_of(c) / c.spec["path"]
        return assets._human(assets._size(p)) if p.exists() else ""
    except Exception:  # never breaks the page
        return ""


def _waiting(conn: sqlite3.Connection, name: str, state: str) -> str:
    n = uses.waiting(conn, name)
    return f"{state} · {n} waiting" if n else state


def _shown(kind: str, spec: Any) -> Any:
    """What of a card's spec may leave the server: an mcp card's env values masked."""
    if kind == "mcp" and isinstance(spec, dict):
        from .components import mcpserver
        return mcpserver.public(spec)
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
    if c.kind == "instructions":
        from .components import instructions
        out["instructions"] = instructions.summary(root, c.spec.get("file", "AGENTS.md"))
    return out


def _broken(conn: sqlite3.Connection, root: Path, where: str, why: str) -> Dict[str, Any]:
    name = Path(where).name
    spec = _read(where)
    ref = spec["pool"] if spec is not None and list(spec) == ["pool"] and isinstance(spec["pool"], str) else ""
    kind = spec.get("kind") if spec is not None and isinstance(spec.get("kind"), str) else ""
    about = spec.get("about") if spec is not None and isinstance(spec.get("about"), str) else ""
    return {"name": name, "kind": kind, "about": about, "scope": "project", "ref": ref, "card": where,
            "folder": where, "error": why, "notes": [], "spec": _shown(kind, spec), "prompt": None,
            "instructions": None, "editable": spec is not None and not ref, "state": _waiting(conn, name, "broken"),
            "last_use": uses.last_use(conn, name, root), "links": [], "size": "",
            "choices": components.choices(kind), "choice_defaults": components.choice_defaults(kind),
            "choice_help": components.choice_help(kind),
            "field_help": components.field_help(kind), **_locked(kind, root),
            "title": kindtext.title(kind), "explain": kindtext.explain(kind), "live": {},
            "overview": cardview.broken(why)}


def cards_of(conn: sqlite3.Connection, root: Path) -> List[Dict[str, Any]]:
    """Every card in root/components, sorted by name."""
    found, errors = components.own(root)
    out = [_good(conn, root, c) for c in found.values()]
    out += [_broken(conn, root, where, why) for where, why in errors if Path(where).parent == root / "components"]
    return sorted(out, key=lambda c: c["name"])


def card_of(conn: sqlite3.Connection, pid: str, name: str) -> Dict[str, Any]:
    """The project's card `name`; KeyError when it has none."""
    root = project_root_of(conn, pid)
    for c in cards_of(conn, root) if root is not None else []:
        if c["name"] == name:
            return c
    raise KeyError(f"project {pid} has no card {name}")


def suggest(root: Path, cards: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The Cards area's offer of an instructions card; None when the project has one."""
    if any(c["kind"] == "instructions" for c in cards):
        return None
    for name in ("AGENTS.md", "CLAUDE.md"):
        if (root / name).is_file():
            return {"template": "instructions", "why": f"This project has {name}: add it as a card to see and edit it"}
    return {"template": "instructions",
            "why": "No AGENTS.md yet: add an instructions card to start one (and a CLAUDE.md pointing at it)"}


def project_cards(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid = m.group(1)
    folder = projectchats.folder_of(conn, pid)
    root = components.project_root(folder, conn)
    if root is None:
        return {"project": pid, "folder": folder, "readonly": READONLY, "cards": [], "suggest": None}
    cards = cards_of(conn, root)
    return {"project": pid, "folder": folder, "readonly": "", "cards": cards, "suggest": suggest(root, cards)}


def templates() -> List[Dict[str, str]]:
    """What `eki component kinds` lists as templates: each folder in components/templates."""
    try:
        dirs = sorted(d for d in components.templates_dir().iterdir()
                      if d.is_dir() and not d.name.startswith((".", "_")))
    except OSError:
        return []
    out = []
    for d in dirs:
        spec = _read(str(d)) or {}
        kind = str(spec.get("kind", ""))
        out.append({"name": d.name, "kind": kind, "about": str(spec.get("about", "")), "title": kindtext.title(kind)})
    return out


def library(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pool = [{"name": r["name"], "kind": r["kind"], "about": r["about"], "error": r["error"],
             "title": kindtext.title(r["kind"])}
            for r in components.listing(None, conn) if r["scope"] == "pool"]
    return {"templates": templates(), "pool": pool}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/projects/([\w.-]+)/cards", project_cards),
    ("GET", r"/api/cards/library", library),
]
