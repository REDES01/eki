"""Final acceptance: a goal with checks is done only when an independent judge says so.

When every non-dropped item of a `planned` goal with checks is landed, live or
applied, the goal goes to `accepting` and a judge run (scope `final`) judges
every check in the read-only worktree `accept-<goal>`, at integration main for
eki or at the project's branch tip; a card with `playtester` adds a command run
(scope `play`) that pipes the playtester's answer into `eki verdict --lines`.
The judge never runs on a provider that built an item of the goal.

Once the round's runs have ended, each check's result is the worst of the
round's verdicts (failed > uncertain > passed; no verdict is uncertain):

    all passed            → accepted (the only done state of a goal with checks)
    any failed, first time → one fix item per failed check; planned, next round
    any failed again       → judgment
    uncertain, first time  → one `second` run on another backend (roles.pick_other)
    uncertain after that   → judgment, with the needs-you notice

`goals.accept` holds {round, runs, fixes, by?}. Every move reads the tables
and rechecks the goal's state in its own transaction, so a restart loses
nothing: a goal left in `accepting` concludes on the next tick.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from . import db, judgebrief, judgerun, store, verdicts, workflow, workflowgates, workspace
from .workspace import WorkspaceError

DONE = ("landed", "live", "applied")
NO_VERDICT = "the judge filed no verdict"
TITLE_CUT = 60


# ---- reading ----------------------------------------------------------------------------------

def state_of(g: sqlite3.Row) -> Dict[str, Any]:
    """goals.accept as a dict, {round: 1, runs: [], fixes: []} when not set."""
    try:
        got = json.loads(g["accept"] or "{}")
    except (ValueError, TypeError):
        got = {}
    got = got if isinstance(got, dict) else {}
    got.setdefault("round", 1)
    got.setdefault("runs", [])
    got.setdefault("fixes", [])
    return got


def _goal(conn: sqlite3.Connection, gid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def ready(conn: sqlite3.Connection, g: sqlite3.Row) -> bool:
    """Planned, has checks, at least one live item, and every live item is landed, live or applied."""
    if g["state"] != "planned" or not verdicts.checks_of(conn, g["id"]):
        return False
    states = [r[0] for r in conn.execute("SELECT state FROM items WHERE goal_id=? AND state!='dropped'",
                                         (g["id"],))]
    return bool(states) and all(s in DONE for s in states)


def builders(conn: sqlite3.Connection, gid: str) -> List[str]:
    """Every provider that ran in an item's thread of the goal (its builds and fixes)."""
    return sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT r.provider FROM runs r JOIN items i ON r.thread_id=i.thread_id "
        "WHERE i.goal_id=? AND COALESCE(r.provider, '') NOT IN ('', 'command')", (gid,))})


def _judgings(conn: sqlite3.Connection, gid: str, rnd: int) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM judgings WHERE goal_id=? AND item_id IS NULL AND round=? "
                        "ORDER BY created_at, rowid", (gid, rnd)).fetchall()


def results(conn: sqlite3.Connection, gid: str, rnd: int) -> Dict[str, Dict[str, str]]:
    """{check id: {verdict, evidence, limitation, suggestion}} for round `rnd`: the worst of the
    final and play runs' verdicts, and where a second run judged a check, the worst of its."""
    js = _judgings(conn, gid, rnd)
    first = [j["run_id"] for j in js if j["scope"] != "second"]
    second = [j for j in js if j["scope"] == "second"]
    out = {}
    for c in verdicts.checks_of(conn, gid):
        runs = first
        again = [j["run_id"] for j in second if c["id"] in verdicts.listed(j)]
        if again:
            runs = again
        got = verdicts.worst(v for r in runs for v in verdicts.outcome(conn, r).values() if v["check_id"] == c["id"])
        if got is None:
            out[c["id"]] = {"verdict": "uncertain", "evidence": "", "limitation": NO_VERDICT,
                            "suggestion": "judge this check again"}
        else:
            out[c["id"]] = {k: got[k] for k in ("verdict", "evidence", "limitation", "suggestion")}
    return out


