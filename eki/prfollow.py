"""The person's comments on a PR come back in as follow-ups to the item.

Each housekeeping pass (at most every self.issues_minutes) looks at the open
PRs of projects on the GitHub path (eki/github.py):

- New comments and reviews by the gh user (never eki's own, which carry
  github.MARK) newer than `items.pr_seen` are triaged: approval words only
  is "not"; anything else asks the local model in a `prcomment` chore
  (subject `<item id>:<newest time>`), and a skipped or failed chore counts
  as a change requested.
- A change requested puts the item back to `waiting` on the same worktree and
  `eki/<id>` branch, with the comment appended to its spec; the ordinary
  scheduler builds it, gate 1 and the review run as for any build. At
  `self.pr_followups` eki says once that the next change is the person's.
- Back in `proposed` with a new commit, eki pushes it (a fast-forward) and
  answers in one line. A follow-up that ends unfit or left is rolled back to
  what was pushed, and eki says it couldn't.

Nothing is held in memory: the state is in the db and on the PR, and each
comment eki posts is looked for on the PR first, so a restart never doubles one.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import chores, db, github, projects, selfwork, store, workspace

KIND = "prcomment"
CAP_WHY = "follow-up cap reached"
_APPROVAL = re.compile(r"^(?:(?:lgtm|looks good|thanks|thank you|ok|👍|ship it|nice|great)\s*)+$")
_TRIAGE = re.compile(r"TRIAGE:[`*\s]*(change requested|not)\b", re.I)
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

PROMPT = """Triage the person's comments on a pull request eki opened.

Do they request a change to the code, or only approve, thank or chat?

The pull request: {title}
What it does: {summary}

The comment(s):
{text}

Answer with one line, exactly one of:
TRIAGE: change requested
TRIAGE: not
"""


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("prfollow"):
        return []
    said: List[str] = []
    rows = conn.execute("SELECT i.*, g.project AS pid FROM items i JOIN goals g ON g.id=i.goal_id"
                        " WHERE g.project IS NOT NULL AND i.pr_state='open' ORDER BY i.created_at").fetchall()
    finished = chores.finished(conn, KIND)
    pids = {r["pid"] for r in rows} | {_row_pid(conn, c["subject"]) for c in finished}
    on: Dict[str, sqlite3.Row] = {}                    # the projects on the GitHub path, checked fresh
    for pid in filter(None, pids):
        project = projects.get(conn, pid)
        if project is not None and github.path_of(project["path"])[0]:
            on[pid] = project
    if not on:
        return said
    try:
        me = github.whoami()
    except github.GhError as e:
        return [f"prfollow: couldn't ask gh who is logged in: {e}"]
    for c in finished:
        pid = _row_pid(conn, c["subject"])
        if pid in on:
            said += _guard(conn, c["subject"].split(":", 1)[0], lambda: _settle(conn, c, on[pid], me))
    for it in rows:
        if it["pid"] in on:
            said += _guard(conn, it["id"], lambda: _one(conn, it["id"], on[it["pid"]], me))
    return said


def _row_pid(conn: sqlite3.Connection, subject: str) -> Optional[str]:
    got = conn.execute("SELECT g.project FROM items i JOIN goals g ON g.id=i.goal_id WHERE i.id=?",
                       (subject.split(":", 1)[0],)).fetchone()
    return got["project"] if got else None


def _guard(conn: sqlite3.Connection, iid: str, step) -> List[str]:
    """One item's gh or git trouble is its own: said in items.why, the others go on."""
    try:
        return step()
    except (github.GhError, workspace.WorkspaceError) as e:
        why = f"GitHub: {github._first(str(e))}"[:400]
        with db.tx(conn):
            selfwork._set(conn, iid, why=why)
        return [f"item {iid}: {why}"]


# ---- one item ------------------------------------------------------------------------------

