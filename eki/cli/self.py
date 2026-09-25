"""`eki self`: ask eki to change itself, and see how each piece is going."""
from __future__ import annotations

import json
import subprocess

from .. import selfwork
from .common import ago, conn, ensure_engine, err, follow

NAME = "self"
HELP = "eki builds eki: `self \"…\"` plans and builds a change in parallel worktrees; `self` shows it"


def add(p) -> None:
    p.add_argument("what", nargs="*", help='a goal in words, or: show|diff|follow|drop|retry <item>')
    p.add_argument("--one", action="store_true", help="no planning: the goal is one item")
    p.add_argument("--files", help="with --one: the files it will touch, comma-separated")
    p.add_argument("--bg", action="store_true", help="don't follow the plan run")


def run(args) -> int:
    c = conn()
    words = args.what
    if not words:
        return board(c)
    verb = words[0]
    if verb in ("show", "diff", "follow", "drop", "retry") and len(words) == 2:
        return {"show": show, "diff": diff, "follow": follow_item, "drop": drop, "retry": retry}[verb](c, words[1])
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


def board(c) -> int:
    goals = c.execute("SELECT * FROM goals ORDER BY created_at DESC LIMIT 20").fetchall()
    if not goals:
        print('nothing yet — `eki self "…"` asks for a change')
        return 0
    s = selfwork.settings()
    print(f"(autonomy {s['autonomy']}, {s['parallel']} at once, source {selfwork.source()})")
    for g in goals:
        print(f"\n{g['id']}  {g['state']:<9} {ago(g['created_at']):>8}  {g['text'].splitlines()[0][:70]}")
        if g["error"]:
            print(f"           ! {g['error'][:200]}")
        for it in c.execute("SELECT * FROM items WHERE goal_id=? ORDER BY created_at", (g["id"],)):
            print(f"  {it['id']}  {it['state']:<9} {_where(it):<22} {it['title'][:52]}")
            note = _note(c, it)
            if note:
                print(f"             {note}")
    return 0


def _where(it) -> str:
    if it["state"] in ("building", "judging") and it["run_id"]:
        return f"run {it['run_id']}"
    if it["state"] == "proposed":
        return it["branch"] or ""
    if it["state"] == "waiting":
        deps = json.loads(it["deps"] or "[]")
        return f"after {', '.join(deps)}" if deps else "for room"
    return ""


def _note(c, it) -> str:
    if it["state"] in ("left", "unfit") and it["error"]:
        return f"! {it['error'].splitlines()[0][:150]}"
    if it["state"] == "proposed":
        files = json.loads(it["touched"] or "[]")
        return f"✓ {len(files)} files; merge {it['branch']} or `eki swap {it['branch']}`"
    if it["state"] == "applied":
        return f"✓ in main ({(it['commit_sha'] or '')[:12]})"
    return ""


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
    out = subprocess.run(["git", "-C", str(selfwork.source()), "diff", f"{it['base']}..{it['commit_sha']}"],
                         capture_output=True, text=True)
    print(out.stdout or out.stderr)
    return 0


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
