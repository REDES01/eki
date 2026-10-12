"""A project's workflow as a graph, read-only: the stages in order from its workflow card,
the cards that feed them, and the project's goals and items at each stage now.

The one JSON the Workflow page, its canvas and its stage panel share. Built on every call
from the card (eki/workflow.py, eki/components/workflow.py) and the goals/items tables, so
it holds nothing. Settings are written through the card's own edit route, not here.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import callwait, components, pathrules, projectchats, selfview, workflow

#: a check's stage title, one-line summary and what it does
_CHECKS = {
    "self": ("Self check", "the agent checks its own work",
             "The building agent verifies its own work — screenshots, its tests —\nbefore it finishes. Always on."),
    "review": ("Review", "a second agent reads it",
               "A second reader agent reads the change and says fit or not.\nA failed review sends it back."),
    "command": ("Check command", "",
                "The project's check command runs on the change (gate 1).\nRed means unfit."),
    "batch": ("Batch", "",
              "The queue checks queued changes together on top of each other\nand bisects on red."),
    "playtester": ("Playtester", "", "The project's playtester card plays the change\nand says what it found."),
    "train": ("Train", "build + drills + swap",
              "eki builds itself, runs the drills, and swaps to the new build.\nA failure rolls it back."),
}
_MERGE = {"auto": "auto — eki lands it", "pr": "PR on GitHub", "person": "person merges"}
#: item states that are over: never listed (landed is over except on eki, where train holds it)
_ENDED = ("applied", "live", "dropped")
_ALERT = ("left", "unfit", "locked", "proposed", "rolled back")


def _row(conn: sqlite3.Connection, pid: str) -> Optional[sqlite3.Row]:
    if pid == projectchats.SELF:
        return None
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise KeyError(f"no project {pid}")
    return row


def _card(root: Optional[Path]) -> Tuple[Optional[components.Component], str, Dict[str, components.Component]]:
    """The good workflow card, a broken one's error, and every good card in root/components."""
    if root is None:
        return None, "", {}
    found, errors = components.own(root)
    good = next((c for c in found.values() if c.kind == "workflow"), None)
    error = ""
    for where, why in errors:
        try:
            spec = json.loads((Path(where) / "component.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(spec, dict) and spec.get("kind") == "workflow" and Path(where).parent == root / "components":
            error = why
            break
    return good, ("" if good else error), found


def _values(spec: dict, eki: bool) -> Dict[str, Any]:
    """The spec with every stage setting filled in as the engine reads it."""
    from . import targets
    from .components import workflow as kind
    t = spec.get("target")
    checks = spec.get("checks") if isinstance(spec.get("checks"), list) else ["self"]
    am = spec.get("after_merge")
    cmd, batch = spec.get("command"), spec.get("batch_command")
    try:
        rules = pathrules.parse(cmd) if isinstance(cmd, list) and not eki else ()
    except ValueError:
        rules = ()
    cmd = (cmd if isinstance(cmd, str) else "") or ("bin/check" if eki else "")
    batch = batch if isinstance(batch, str) and batch.strip() and not eki else cmd
    out = {"router": spec.get("router", kind.CHOICE_DEFAULTS["router"]), "target": targets.key(t) if t else "",
           "plan": spec.get("plan", kind.CHOICE_DEFAULTS["plan"]), "parallel": spec.get("parallel", kind.PARALLEL),
           "checks": [c for c in checks if isinstance(c, str)] or ["self"],
           "command": cmd, "rules": [{"name": r.name, "paths": list(r.paths), "run": r.run} for r in rules],
           "batch_command": batch,
           "merge": spec.get("merge", kind.CHOICE_DEFAULTS["merge"]),
           "playtester": spec.get("playtester") or kind.PLAYTESTER,
           "after_merge": am.strip() if isinstance(am, str) else ""}
    wf = workflow.Workflow(router=out["router"], target=out["target"], plan=out["plan"], parallel=out["parallel"],
                           checks=tuple(out["checks"]), command=cmd, merge=out["merge"],
                           playtester=out["playtester"], card="", rules=rules, batch_command=batch)
    out["each"], out["together"] = workflow.each_said(wf), workflow.together_said(wf)
    return out


def line(v: Dict[str, Any]) -> str:
    """'plan split · 4 parallel · checks: self · merge auto', the router said first when off."""
    head = f"router off → {v['target']} · " if v["router"] == "off" else ""
    return (f"{head}plan {v['plan']} · {v['parallel']} parallel · checks: {', '.join(v['checks'])}"
            f" · merge {v['merge']}")


def _stage(sid: str, title: str, summary: str, what: str, on: bool, keys: List[str],
           locked: bool = False) -> Dict[str, Any]:
    return {"id": sid, "title": title, "summary": summary, "what": what, "on": on, "locked": locked,
            "keys": keys, "count": 0, "alert": False, "items": []}


def stages(v: Dict[str, Any], eki: bool) -> List[Dict[str, Any]]:
    """The stages in the order they run, each on or off; no items yet."""
    router_on = v["router"] == "on"
    single = v["plan"] == "single"
    n = v["parallel"]
    out = [_stage("request", "Request in", "a goal asked in the project",
                  "A request comes in: a goal asked in this project.\nIt runs through the stages to the right.",
                  True, []),
           _stage("router", "Router", "rules, then a small model" if router_on else f"straight to {v['target']}",
                  "Picks who does the work: rules first, a small model for the rest,\n"
                  "among the providers and the project's cards. Off: everything goes to one target.",
                  router_on, ["router", "target"]),
           _stage("plan", "Single item" if single else "Planner",
                  "the whole request is one item" if single else "splits the request into items",
                  "A planner turns a request into items with write-sets, built in parallel.\n"
                  "Single: the whole request is one item for one agent.", True, ["plan"]),
           _stage("build", "Build", "1 at a time" if n == 1 else f"{n} in parallel",
                  "Each item is built by an agent in a worktree of its own.\n"
                  "Parallel: how many items build at once.", True, ["parallel"])]
    for c in workflow.CHECKS:
        if c == "train" and not eki:
            continue
        title, summary, what = _CHECKS[c]
        if c == "command":
            summary = v["each"]
        elif c == "batch":
            summary = v["together"]
        elif c == "playtester":
            summary = f"{v['playtester']} plays it"
        keys = ["checks"] + {"command": ["command"], "batch": ["batch_command"],
                             "playtester": ["playtester"]}.get(c, [])
        out.append(_stage(c, title, summary, what, c in v["checks"], keys,
                          locked=eki and c in workflow.LOCKED))
    out.append(_stage("merge", "Merge", _MERGE.get(v["merge"], v["merge"]),
                      "How a fit change lands: eki merges it, a pull request on GitHub,\n"
                      "or it waits for the person to merge.", True, ["merge"]))
    out.append(_stage("after_merge", "After-merge", v["after_merge"] or "nothing runs after merge",
                      "A shell command eki runs in the person's folder right after it lands a change\n"
                      "(e.g. npm run build). It never blocks or undoes the landing.",
                      bool(v["after_merge"]), ["after_merge"]))
    return out


def feeds(found: Dict[str, components.Component], v: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The cards that feed a stage, as side nodes; the check command ("Each change: …") last."""
    to = {"knowledge": ["plan", "build"], "instructions": ["plan", "build"], "skills": ["build"],
          "mcp": ["build"], "serve": ["build", "playtester"]}
    out = []
    for name in sorted(found):
        c = found[name]
        stages_ = to.get(c.kind) or (["playtester"] if c.kind == "agent" and name == v["playtester"] else None)
        if stages_:
            out.append({"card": name, "kind": c.kind, "label": name, "to": stages_,
                        "note": "picked per item" if c.kind == "knowledge" else ""})
    if v["command"] or v["rules"]:
        out.append({"card": "", "kind": "command", "label": v["each"], "to": ["command"], "note": ""})
    return out


def _goal(conn, g) -> Dict[str, Any]:
    thread = selfview._thread(conn, g["draft_run"]) or selfview._thread(conn, g["plan_run"]) or g["thread_id"]
    return {"id": g["id"], "kind": "goal", "title": ((g["text"] or "").splitlines() or [""])[0],
            "state": g["state"], "since": g["created_at"], "where": "", "note": g["error"] or "",
            "thread": thread or "", "goal": g["id"]}


def _item(conn, it) -> Dict[str, Any]:
    return {"id": it["id"], "kind": "item", "title": it["title"], "state": it["state"], "since": it["updated_at"],
            "where": selfview.where(it), "note": selfview.note(conn, it),
            "thread": selfview._thread(conn, it["run_id"]) or "", "goal": it["goal_id"]}


def _place(it, checks: List[str], eki: bool) -> Optional[str]:
    """The stage an item is at now; None when it's over."""
    s = it["state"]
    if it["pr_state"] == "open":
        return "merge"
    if s == callwait.STATE:
        s = "building" if (callwait.waits_of(it) or {}).get("stage") == "building" else "resolving"
    if s in ("waiting", "building", "left"):
        return "build"
    if s in ("judging", "reviewing", "unfit"):
        if s != "unfit" and "review" in checks:
            return "review"
        return "command" if s != "reviewing" and "command" in checks else "self"
    if s in ("queued", "resolving", "rechecking"):
        return "batch" if "batch" in checks else "merge"
    if s in ("landed", "rolled back") and eki:
        return "train"
    if s in ("proposed", "locked", "rolled back"):
        return "merge"
    return None


def place(conn: sqlite3.Connection, pid: str, eki: bool, out: List[Dict[str, Any]], checks: List[str]) -> None:
    """Fill each stage's items, count and alert from the project's goals and items."""
    by = {s["id"]: s for s in out}
    own, args = ("g.project IS NULL", ()) if eki else ("g.project = ?", (pid,))
    for g in conn.execute(f"SELECT * FROM goals g WHERE {own} AND g.state IN ('drafting','planning','failed') "
                          "ORDER BY g.created_at DESC", args).fetchall():
        by["plan"]["items"].append(_goal(conn, g))
        by["plan"]["alert"] |= g["state"] == "failed"
    ended = _ENDED + (() if eki else ("landed",))
    marks = ",".join("?" * len(ended))
    rows = conn.execute(f"SELECT i.* FROM items i JOIN goals g ON g.id = i.goal_id WHERE {own} "
                        f"AND (i.state NOT IN ({marks}) OR i.pr_state = 'open') ORDER BY i.updated_at DESC",
                        args + ended).fetchall()
    for it in rows:
        sid = _place(it, checks, eki)
        if sid in by:
            by[sid]["items"].append(_item(conn, it))
            by[sid]["alert"] |= it["state"] in _ALERT
    for s in out:
        s["count"] = len(s["items"])


def graph(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """The project's workflow graph; KeyError for an unknown project."""
    row = _row(conn, pid)
    eki = pid == projectchats.SELF
    root = components.project_root(projectchats.folder_of(conn, pid), conn)
    good, error, found = _card(root)
    spec = dict(good.spec) if good else workflow.from_settings(conn, row)
    v = _values(spec, eki)
    out = stages(v, eki)
    place(conn, pid, eki, out, v["checks"])
    return {"project": pid, "name": "eki" if eki else row["name"], "card": good.name if good else "",
            "error": error, "editable": bool(good and not good.card and root is not None), "eki": eki,
            "line": line(v), "spec": spec, "choices": components.choices("workflow"),
            "choice_defaults": components.choice_defaults("workflow"),
            "choice_help": components.choice_help("workflow"),
            "locked": list(workflow.LOCKED) if eki else [], "stages": out, "feeds": feeds(found, v)}


def get(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    return graph(conn, m.group(1))


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/projects/([\w.-]+)/workflow", get),
]
