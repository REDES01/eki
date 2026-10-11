"""A project's workflow card, read: the one place the engine learns which stages a project runs.

The card is a `workflow` component in the project's components/ (eki/components/workflow.py
checks it); at most one per project. No card, or a broken one, is None here and the engine
keeps today's behaviour for that kind of project. `from_settings` is what today's settings
mean as card values, written once when a card is added (one source of truth from then on).
"""
from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

#: the checks a change can go through, in the order they run
CHECKS = ("self", "review", "command", "batch", "playtester", "train")
#: can't be turned off on eki's own project
LOCKED = ("batch", "train")


@dataclass(frozen=True)
class Workflow:
    router: str
    target: str            # targets.key of the card's target ('' when it has none)
    plan: str
    parallel: int
    checks: Tuple[str, ...]
    command: str
    merge: str
    playtester: str
    card: str              # the card's folder
    after_merge: str = ""  # shell command run in the person's folder after eki lands ('' when none)


def is_eki(root) -> bool:
    """`root` is eki's own checkout: the source, the integration repo, or a worktree of either."""
    from . import builds, paths
    try:
        here = Path(root).expanduser().resolve()
        own = {builds.source().resolve(), (paths.home() / "self" / "repo").resolve()}
    except OSError:
        return False
    if here in own:
        return True
    try:
        out = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    common = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    return common is not None and common.name == ".git" and common.parent.resolve() in own


def _of(comp) -> Workflow:
    from . import targets
    from .components import workflow as kind
    s = comp.spec
    t = s.get("target")
    return Workflow(router=s.get("router", kind.CHOICE_DEFAULTS["router"]),
                    target=targets.key(t) if t else "",
                    plan=s.get("plan", kind.CHOICE_DEFAULTS["plan"]),
                    parallel=s.get("parallel", kind.PARALLEL),
                    checks=tuple(s.get("checks") or ("self",)),
                    command=s.get("command") or "",
                    merge=s.get("merge", kind.CHOICE_DEFAULTS["merge"]),
                    playtester=s.get("playtester") or kind.PLAYTESTER,
                    card=str(comp.folder),
                    after_merge=s.get("after_merge") if isinstance(s.get("after_merge"), str) else "")


def of_root(root: Optional[Path]) -> Optional[Workflow]:
    """The good workflow card in root/components, None when none or broken; never raises."""
    if root is None:
        return None
    try:
        from . import components
        found, _ = components.own(Path(root))
        for comp in found.values():
            if comp.kind == "workflow" and comp.card is None:
                return _of(comp)
    except Exception:  # reading a card must never break the engine
        return None
    return None


def of_project(conn: Optional[sqlite3.Connection], project: Optional[sqlite3.Row]) -> Optional[Workflow]:
    """The card of a project row; None is eki itself (its source checkout)."""
    try:
        from . import builds, components
        if project is None:
            return of_root(builds.source())
        keys = project.keys()
        where = (project["folder"] if "folder" in keys else None) or project["path"]
        return of_root(components.project_root(where, conn))
    except Exception:
        return None


def of_folder(conn: Optional[sqlite3.Connection], folder: Optional[str]) -> Optional[Workflow]:
    """The card of the project a run's cwd belongs to; None outside any project."""
    try:
        from . import components
        root = components.project_root(folder, conn)
        if root is None or not components.is_project(root, conn):
            return None
        return of_root(root)
    except Exception:
        return None


def has(wf: Optional[Workflow], check: str) -> bool:
    return wf is not None and check in wf.checks


def autonomy(wf: Workflow) -> str:
    """projects.AUTONOMY for the card: eki lands it under merge auto, else it waits."""
    return "apply" if wf.merge == "auto" else "propose"


def said(wf: Workflow) -> str:
    """The one board line naming what runs, so what is skipped shows: 'checks: self only'."""
    checks = "checks: self only" if wf.checks == ("self",) else f"checks: {', '.join(wf.checks)}"
    head = f"router off → {wf.target}; " if wf.router == "off" else ""
    head += "plan single; " if wf.plan == "single" else ""
    return head + checks


def from_settings(conn: Optional[sqlite3.Connection], project: Optional[sqlite3.Row]) -> dict:
    """The card values today's settings mean, for the card added to `project` (None: eki itself)."""
    from . import projects, selfwork
    st = selfwork.settings()
    checks = ["self"] + (["review"] if st.get("review", True) else [])
    out = {"router": "on", "plan": "split", "parallel": st.get("parallel", 4)}
    if project is None:
        checks += ["batch", "train"]
        out["merge"] = "auto" if st.get("autonomy") == "apply" else "person"
    else:
        cmd = projects.legacy_check(project)
        if cmd:
            checks += ["command", "batch"]
            out["command"] = cmd
        if "repo" in project.keys() and project["repo"]:
            out["merge"] = "pr"
        else:
            out["merge"] = "auto" if projects.legacy_autonomy(project) == "apply" else "person"
    out["checks"] = checks
    return out
