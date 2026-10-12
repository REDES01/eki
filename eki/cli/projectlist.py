"""`eki project` with no key lists every project; with a key it shows one; `--new` makes one."""
from __future__ import annotations

import shlex
from typing import List, Optional

from .. import gitstart, projectchats, projects
from .common import err


def counts(e: dict) -> str:
    return f"{e['chats']} chats · {e['goals']} goals · {e['prs']} PRs"


def lines(entries: List[dict]) -> List[str]:
    """One line per project: name, folder, repo, counts — columns lined up."""
    rows = [(e["name"], e["folder"] or "no folder", e["repo"] or "-", counts(e)) for e in entries]
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    return ["  ".join(r[i].ljust(widths[i]) for i in range(3)) + "  " + r[3] for r in rows]


def listing(c) -> int:
    for line in lines(projectchats.listing(c, scan=True)):
        print(line)
    return 0


def entry_of(c, pid: str) -> dict:
    """The project as listed — a retired row a live one shadows isn't listed, so built from its row."""
    for e in projectchats.listing(c, scan=True):
        if e["id"] == pid:
            return e
    return projectchats.entry(c, projects.get(c, pid))


def details(e: dict) -> List[str]:
    folder = e["folder"] or "no folder yet: eki project <key> --folder PATH"
    if e["folder"] and not e["folder_ok"]:
        folder += " (missing)"
    return [f"  name: {e['name']} ({e['id']}, {e['kind']})",
            f"  folder: {folder}",
            f"  description: {e.get('description') or '-'}",
            f"  repo: {e['repo'] or '-'}",
            f"  {counts(e)}",
            f"  instructions: {e['instructions'] or 'none'}",
            f"  background: {e['background'] or 'none'}"]


def resolve(c, key: str) -> Optional[str]:
    """A project id from the key, or None after saying why."""
    try:
        return projectchats.find(c, key)
    except ValueError as e:
        err(f"eki: {e}")
        return None
    except KeyError:
        pass
    if "/" not in key:
        err(f"eki: no project named {key}, and it isn't OWNER/REPO: `eki project` lists them")
        return None
    p = projects.by_repo(c, key)
    if p is not None and p["state"] == "dropped":
        err(f"eki: {key} is dropped: a new issue labelled eki brings it back")
    else:
        err(f"eki: no project {key}: label an issue eki there, or `eki goal add <its folder> \"…\"`")
    return None


def new(c, name: str, folder: Optional[str], description: Optional[str] = None, yes: bool = False) -> int:
    try:
        pid = projectchats.create(c, name, folder, description, anyway=yes)
    except gitstart.Embedded as e:
        err(f"eki: {e}\n  to create it anyway: eki project --new {shlex.quote(name)} --folder "
            f"{shlex.quote(e.folder)}"
            + (f" --description {shlex.quote(description)}" if description else "") + " --yes")
        return 1
    except ValueError as e:
        err(f"eki: {e}")
        return 1
    print(f"{name}: project {pid} in {projectchats.folder_of(c, pid)}")
    return 0


def set_folder(c, pid: str, folder: str) -> int:
    try:
        where = projectchats.set_folder(c, pid, folder)
    except (ValueError, KeyError) as e:
        err(f"eki: {e.args[0] if e.args else e}")
        return 1
    print(f"{entry_of(c, pid)['name']}: folder {where}")
    return 0
