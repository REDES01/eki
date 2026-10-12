"""Starting a project from the window: where a new one goes, whether a folder is one already,
and the cards to suggest for what is in its folder. `api_projects` routes them.

Nothing is kept here. The proposal is read from the `projects` rows each time: the newest
project with a folder of its own (made from the window or `eki project new`; a row doesn't say
which) gives the parent, else `~/Projects`; never your home's root itself.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import api_cards, gitstart, paths, projectchats

#: how many recent folders the proposal offers
RECENT = 8
#: where new projects go when there's no last-used parent (or it was your home itself)
DEFAULT_BASE = "Projects"
MEDIA_DIRS = ("assets", "images", "media", "art", "sounds", "audio", "video")
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".psd", ".heic",
             ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aiff", ".mp4", ".mov", ".webm", ".mkv", ".avi",
             ".glb", ".gltf", ".fbx", ".obj", ".blend"}
#: top-level media files that make a folder of assets
MEDIA_FILES = 5


def _folders(conn: sqlite3.Connection) -> List[str]:
    """Every live local project's folder, newest project first, once each."""
    marks = ",".join("?" * len(projectchats.HIDDEN))
    rows = conn.execute(
        "SELECT COALESCE(folder, CASE WHEN repo IS NULL OR state='retired' THEN path END) AS f "
        f"FROM projects WHERE COALESCE(state,'on') NOT IN ({marks}) ORDER BY created_at DESC, rowid DESC",
        projectchats.HIDDEN).fetchall()
    out: List[str] = []
    for r in rows:
        if r["f"]:
            f = projectchats.norm(r["f"])
            if f not in out:
                out.append(f)
    return out


def recent(conn: sqlite3.Connection) -> List[str]:
    """Up to RECENT project folders, newest first, that are still there."""
    return [f for f in _folders(conn) if os.path.isdir(f)][:RECENT]


def base(conn: sqlite3.Connection) -> str:
    """Where a new project's folder goes: the newest project's parent, never your home's root."""
    home = projectchats.norm(paths.user_home())
    for f in _folders(conn):
        parent = os.path.dirname(f)
        if parent != home and os.path.isdir(parent) and not projectchats._under(parent, paths.home()):
            return parent
        break
    return projectchats.norm(paths.user_home() / DEFAULT_BASE)


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("a project needs a name")
    if "/" in name or name.startswith("."):
        raise ValueError(f"{name!r} can't be a folder name: open a folder instead")
    return name


def expand(folder: str) -> str:
    """A typed folder, `~` as api_projects reads it, relative under your home; normalised."""
    from .api_projects import _expand
    got = _expand(folder)
    if not got:
        raise ValueError("which folder?")
    return projectchats.norm(os.path.join(paths.user_home(), got))


def project_on(conn: sqlite3.Connection, folder: str) -> Optional[str]:
    """The live project whose folder is `folder` (eki for its own source), or None."""
    where = projectchats.norm(folder)
    return next((e["id"] for e in projectchats.listing(conn) if e["folder"] == where), None)


def proposal(conn: sqlite3.Connection, name: Optional[str] = None, folder: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/projectstart: the folder for `name` under the base, or `folder` as typed; whether it
    is on disk and the project already on it."""
    at = base(conn)
    where = expand(folder) if folder is not None else os.path.join(at, check_name(name or ""))
    return {"folder": where, "base": at, "recent": recent(conn), "exists": os.path.exists(where),
            "project": project_on(conn, where)}


def create(conn: sqlite3.Connection, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/projects: the project on `folder` (else the proposal for its name); one already on
    that folder comes back with `existing: true` and nothing new is made. A folder whose files another
    repo tracks comes back as {ask: {inner, offer, parent}}, nothing made, until `history` is 'anyway'."""
    name = str(body.get("name") or "").strip()
    typed = str(body.get("folder") or "").strip()
    if typed:
        where = expand(typed)
        name = name or os.path.basename(where)
    else:
        where = os.path.join(base(conn), check_name(name))
    before = {r[0] for r in conn.execute("SELECT id FROM projects")}
    pid = project_on(conn, where)
    if pid is None:
        history = body.get("history") or None
        try:
            pid = projectchats.create(conn, name, where, str(body.get("description") or ""),
                                      None if history == "anyway" else history, anyway=history == "anyway")
        except gitstart.Embedded as e:          # the one warning: Cancel, or Create anyway (history "anyway")
            return {"id": None, "ask": {"inner": True, "fit": True, "offer": str(e), "parent": e.parent,
                                        "left_out": []}}
    return {"id": pid, "folder": projectchats.folder_of(conn, pid), "existing": pid in before or pid == projectchats.SELF}


# ---- what to add first ----------------------------------------------------------------------

def _scripts(folder: Path) -> Dict[str, str]:
    try:
        got = json.loads((folder / "package.json").read_text())
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    scripts = got.get("scripts") if isinstance(got, dict) else None
    return {k: v for k, v in scripts.items() if isinstance(v, str)} if isinstance(scripts, dict) else {}


def _instructions(folder: Path, names: List[str]) -> Optional[Dict[str, str]]:
    for own in projectchats.INSTRUCTIONS:
        if (folder / own).is_file():
            return {"template": "instructions", "from": own,
                    "why": f"{own} is already here: add it as the instructions card to see and edit it"}
    readme = next((n for n in names if n.lower().startswith("readme") and (folder / n).is_file()), None)
    if readme:
        return {"template": "instructions", "from": readme,
                "why": f"{readme} says what this is: start the instructions from it"}
    return None


def _assets(folder: Path, names: List[str]) -> Optional[Dict[str, str]]:
    dirs = [n for n in names if n.lower() in MEDIA_DIRS and (folder / n).is_dir()]
    if dirs:
        return {"template": "assets", "from": dirs[0] + "/",
                "why": f"{dirs[0]}/ holds media: an assets card shows and finds what's in it"}
    media = [n for n in names if os.path.splitext(n)[1].lower() in MEDIA_EXT and (folder / n).is_file()]
    if len(media) >= MEDIA_FILES:
        return {"template": "assets", "from": f"{len(media)} media files",
                "why": f"{len(media)} media files at the top: an assets card shows and finds them"}
    return None


def suggestions(folder: Path) -> List[Dict[str, str]]:
    """The cards what is in `folder` asks for, in order: instructions, serve, workflow, assets."""
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return []
    out = [s for s in [_instructions(folder, names)] if s]
    scripts = _scripts(folder)
    run = next((k for k in ("dev", "start") if k in scripts), None)
    if run:
        out.append({"template": "serve", "from": "package.json",
                    "why": f"package.json has a {run} script (`{scripts[run]}`): a serve card starts it"})
    if "test" in scripts:
        out.append({"template": "workflow", "from": "package.json",
                    "why": f"package.json's test script (`{scripts['test']}`) is the check: "
                           "a workflow card runs it before work lands"})
    return out + [s for s in [_assets(folder, names)] if s]


def starter(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """GET /api/projects/<id>/starter: suggestions for the project's folder, without the kinds of
    card it already has. A project with no folder has none."""
    pid = projectchats.find(conn, pid)
    folder = projectchats.folder_of(conn, pid)
    root = api_cards.project_root_of(conn, pid)
    if not folder or root is None:
        return {"suggestions": []}
    have = {c["kind"] for c in api_cards.cards_of(conn, root)}
    kinds = {t["name"]: t["kind"] for t in api_cards.templates()}
    return {"suggestions": [s for s in suggestions(Path(folder))
                            if kinds.get(s["template"], s["template"]) not in have]}