# ---- the tick ---------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    """Start acceptance for goals that are ready; conclude those whose round has ended."""
    said: List[str] = []
    for g in conn.execute("SELECT g.* FROM goals g WHERE g.state='planned' "
                          "AND EXISTS (SELECT 1 FROM checks c WHERE c.goal_id=g.id)").fetchall():
        if ready(conn, g):
            said += start(conn, g)
    for g in conn.execute("SELECT * FROM goals WHERE state='accepting'").fetchall():
        said += conclude(conn, g)
    return said


def _where(conn: sqlite3.Connection, g: sqlite3.Row) -> Tuple[str, str, Any]:
    """(repo, ref, card) the goal is judged at: integration main for eki, the branch tip for a project."""
    from . import integration, projects, projectwork
    project = projectwork.project_of(conn, g)
    if project is None:
        return str(integration.repo()), integration.main(), workflowgates.own()
    return str(project["path"]), projects.base(project), workflow.of_project(conn, project)


def _tree(gid: str) -> str:
    return f"accept-{gid}"


def start(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    """Move a ready goal to accepting and start its round's judge (and play) runs."""
    gid = g["id"]
    try:
        repo, ref, wf = _where(conn, g)
        workspace.remove(repo, _tree(gid), delete_branch=True)      # a round before: judged at an older head
    except (KeyError, WorkspaceError, OSError, RuntimeError) as e:
        return _wait(conn, gid, f"acceptance waits: {e}")
    acc = state_of(g)
    rnd = int(acc["round"])
    checks = [(c["id"], c["text"]) for c in verdicts.checks_of(conn, gid)]
    ids = [c for c, _ in checks]
    avoid = builders(conn, gid)
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None or not ready(conn, cur):
            return []
        rid, why = judgerun.start(conn, goal_id=gid, item_id=None, scope="final", checks=ids, round=rnd,
                                  avoid=avoid, tree=_tree(gid), repo=repo, ref=ref,
                                  prompt=judgebrief.brief(cur["text"], checks, "final"), background=True)
        if rid is None:
            return _wait(conn, gid, f"acceptance waits: {why}")
        runs = [rid]
        if workflow.has(wf, "playtester"):
            runs.append(_play(conn, gid, wf, cur, checks, rnd, avoid, rid))
        acc.update(round=rnd, runs=runs)
        conn.execute("UPDATE goals SET state='accepting', accept=?, error=NULL WHERE id=?", (db.dumps(acc), gid))
    return [f"goal {gid}: accepting, round {rnd} (judge run {rid})"]


def _wait(conn: sqlite3.Connection, gid: str, why: str) -> List[str]:
    """No judge now: say why once on the goal and try again next tick."""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    cur = _goal(conn, gid)
    changed = cur is not None and cur["error"] != why
    if changed:
        conn.execute("UPDATE goals SET error=? WHERE id=?", (why, gid))
    if own:
        conn.execute("COMMIT")
    return [f"goal {gid}: {why}"] if changed else []


def _play(conn: sqlite3.Connection, gid: str, wf: Any, g: sqlite3.Row, checks: List[Tuple[str, str]],
          rnd: int, avoid: List[str], judge: str) -> str:
    """The playtester's command run in the judge's worktree, registered as scope play (in the tx)."""
    cwd = store.thread(conn, store.run(conn, judge)["thread_id"])["cwd"]
    title = next((ln.strip() for ln in (g["text"] or "").splitlines() if ln.strip()), gid)[:80]
    line = judgebrief.play_line(wf, title, checks)
    tid = store.create_thread(conn, f"judge (play): {gid}", cwd, by="eki")
    rid = store.create_run(conn, tid, json.dumps(["/bin/sh", "-c", line, "eki-judge"]), provider="command",
                           priority="background")
    verdicts.register(conn, rid, gid, None, "play", [c for c, _ in checks], rnd, avoid)
    return rid


# ---- concluding -------------------------------------------------------------------------------

def conclude(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    """Once every run of the goal's round has ended: accepted, a second judge, fixes, or judgment."""
    gid = g["id"]
    acc = state_of(g)
    rnd = int(acc["round"])
    js = _judgings(conn, gid, rnd)
    if not js:                                   # nothing was started for this round: start it again
        with db.tx(conn):
            cur = _goal(conn, gid)
            if cur is None or cur["state"] != "accepting":
                return []
            conn.execute("UPDATE goals SET state='planned' WHERE id=?", (gid,))
        return [f"goal {gid}: acceptance had no judge run; starting again"]
    if not all(judgerun.ended(conn, j["run_id"]) for j in js):
        return []
    got = results(conn, gid, rnd)
    failed = [c for c, r in got.items() if r["verdict"] == "failed"]
    unsure = [c for c, r in got.items() if r["verdict"] == "uncertain"]
    if failed:
        if acc["fixes"]:
            return _judgment(conn, gid, rnd, [f"{c}: failed again: {got[c]['suggestion']}" for c in failed])
        return _fix(conn, gid, rnd, failed, got)
    if unsure:
        if any(j["scope"] == "second" for j in js):
            return _judgment(conn, gid, rnd, [f"{c}: {got[c]['limitation'] or NO_VERDICT}" for c in unsure])
        return second(conn, g, unsure, got, js)
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None or cur["state"] != "accepting":
            return []
        conn.execute("UPDATE goals SET state='accepted', error=NULL WHERE id=?", (gid,))
    return [f"goal {gid}: accepted — every check passed (round {rnd})"]


def second(conn: sqlite3.Connection, g: sqlite3.Row, unsure: List[str], got: Dict[str, Dict[str, str]],
           js: List[sqlite3.Row]) -> List[str]:
    """One more judge, off the builders' and the first judges' providers, for the uncertain checks."""
    gid = g["id"]
    acc = state_of(g)
    rnd = int(acc["round"])
    avoid = set(builders(conn, gid))
    for j in js:
        run = store.run(conn, j["run_id"])
        if run is not None and run["provider"] and run["provider"] != "command":
            avoid.add(run["provider"])
    text = {c["id"]: c["text"] for c in verdicts.checks_of(conn, gid)}
    checks = [(c, f"{text[c]} (the first judge could not settle it: {got[c]['limitation'] or NO_VERDICT})")
              for c in unsure]
    try:
        repo, ref, _ = _where(conn, g)
    except (KeyError, WorkspaceError, OSError, RuntimeError) as e:
        return _judgment(conn, gid, rnd, [f"{c}: {got[c]['limitation'] or NO_VERDICT}; no second judge: {e}"
                                          for c in unsure])
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None or cur["state"] != "accepting":
            return []
        rid, why = judgerun.start(conn, goal_id=gid, item_id=None, scope="second", checks=unsure, round=rnd,
                                  avoid=avoid, tree=_tree(gid), repo=repo, ref=ref,
                                  prompt=judgebrief.brief(cur["text"], checks, "second"), background=True)
        if rid is not None:
            acc["runs"] = list(acc["runs"]) + [rid]
            conn.execute("UPDATE goals SET accept=? WHERE id=?", (db.dumps(acc), gid))
    if rid is None:
        return _judgment(conn, gid, rnd, [f"{c}: {got[c]['limitation'] or NO_VERDICT}" for c in unsure],
                         also=why)
    return [f"goal {gid}: {', '.join(unsure)} uncertain — a second judge (run {rid})"]


def _judgment(conn: sqlite3.Connection, gid: str, rnd: int, why: List[str], also: str = "") -> List[str]:
    """The person decides: state judgment, the needs-you notice, `needs you: <id>: <limitation>`."""
    from . import selfwork
    error = "needs you: " + "; ".join(why) + (f" ({also})" if also else "")
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None or cur["state"] != "accepting":
            return []
        conn.execute("UPDATE goals SET state='judgment', error=? WHERE id=?", (error, gid))
        title = next((ln.strip() for ln in (cur["text"] or "").splitlines() if ln.strip()), gid)[:80]
        selfwork.needs_you(conn, {"id": gid, "title": title, "tries": rnd}, "judgment", "; ".join(why),
                           f"eki self accept {gid} or eki self retry {gid}", "judgment")
    return [f"goal {gid}: judgment — {error}"]


def _files_for(conn: sqlite3.Connection, gid: str, cid: str) -> List[str]:
    """The touched files of the goal's items tagged with `cid`, else of all its items."""
    tagged, every = [], []
    for it in conn.execute("SELECT touched, checks FROM items WHERE goal_id=? AND state!='dropped' "
                           "ORDER BY created_at, rowid", (gid,)):
        touched = json.loads(it["touched"] or "[]")
        every += touched
        if cid in json.loads(it["checks"] or "[]"):
            tagged += touched
    return list(dict.fromkeys(tagged or every))


def _fix(conn: sqlite3.Connection, gid: str, rnd: int, failed: List[str],
         got: Dict[str, Dict[str, str]]) -> List[str]:
    """One fix item per failed check; the goal goes back to planned for the next round."""
    from . import selfwork
    text = {c["id"]: c["text"] for c in verdicts.checks_of(conn, gid)}
    made = []
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None or cur["state"] != "accepting":
            return []
        for cid in failed:
            r = got[cid]
            spec = (f"Final acceptance found check {cid} failed.\n\nThe check: {cid}: {text[cid]}\n\n"
                    f"What the judge saw:\n{r['evidence']}\n\nThe judge's suggestion:\n{r['suggestion']}\n\n"
                    f"Make the check pass; change only what it needs.")
            iid = selfwork.new_item(conn, gid, f"Fix check {cid}: {text[cid][:TITLE_CUT]}", spec,
                                    _files_for(conn, gid, cid), [], False)
            conn.execute("UPDATE items SET checks=? WHERE id=?", (db.dumps([cid]), iid))
            made.append(iid)
        acc = state_of(cur)
        acc.update(round=rnd + 1, fixes=list(acc["fixes"]) + made, runs=[])
        conn.execute("UPDATE goals SET state='planned', accept=?, error=NULL WHERE id=?", (db.dumps(acc), gid))
    return [f"goal {gid}: {', '.join(failed)} failed — fix item(s) {', '.join(made)}"]


# ---- the person -------------------------------------------------------------------------------

def accept_by_you(conn: sqlite3.Connection, gid: str) -> str:
    """A judgment goal, accepted by the person."""
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None:
            raise KeyError(f"no goal {gid}")
        if cur["state"] != "judgment":
            raise ValueError(f"goal {gid} is {cur['state']}, not waiting for your judgment")
        acc = state_of(cur)
        acc["by"] = "you"
        conn.execute("UPDATE goals SET state='accepted', accept=?, error=NULL WHERE id=?", (db.dumps(acc), gid))
    return "accepted"


def rerun(conn: sqlite3.Connection, gid: str) -> str:
    """A judgment goal judged again, in a new round (fixes already made stay made: no second set)."""
    with db.tx(conn):
        cur = _goal(conn, gid)
        if cur is None:
            raise KeyError(f"no goal {gid}")
        if cur["state"] != "judgment":
            raise ValueError(f"goal {gid} is {cur['state']}, not waiting for your judgment")
        acc = state_of(cur)
        acc.update(round=int(acc["round"]) + 1, runs=[])
        conn.execute("UPDATE goals SET state='planned', accept=?, error=NULL WHERE id=?", (db.dumps(acc), gid))
    g = _goal(conn, gid)
    if ready(conn, g):
        start(conn, g)
    return _goal(conn, gid)["state"]
