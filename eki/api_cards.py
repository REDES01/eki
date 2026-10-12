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

from . import components, gitstart, projectchats
from .components import kindtext, uses

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


def broken_parts(where: str) -> Tuple[str, str, str, Optional[dict]]:
    """(kind, about, pool ref, spec) of a card that doesn't load: what its component.json still says."""
    spec = _read(where)
    ref = spec["pool"] if spec is not None and list(spec) == ["pool"] and isinstance(spec["pool"], str) else ""
    kind = spec.get("kind") if spec is not None and isinstance(spec.get("kind"), str) else ""
    about = spec.get("about") if spec is not None and isinstance(spec.get("about"), str) else ""
    return kind, about, ref, spec


def cards_of(conn: sqlite3.Connection, root: Path) -> List[Dict[str, Any]]:
    """Every card in root/components, sorted by name, as the grid shows it: name, title, kind, about,
    ref, error, state and last use. From the cached cards and one uses.grid call: no kind's is_up,
    live or notes, no subprocess, no git. A card's details are api_carddetail's."""
    found, errors = components.own(root)
    out = [{"name": c.name, "title": kindtext.title(c.kind), "kind": c.kind, "about": c.spec.get("about", ""),
            "ref": c.name if c.card else "", "error": ""} for c in found.values()]
    for where, why in errors:
        if Path(where).parent == root / "components":
            kind, about, ref, _ = broken_parts(where)
            out.append({"name": Path(where).name, "title": kindtext.title(kind), "kind": kind, "about": about,
                        "ref": ref, "error": why})
    got = uses.grid(conn, [c["name"] for c in out], root, specs={n: c.spec for n, c in found.items()})
    for c in out:
        g = got.get(c["name"]) or {}
        c["state"] = "broken" if c["error"] else g.get("state", "idle")
        c["last_use"] = g.get("last_use")
    return sorted(out, key=lambda c: c["name"])


def card_of(conn: sqlite3.Connection, pid: str, name: str) -> Dict[str, Any]:
    """The project's card `name` in full (api_carddetail.build), never kept; KeyError when it has none."""
    from . import api_carddetail
    root = project_root_of(conn, pid)
    try:
        if root is None:
            raise KeyError(name)
        return api_carddetail.build(conn, root, name)
    except KeyError:
        raise KeyError(f"project {pid} has no card {name}") from None


def _assets_offer(root: Path) -> Optional[Dict[str, str]]:
    """The offer of a media pool for what the first commit left out; None when it left nothing out.
    Its path is the one top-level folder every entry sits in, else "assets" (and the why says to move them)."""
    out = [e.strip().lstrip("/") for e in gitstart.left_out(root)]
    out = [e for e in out if e.strip("/")]
    if not out:
        return None
    more = f" and {len(out) - 1} more are" if len(out) > 1 else " is"
    why = f"{out[0]}{more} kept out of the history: add a media pool so every agent sees them"
    tops = {e.split("/", 1)[0] for e in out}
    top = tops.pop() if len(tops) == 1 else ""
    if top and (all("/" in e for e in out) or (root / top).is_dir()):
        return {"template": "assets", "why": why, "path": top}
    return {"template": "assets", "why": why + "; move them into assets/", "path": "assets"}


def suggest(root: Path, cards: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The Cards area's offer: a media pool for what the first commit left out (gitstart.left_out) when
    the project has no assets card, else an instructions card; None when it has that too."""
    if not any(c["kind"] == "assets" for c in cards):
        offer = _assets_offer(root)
        if offer is not None:
            return offer
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
