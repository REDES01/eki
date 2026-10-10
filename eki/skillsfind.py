"""Skills to discover: the skill folders of a few public repositories.

Read through `gh api` (github.contents), never over HTTP by eki itself, and
cached a day in `EKI_HOME/cache/discover-skills.json`. Installing one is
`skills.add("<repo>#<path>")`. Without a working gh the list is empty and
`why` says so.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Dict, List, Optional

from . import github, paths, skills

#: (repo, folder holding one folder per skill)
SOURCES = ["anthropics/skills"]
FOLDER = "skills"
DAY = 24 * 3600


def _cache():
    d = paths.home() / "cache"
    d.mkdir(exist_ok=True)
    return d / "discover-skills.json"


def _read_cache(now: float) -> Optional[List[Dict]]:
    try:
        got = json.loads(_cache().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or now - float(got.get("at") or 0) > DAY:
        return None
    return got.get("skills")


def _text(entry: dict) -> str:
    raw = entry.get("content") or ""
    try:
        return base64.b64decode(raw).decode("utf-8", "replace")
    except ValueError:
        return ""


def _fetch() -> List[Dict]:
    found = []
    for repo in SOURCES:
        for d in github.contents(repo, FOLDER):
            if d.get("type") != "dir":
                continue
            path = d.get("path") or f"{FOLDER}/{d.get('name')}"
            try:
                md = github.contents(repo, f"{path}/SKILL.md")
            except github.GhError:
                continue                              # a folder without SKILL.md isn't a skill
            meta = skills._front(_text(md[0])) if md else {}
            found.append({"name": meta.get("name") or d.get("name"), "description": meta.get("description", ""),
                          "repo": repo, "path": path})
    return found


def discover(refresh: bool = False) -> Dict:
    """{'skills': [{name, description, repo, path, installed}], 'why': str|None}."""
    now = time.time()
    found = None if refresh else _read_cache(now)
    if found is None:
        try:
            found = _fetch()
        except github.GhError as e:
            return {"skills": [], "why": f"can't list skills from GitHub: {e}"}
        _cache().write_text(json.dumps({"at": now, "skills": found}))
    have = {s["name"] for s in skills.all_skills()}
    return {"skills": [{**s, "installed": skills.slug(s["name"]) in have} for s in found], "why": None}
