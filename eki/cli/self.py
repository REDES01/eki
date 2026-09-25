"""`eki self`: ask eki to change itself, and see how each piece is going."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .. import builds, queue, selfwork, train
from .common import conn, ensure_engine, err, follow
from .selfboard import board

NAME = "self"
HELP = "eki builds eki: `self \"…\"` plans and builds a change in parallel worktrees; `self` shows it"


def add(p) -> None:
    p.add_argument("what", nargs="*", help='a goal in words, or: show|diff|follow|drop|retry|apply <item>, '
                                               'release, autonomy apply|propose')
    p.add_argument("--one", action="store_true", help="no planning: the goal is one item")
    p.add_argument("--files", help="with --one: the files it will touch, comma-separated")
    p.add_argument("--bg", action="store_true", help="don't follow the plan run")
    p.add_argument("--yes", action="store_true", help="with apply: queue it even if it touches locked files")


def run(args) -> int:
    c = conn()
    words = args.what
    if not words:
        return board(c)
    verb = words[0]
    if verb in ("show", "diff", "follow", "drop", "retry") and len(words) == 2:
        return {"show": show, "diff": diff, "follow": follow_item, "drop": drop, "retry": retry}[verb](c, words[1])
    if verb == "apply" and len(words) == 2:
        print(f"queued {queue.apply(c, words[1], yes=args.yes)}")
        ensure_engine(quiet=True)
        return 0
    if verb == "release" and len(words) == 1:
        print("\n".join(train.release(c)) or "nothing to release")
        return 0
    if verb == "autonomy" and len(words) == 2:
        queue.set_autonomy(words[1])
        print(f"autonomy {selfwork.settings()['autonomy']}")
        return 0
    text = " ".join(words)
    files = [f.strip() for f in (args.files or "").split(",") if f.strip()]
    gid = selfwork.submit(c, text, plan=not args.one, files=files)
    ensure_engine(quiet=True)
    g = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    if args.one or args.bg or not g["plan_run"]:
        print(f"goal {gid}: {'one item, building when there is room' if args.one else 'planning'}")
        return 0
    err(f"goal {gid}: planning (run {g['plan_run']})")
    code = follow(c, g["plan_run"], show_tools=False)
    return code or board(c)


def show(c, iid: str) -> int:
    it = selfwork.store_item(c, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    g = c.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    print(f"item {it['id']}  {it['state']}  (goal {g['id']}: {g['text'].splitlines()[0][:60]})")
    print(f"title:    {it['title']}")
    print(f"spec:     {it['spec'][:600]}")
    print(f"files:    {', '.join(json.loads(it['files'] or '[]')) or '(not declared)'}")
    print(f"touched:  {', '.join(json.loads(it['touched'] or '[]')) or '-'}")
    print(f"worktree: {it['worktree'] or '-'}  branch {it['branch'] or '-'}  base {(it['base'] or '')[:12]}")
    print(f"commit:   {it['commit_sha'] or '-'}   tries {it['tries']}   run {it['run_id'] or '-'}")
    if it["summary"]:
        print(f"\nsummary:\n{it['summary']}")
    if it["verdict"]:
        print(f"\nchecks:\n{it['verdict']}")
    if it["error"]:
        print(f"\n! {it['error']}")
    return 0


def diff(c, iid: str) -> int:
    it = selfwork.store_item(c, iid)
    if it is None or not it["commit_sha"]:
        raise KeyError(f"no committed change for item {iid}")
    where = selfwork.repo()
    if not _has(where, it["commit_sha"]):
        where = builds.source()                    # built before the queue, from the source checkout
    out = subprocess.run(["git", "-C", str(where), "diff", f"{it['base']}..{it['commit_sha']}"],
                         capture_output=True, text=True)
    print(out.stdout or out.stderr)
    return 0


def _has(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def follow_item(c, iid: str) -> int:
    it = selfwork.store_item(c, iid)
    if it is None or not it["run_id"]:
        raise KeyError(f"no run for item {iid}")
    return follow(c, it["run_id"])


def drop(c, iid: str) -> int:
    print(f"dropped {selfwork.drop(c, iid)}")
    return 0


def retry(c, iid: str) -> int:
    print(f"queued again: {selfwork.retry(c, iid)}")
    ensure_engine(quiet=True)
    return 0
