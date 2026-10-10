"""The window's Customize page over HTTP: its routes for `apiroutes`.

Skills (the store in eki/skills.py, and what eki/skillsfind.py finds on
GitHub) and connectors (the MCP registry in eki/mcp.py, and the catalog in
eki/connectors.py). Env values never leave here whole: every spec goes out
through `mcp.public`.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import unquote

from . import connectors, mcp, paths, skills, skillsfind

_NAME = r"([^/]+)"


def _name(m: Any) -> str:
    name = unquote(m.group(1))
    if not name or "/" in name or name.startswith("."):
        raise KeyError(f"no such name {name}")
    return name


# ---- skills ---------------------------------------------------------------------------

def skill_list(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return {"skills": skills.all_skills()}


def skill_discover(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return skillsfind.discover(refresh=q.get("refresh") == "1")


def skill_read(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    name = _name(m)
    md = paths.skills() / name / "SKILL.md"
    if not md.is_file():
        raise KeyError(f"no skill {name}")
    return {"name": name, "text": md.read_text(errors="replace")}


def skill_add(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return {"name": skills.add(str(body.get("source") or ""))}


def skill_remove(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    name = _name(m)
    if not skills.remove(name):
        raise KeyError(f"no skill {name}")
    return {"removed": name}


# ---- connectors -----------------------------------------------------------------------

def connector_list(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    progs = mcp.programs(conn)
    return {"connectors": [{"name": r["name"], "spec": mcp.public(r["name"], r["spec"]), "on": r["on"],
                            "programs": progs.get(r["name"], {"claude": False, "codex": False})}
                           for r in mcp.all_servers()]}


def connector_discover(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return {"catalog": connectors.listing()}


def _env(body: dict) -> Dict[str, str]:
    env = body.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("env must be an object of KEY: value")
    return {str(k).strip(): str(v or "") for k, v in env.items() if str(k).strip()}


def connector_add(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    args = body.get("args") or []
    if isinstance(args, str):
        args = args.split()
    if not isinstance(args, list):
        raise ValueError("args must be a list")
    name = str(body.get("name") or "").strip()
    if "/" in name:
        raise ValueError("a connector's name can't hold '/'")
    mcp.add(name, command=(str(body.get("command") or "").strip() or None), args=[str(a) for a in args],
            url=(str(body.get("url") or "").strip() or None), env=_env(body))
    return {"name": name}


def connector_act(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    name, act = _name(m), m.group(2)
    if act == "remove":
        if not mcp.remove(name):
            raise KeyError(f"no MCP server {name}")
    else:
        mcp.set_on(name, act == "on")
    return {"name": name, "done": act}


def connector_install(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    cid = _name(m)
    connectors.install(cid, _env(body), body.get("folder"))
    return {"name": cid, "added": connectors.added(cid)}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/skills", skill_list),
    ("GET", r"/api/skills/discover", skill_discover),
    ("GET", rf"/api/skills/{_NAME}", skill_read),
    ("POST", r"/api/skills", skill_add),
    ("POST", rf"/api/skills/{_NAME}/remove", skill_remove),
    ("GET", r"/api/connectors", connector_list),
    ("GET", r"/api/connectors/discover", connector_discover),
    ("POST", r"/api/connectors", connector_add),
    ("POST", rf"/api/connectors/discover/{_NAME}", connector_install),
    ("POST", rf"/api/connectors/{_NAME}/(on|off|remove)", connector_act),
]