def _one(conn: sqlite3.Connection, iid: str, project: sqlite3.Row, me: str) -> List[str]:
    it = selfwork.store_item(conn, iid)
    if it is None or it["pr_state"] != "open":
        return []
    if it["state"] in ("unfit", "left") and (it["followups"] or 0) > 0 and it["pushed"]:
        return _roll_back(conn, it, project)
    if it["state"] != "proposed":
        return []
    said: List[str] = []
    if it["pushed"] and it["commit_sha"] and it["commit_sha"] != it["pushed"]:
        said += _answer(conn, it, project)
        it = selfwork.store_item(conn, iid)
    if conn.execute("SELECT 1 FROM chores WHERE kind=? AND state='open' AND subject LIKE ?",
                    (KIND, f"{iid}:%")).fetchone():
        return said
    view = github.pr_view(it["pr"])
    got = comments(view, me, it["pr_seen"])
    if not got:
        _clear_why(conn, it)
        return said
    newest = got[-1]["at"]
    if (it["followups"] or 0) >= _cap() and it["why"] == CAP_WHY:      # already said: nothing more to do
        with db.tx(conn):
            selfwork._set(conn, iid, pr_seen=newest)
        return said
    if all(approval(c["body"]) for c in got):
        return said + _decide(conn, it, project, me, got, newest, False, view)
    with db.tx(conn):
        rid = chores.start(conn, KIND, f"{iid}:{newest}", _prompt(it, got), "background")
    if rid is None:                                     # no local model: count it as a change asked
        return said + _decide(conn, it, project, me, got, newest, True, view)
    return said + [f"item {iid}: {len(got)} new PR comment(s); triage {rid[:8]}"]


def _settle(conn: sqlite3.Connection, c: sqlite3.Row, project: sqlite3.Row, me: str) -> List[str]:
    """A finished triage chore: act on it once, then close it."""
    iid, newest = c["subject"].split(":", 1)
    it = selfwork.store_item(conn, iid)
    if it is None or it["state"] != "proposed" or it["pr_state"] != "open":
        with db.tx(conn):
            chores.close(conn, c["id"], "done", "the item moved on")
        return []
    answer = store.answer(conn, c["run_id"]) if c["run_state"] == "done" else ""
    m = None
    for m in _TRIAGE.finditer(answer or ""):
        pass
    requested = m is None or m.group(1).lower() != "not"     # failed or unreadable: a change asked
    view = github.pr_view(it["pr"])
    got = [x for x in comments(view, me, it["pr_seen"]) if _when(x["at"]) <= _when(newest)]
    result = ("change requested" if requested else "not") if m else \
        f"change requested (the triage {'had no TRIAGE line' if c['run_state'] == 'done' else 'failed'})"
    return _decide(conn, it, project, me, got, newest, requested, view, chore=(c["id"], result))


def _decide(conn: sqlite3.Connection, it: sqlite3.Row, project: sqlite3.Row, me: str, got: List[dict],
            newest: str, requested: bool, view: dict, chore: Optional[Tuple[str, str]] = None) -> List[str]:
    iid, k, cap = it["id"], (it["followups"] or 0) + 1, _cap()
    if requested and got and k <= cap:
        if not it["worktree"] or not Path(it["worktree"]).exists():
            _recreate(conn, it, project)
        spec = (it["spec"] or "") + f"\n\nFollow-up {k} — asked on the PR by @{me}:\n" + \
            "\n\n".join(x["body"].strip() for x in got)
        with db.tx(conn):
            if chore and not chores.close(conn, chore[0], "done", chore[1]):
                return []
            cur = selfwork.store_item(conn, iid)
            if cur["state"] != "proposed" or cur["pr_seen"] != it["pr_seen"]:
                return []
            selfwork._set(conn, iid, spec=spec, followups=k, pr_seen=newest, state="waiting", tries=0,
                          verdict=None, review=None, error=None, reviews=0, why=None)
        return [f"item {iid}: follow-up {k} asked on its PR — building again on eki/{iid}"]
    if requested and got and not _said(view, "eki has made"):
        github.pr_comment(it["pr"], f"eki has made {cap} follow-ups here; the next change is yours {github.MARK}")
    with db.tx(conn):
        if chore and not chores.close(conn, chore[0], "done", chore[1]):
            return []
        fields = {"pr_seen": newest}
        if requested and got:
            fields["why"] = CAP_WHY
        elif (it["why"] or "").startswith("GitHub:"):
            fields["why"] = None
        selfwork._set(conn, iid, **fields)
    if requested and got:
        return [f"item {iid}: a change asked on its PR, but the {cap} follow-ups are made — left for you"]
    return [f"item {iid}: PR comment(s) ask for no change"]


