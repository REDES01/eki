"""Connectors to discover: MCP servers that are one click (and maybe a key) away.

Each entry says what it needs — env keys the person types, or a folder.
eki reads no token: a GitHub token is typed like any other key.
`install` hands the catalog spec to eki/mcp.py under the entry's id.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import mcp

FOLDER = "<folder>"      # an arg the person fills with a folder


def _npx(*args: str) -> Dict[str, Any]:
    return {"command": "npx", "args": ["-y", *args]}


def _uvx(*args: str) -> Dict[str, Any]:
    return {"command": "uvx", "args": list(args)}


CATALOG: List[Dict[str, Any]] = [
    {"id": "brave-search", "title": "Brave Search", "blurb": "Web and local search through Brave's API.",
     "needs": ["BRAVE_API_KEY"], "spec": _npx("@modelcontextprotocol/server-brave-search")},
    {"id": "exa", "title": "Exa", "blurb": "Search the web with Exa, made for AI agents.",
     "needs": ["EXA_API_KEY"], "spec": _npx("exa-mcp-server")},
    {"id": "tavily", "title": "Tavily", "blurb": "Search and extract pages with Tavily.",
     "needs": ["TAVILY_API_KEY"], "spec": _npx("tavily-mcp")},
    {"id": "perplexity", "title": "Perplexity", "blurb": "Ask Perplexity's Sonar for answers with sources.",
     "needs": ["PERPLEXITY_API_KEY"], "spec": _npx("server-perplexity-ask")},
    {"id": "fetch", "title": "Fetch", "blurb": "Fetch a web page and read it as Markdown.",
     "needs": [], "spec": _uvx("mcp-server-fetch")},
    {"id": "playwright", "title": "Playwright", "blurb": "Drive a browser: open pages, click, fill and read.",
     "needs": [], "spec": _npx("@playwright/mcp@latest")},
    {"id": "github", "title": "GitHub", "blurb": "Issues, pull requests and code on GitHub, with a token you paste.",
     "needs": ["GITHUB_PERSONAL_ACCESS_TOKEN"], "spec": _npx("@modelcontextprotocol/server-github")},
    {"id": "filesystem", "title": "Filesystem", "blurb": "Read and write files in one folder you choose.",
     "needs": [], "folder": True, "spec": _npx("@modelcontextprotocol/server-filesystem", FOLDER)},
    {"id": "blender", "title": "Blender", "blurb": "Model and render in a running Blender.",
     "needs": [], "spec": _uvx("blender-mcp")},
]


def get(cid: str) -> Dict[str, Any]:
    for c in CATALOG:
        if c["id"] == cid:
            return c
    raise KeyError(f"no connector {cid}")


def added(cid: str) -> bool:
    return any(r["name"] == cid for r in mcp.all_servers())


def listing() -> List[Dict[str, Any]]:
    """The catalog as the page shows it: what each needs, and whether it's added."""
    names = {r["name"] for r in mcp.all_servers()}
    return [{**{k: v for k, v in c.items() if k != "spec"}, "folder": bool(c.get("folder")),
             "command": " ".join([c["spec"]["command"], *c["spec"]["args"]]), "added": c["id"] in names}
            for c in CATALOG]


def install(cid: str, env: Optional[Dict[str, str]] = None, folder: Optional[str] = None) -> None:
    c = get(cid)
    env = {k: (v or "").strip() for k, v in (env or {}).items()}
    missing = [k for k in c["needs"] if not env.get(k)]
    if missing:
        raise ValueError(f"{c['title']} needs {', '.join(missing)}")
    args = list(c["spec"]["args"])
    if FOLDER in args:
        folder = (folder or "").strip()
        if not folder:
            raise ValueError(f"{c['title']} needs a folder")
        args = [folder if a == FOLDER else a for a in args]
    mcp.add(cid, command=c["spec"]["command"], args=args, env={k: env[k] for k in c["needs"]} or None)
