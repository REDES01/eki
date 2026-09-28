"""What the digest says about pull requests (docs/design.md, "GitHub is the front").

A project on the GitHub path hands its work back as PRs (eki/prs.py); an item
whose PR is open waits for you on GitHub, not in `eki goal`. digest.py asks
here for the one line such an item gets, for the long page's per-project
lines, and for the count the short page's closing line names. Reads the items
table only; holds no state.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

from . import projects


def _first_line(text: Optional[str]) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def happened(it: sqlite3.Row, groups: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """(group, what happened) for a project item that went through a PR, else None."""
    url, pr_state, state = it["pr"], it["pr_state"], it["state"]
    if not url:
        return None
    if state == "applied" and pr_state == "merged":
        return groups[0], f"merged on GitHub ({url})"
    if state == "dropped" and pr_state == "closed":
        return groups[1], f"PR closed: {_first_line(it['error']) or 'no reason given'}"
    if state == "proposed":
        return groups[2], f"PR open: {url}"
    return None


def total(conn: sqlite3.Connection) -> int:
    """How many PRs wait for you, over every project."""
    return sum(projects.open_prs(conn).values())


def waiting(conn: sqlite3.Connection) -> List[str]:
    """One line per project with PRs open: `- <name>: N PRs open — <url>, <url>`."""
    urls: Dict[str, List[str]] = {}
    for r in conn.execute("SELECT p.name, i.pr FROM items i JOIN goals g ON g.id=i.goal_id"
                          " JOIN projects p ON p.id=g.project"
                          " WHERE i.state='proposed' AND i.pr_state='open'"
                          " ORDER BY p.name, i.created_at, i.id"):
        urls.setdefault(r["name"], []).append(r["pr"])
    return [f"- {name}: {len(got)} PRs open — {', '.join(got)}" for name, got in urls.items()]