def _answer(conn: sqlite3.Connection, it: sqlite3.Row, project: sqlite3.Row) -> List[str]:
    """A follow-up built and proposed again: push it and say so on the PR, once."""
    iid, sha = it["id"], it["commit_sha"]
    github.push(project["path"], sha, f"eki/{iid}")      # a fast-forward of what was pushed; never forced
    view = github.pr_view(it["pr"])
    if not _said(view, sha[:7]):
        files = workspace.git(project["path"], "diff", "--name-only", it["pushed"], sha, check=False).split()
        first = github._first(it["summary"] or "") or it["title"]
        github.pr_comment(it["pr"], f"Changed in {sha[:7]}: {first} ({len(files)} files) {github.MARK}")
    with db.tx(conn):
        selfwork._set(conn, iid, pushed=sha, why=None)
    return [f"item {iid}: follow-up pushed to its PR ({sha[:7]})"]


def _roll_back(conn: sqlite3.Connection, it: sqlite3.Row, project: sqlite3.Row) -> List[str]:
    """A follow-up that ended unfit or left: the PR stays as it was pushed."""
    iid, pushed = it["id"], it["pushed"]
    view = github.pr_view(it["pr"])
    seen = _when(it["pr_seen"])
    if not any(c["mine"] and c["body"].startswith("Couldn't do that") and _when(c["at"]) > seen
               for c in _all(view)):
        first = github._first(it["error"] or "") or f"the follow-up ended {it['state']}"
        github.pr_comment(it["pr"], f"Couldn't do that: {first} — the PR is as it was {github.MARK}")
    if it["worktree"] and Path(it["worktree"]).exists():
        workspace.git(it["worktree"], "reset", "-q", "--hard", pushed)
    else:
        _recreate(conn, it, project)
    with db.tx(conn):
        if selfwork.store_item(conn, iid)["state"] != it["state"]:
            return []
        selfwork._set(conn, iid, state="proposed", commit_sha=pushed, why=None)
    return [f"item {iid}: follow-up {it['followups']} ended {it['state']} — PR left as it was ({pushed[:7]})"]


def _recreate(conn: sqlite3.Connection, it: sqlite3.Row, project: sqlite3.Row) -> None:
    """The swept worktree again, on the existing eki/<id> branch at what was pushed."""
    wt = workspace.add(project["path"], it["id"], base=it["pushed"], branch=f"eki/{it['id']}")
    workspace.git(wt, "reset", "-q", "--hard", it["pushed"])
    projects.link_deps(project, wt)
    with db.tx(conn):
        selfwork._set(conn, it["id"], worktree=str(wt))


def _clear_why(conn: sqlite3.Connection, it: sqlite3.Row) -> None:
    if (it["why"] or "").startswith("GitHub:"):
        with db.tx(conn):
            selfwork._set(conn, it["id"], why=None)


# ---- reading the PR ------------------------------------------------------------------------

def _all(view: dict) -> List[dict]:
    """Comments and reviews as {login, body, at, mine}; `mine` = eki's own (carries the mark)."""
    out = []
    for c, at in [(c, "createdAt") for c in view.get("comments") or []] + \
                 [(r, "submittedAt") for r in view.get("reviews") or []]:
        body = c.get("body") or ""
        out.append({"login": ((c.get("author") or {}).get("login") or ""), "body": body,
                    "at": c.get(at) or c.get("createdAt") or "", "mine": github.MARK in body})
    return out


def comments(view: dict, me: str, seen: Optional[str]) -> List[dict]:
    """The person's words newer than `seen`, oldest first: by `me`, not empty, not eki's own."""
    after = _when(seen)
    got = [c for c in _all(view) if c["login"] == me and c["body"].strip() and not c["mine"]
           and c["at"] and _when(c["at"]) > after]
    return sorted(got, key=lambda c: _when(c["at"]))


def _said(view: dict, words: str) -> bool:
    return any(c["mine"] and words in c["body"] for c in _all(view))


def approval(body: str) -> bool:
    """Only approval words (case and punctuation aside)?"""
    words = " ".join(re.findall(r"[^\W_]+|👍", body.lower()))
    return bool(words) and bool(_APPROVAL.match(words))


def _when(iso: Optional[str]) -> datetime:
    if not iso:
        return _EPOCH
    try:
        t = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _cap() -> int:
    return int(selfwork.settings().get("pr_followups", 3))


def _prompt(it: sqlite3.Row, got: List[dict]) -> str:
    text = "\n\n".join(f"@{c['login']} at {c['at']}:\n{c['body'].strip()}" for c in got)
    return PROMPT.format(title=it["title"], summary=(it["summary"] or "(nothing said)").strip()[:2000],
                         text=text[:6000])
