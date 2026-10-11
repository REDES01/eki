"""Adding a card to a project (a template or a pool card) and removing one: routes for apiroutes.

Both need the project's folder (api_cards.writable_root): with none, a 400 saying why. A card is
added under a free name, never over one; removing moves it to the Trash. Nothing is committed.
A `workflow` card is one per project, and starts as today's behaviour: the project's settings
(check command, autonomy, review) are written into it once and read from the card from then on.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import api_cards
from .components import cards


def workflow_card(base: Path) -> str:
    """The name of the workflow card already in `base` (a components/ folder), or ''."""
    try:
        folders = sorted(d for d in Path(base).iterdir() if d.is_dir())
    except OSError:
        return ""
    for d in folders:
        try:
            spec = json.loads((d / "component.json").read_text())
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(spec, dict) and spec.get("kind") == "workflow":
            return d.name
    return ""


def is_workflow(template: str) -> bool:
    """`template` copies a workflow card."""
    from . import components
    try:
        spec = json.loads((components.templates_dir() / template / "component.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(spec, dict) and spec.get("kind") == "workflow"


def project_of_root(conn: sqlite3.Connection, root: Path) -> Optional[sqlite3.Row]:
    """The project row whose folder or path is `root`; None for eki's own folder (or none at all)."""
    from . import workflow
    if workflow.is_eki(root):
        return None
    where = str(Path(root).expanduser().resolve())
    return conn.execute("SELECT * FROM projects WHERE (folder=? OR path=?) AND COALESCE(state, 'on') "
                        "NOT IN ('deleting', 'deleted') ORDER BY created_at DESC LIMIT 1",
                        (where, where)).fetchone()


def _carried(project: Optional[sqlite3.Row], values: dict) -> str:
    """What came over from the old settings, said once: 'from the project: check `make test`, …'."""
    from . import projects
    parts = []
    if values.get("command"):
        parts.append(f"check `{values['command']}`")
    parts.append("review " + ("on" if "review" in values["checks"] else "off"))
    if project is None:
        parts.append(f"merge {values['merge']}")
        return "from eki's settings: " + ", ".join(parts)
    if "repo" in project.keys() and project["repo"]:
        parts.append(f"GitHub {project['repo']} → merge pr")
    else:
        parts.append(f"autonomy {projects.legacy_autonomy(project)} → merge {values['merge']}")
    return "from the project: " + ", ".join(parts)


_ROOT: Any = object()   # add_workflow's default: the project is the root's


def add_workflow(conn: sqlite3.Connection, root: Path, template: str = "workflow",
                 name: Optional[str] = None, project: Any = _ROOT) -> Tuple[Path, List[str]]:
    """Add the project's one workflow card, holding what its settings mean today (workflow.from_settings).
    Refused, nothing written, when it has one; a card whose values don't validate is taken back."""
    from . import api_cardedit, workflow
    base = Path(root) / "components"
    other = workflow_card(base)
    if other:
        raise ValueError(f"a project has one workflow card: {other} is it")
    if project is _ROOT:
        project = project_of_root(conn, root)
    values = workflow.from_settings(conn, project)
    folder, said = cards.add_template(base, template, name)
    try:
        api_cardedit.save_settings(folder, values)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)   # the copy just made: nothing is left half-written
        raise
    return folder, said + [_carried(project, values)]


def add_card(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid = m.group(1)
    base = api_cards.writable_root(conn, pid) / "components"
    template, pool = body.get("template"), body.get("pool")
    if bool(template) == bool(pool):
        raise ValueError('send {"template": name} or {"pool": name}')
    if template and is_workflow(str(template)):
        from . import projects
        folder, said = add_workflow(conn, base.parent, str(template), project=projects.get(conn, pid))
    elif template:
        folder, said = cards.add_template(base, str(template))
    else:
        folder, said = cards.attach(base, str(pool)), []
    return {"card": api_cards.card_of(conn, pid, folder.name), "said": said}


def remove_card(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    base = api_cards.writable_root(conn, pid) / "components"
    return {"removed": name, "trash": str(cards.remove(base, name))}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards", add_card),
    ("DELETE", r"/api/projects/([\w.-]+)/cards/([\w.-]+)", remove_card),
]
