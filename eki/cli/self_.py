# SPDX-License-Identifier: Apache-2.0
"""`eki self`: eki working on itself — ask for a change, see what it did,
decide (eki/selfwork.py, eki/selfloop.py, docs/self-build.md)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .. import digest as digest_mod
from .common import _cut, _going_live, _next_go_live, call, ensure_engine, parent_headers, watch

ORDER = 130


def register(sub) -> None:
    sw = sub.add_parser("self", help="eki working on itself: ask for a change, see what it did, decide",
                        description="eki self [--all]              what it's doing, what waits for you, what's next (--all: nothing cut off)\n"
                                    "eki self \"change …\"           a change to eki, now (--later: when there's room)\n"
                                    "eki self -r \"…\" -r \"…\"         several changes, queued; they start as room allows\n"
                                    "eki self --batch FILE         the same, one request per line (- reads stdin)\n"
                                    "eki self parallel [N]         the most self-work at once (default 2)\n"
                                    "eki self release [N]          go live now with what's applied; N: at most one go-live every N min (15)\n"
                                    "eki self on|off               let eki work on itself whenever the machine has room\n"
                                    "eki self --watch               each change going in, at its stage — every few seconds\n"
                                    "eki self diff|show|apply|discard|undo <id>   (show: with its timeline)\n"
                                    "eki self offer <id>           offer a change upstream as a pull request (asks first)\n"
                                    "eki self next                 what it would take next\n"
                                    "eki self retry|drop|mine <item>\n"
                                    "eki self autonomy propose|apply [path=apply …]\n"
                                    "eki self digest [now]         the day's page: what changed, what helped, what waits for you\n"
                                    "eki self note [now]           the weekly note: what it noticed, what it suggests\n"
                                    "eki self drill [quick]        restart a sandboxed engine mid-work: does it lose anything?",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    sw.add_argument("request", nargs="*", default=[])
    sw.add_argument("--later", action="store_true",
                    help="queue it: eki takes it when the machine has room (eki self on)")
    sw.add_argument("-r", "--request", dest="requests", action="append", metavar="REQUEST",
                    help="one of several changes to queue at once (repeat it)")
    sw.add_argument("--batch", metavar="FILE",
                    help="queue a change per line of FILE (blank-line paragraphs for longer ones; - is stdin)")
    sw.add_argument("-b", "--backend", help="the agent to use (default: routed)")
    sw.add_argument("--base", default="HEAD", help="commit or branch to start from")
    sw.add_argument("--anyway", action="store_true",
                    help="go ahead even if the base fails its own tests")
    sw.add_argument("--limit", type=int, default=20)
    sw.add_argument("--all", action="store_true",
                    help="eki self: every row of every list and every title whole — nothing cut off")
    sw.add_argument("--apply", action="store_true",
                    help="if it's fit and touches nothing protected, swap it in (watched, rolled back if unhealthy)")
    sw.add_argument("--json", action="store_true")
    sw.add_argument("-y", "--yes", action="store_true",
                    help="eki self apply: don't ask before applying a change that touches protected paths; "
                         "eki self offer: don't ask before pushing and opening the pull request")
    sw.add_argument("--now", action="store_true",
                    help="eki self apply: go live at once, not with the next release train")
    sw.add_argument("--watch", action="store_true",
                    help="eki self: the changes going in and the next go-live, drawn again every few seconds")
    # `eki self apply --yes abc`: words left over after a flag carry on the request
    sw.set_defaults(func=cmd_self, leftover="request")


SELF_VERBS = ("apply", "discard", "undo", "diff", "show", "on", "off", "next", "note",
              "digest", "autonomy", "retry", "drop", "mine", "parallel", "release", "drill")


def _ago(t: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t or 0))


def _self_status(service: str, limit: int, everything: bool = False) -> int:
    """What eki is doing on itself. Lists and titles are cut to fit a
    terminal, each saying so; `everything` (--all) cuts nothing."""
    v = call("GET", "/api/self", service)
    cut = (lambda text, n: text) if everything else _cut
    if not v["can"]:
        print(v["why_not"])
        return 1
    g = v.get("goal")
    if g is None:
        print("eki works on itself: off — `eki self on` lets it, whenever the machine has room")
    else:
        spend = "your subscriptions' spare room" if g.get("spare") else "nothing but what you allow"
        if v.get("local"):
            spend += " and the local models"
        print(f"eki works on itself: {'on' if g['state'] == 'active' else g['state']} — uses {spend}"
              + ("   (eki self off)" if g["state"] == "active" else "   (eki self on)"))
        if g.get("note") and not g.get("working"):
            print(f"now: {g['note']}")
    flow = _pipeline_lines(v)
    if flow is None:                        # an engine from before eki/pipeline.py
        for line in (_going_live(v.get("going_live") or {}), _next_go_live(v.get("train") or {}, everything)):
            if line:
                print(line)
    if v.get("app"):
        print(v["app"])
    areas = ", ".join(f"{k}: {m}" for k, m in (v.get("areas") or {}).items())
    print(f"autonomy: {v['autonomy']}" + (f" ({areas})" if areas else "")
          + f" · at most {v['review_max']} waiting for you · {v.get('parallel', 1)} at once")
    for line in _tiers(v.get("tiers") or {}):
        print(line)
    working = [i for i in v["working"] if i.get("phase") != "merging"]
    if working:
        print("\nworking on:")
        for i in working:
            where = ", ".join(i.get("areas") or []) or "?"
            step = i.get("step") or {}
            stalled = step.get("state") == "interrupted" or (step and not step.get("live") and not i.get("live"))
            print(f"  {i['source']:<7} {cut(i['title'], 60)}  [{where}]"
                  + ("" if i.get("live") else
                     f"  (cut off at {step.get('kind')} — carries on by itself)" if stalled else
                     "  (carries on when there's room)"))
            if i.get("why"):
                print(f"          picked: {i['why'][:100]}")
    if flow:
        print("\n" + "\n".join(flow).lstrip("\n"))
    if flow is not None and not any(r.get("stage") not in ("live", "rolled back") for r in v.get("pipeline") or []):
        print("\ngoing in: nothing on its way right now")
    elif flow is None and v.get("merging"):
        print("\nmerge queue (the next release train merges them together):")
        for n, r in enumerate(v["merging"], 1):
            state = "being merged now" if r.get("applying") else "waiting for the train" \
                if r.get("live") or r.get("person") else "cut off — back in line when it carries on"
            print(f"  {n}. self/{r['change']}  {cut(r.get('title', ''), 56)} — {state}")
    for line in _tree(v.get("tree") or {}, v.get("tree_last") or {}, (v.get("golive") or {}).get("last") or {}):
        print(line)
    if v["waiting"]:
        print("\nwaiting for you:")
        for c in v["waiting"]:
            print(f"  self/{c['id']}  {c.get('source', ''):<7} {cut(c['title'], 58)}"
                  f"\n              eki self diff {c['id']} · eki self apply {c['id']} · eki self discard {c['id']}")
    ahead = v["queue"]
    upcoming = v["roadmap"]["next"]
    nexts = [f"  roadmap {n['section'].split(' — ')[0]}: {cut(n['title'], 60)}{_after(n)}"
             + (f"\n          {cut(n['worth'], 100)}" if n.get("worth") else "")
             for n in (upcoming if everything else upcoming[:3])]
    if ahead or nexts:
        print("\nup next:")
        for i in ahead:
            print(f"  {i['source']:<7} {cut(i['title'], 70)}{_after(i)}")
        for line in nexts:
            print(line)
        _more(len(upcoming) - len(nexts), "on the roadmap")
    if v["left"]:
        print("\nleft for you:")
        for i in v["left"]:
            print(f"  {i['id'][:6]}  {cut(i['title'], 60)} — {cut(i.get('note', ''), 90)}"
                  f"\n          (eki self retry {i['id'][:6]} to let it try again)")
    done = [c for c in v["changes"] if c["state"] not in ("proposed", "conflicts")]
    rows = done if everything else done[:limit]
    if rows:
        print("\nrecent:")
        for c in rows:
            said = (c.get("stage") or {}).get("label", "").lower() or c["state"]
            print(f"  {_ago(c.get('state_at'))}  {said:<11} self/{c['id']}  {cut(c['title'], 56)}")
        _more(len(done) - len(rows), "older")
    page = v.get("digest")
    if page and not page.get("quiet"):
        print(f"\ntoday's digest ({page['id']}): `eki self digest`")
    note = v.get("note")
    if note:
        print(f"\nweekly note ({note['id']}): {len(note.get('suggestions') or [])} suggestions — "
              "on the board (Goals → Self)")
    return 0


def _more(n: int, what: str) -> None:
    """A list cut short says how much was left out, and how to see it."""
    if n > 0:
        print(f"  … {n} more {what} — eki self --all shows them")


def _tiers(tiers: Dict[str, Any]) -> List[str]:
    """What eki may change alone, in two lines: never, and only fully checked."""
    def names(paths: List[str]) -> str:
        return ", ".join(p.split("/", 1)[-1] if p.startswith("eki/") else p for p in paths)
    out = []
    if tiers.get("locked"):
        out.append("  never alone (you apply them): " + names(tiers["locked"]))
    if tiers.get("guarded"):
        out.append("  alone only under apply, every check passing, live on its own: " + names(tiers["guarded"]))
    return out


def _after(row: Dict[str, Any]) -> str:
    """A roadmap item waiting for the one above it in its stage says so."""
    return f"  (after: {row['after'][:60]})" if row.get("after") else ""


def _pipeline_lines(v: Dict[str, Any]) -> Optional[List[str]]:
    """Each change on its way in at its real stage, and the go-live as one
    group (eki/pipeline.py); None when the engine doesn't say (an older one)."""
    if "pipeline" not in v:
        return None
    from .. import pipeline
    return pipeline.lines({"changes": v.get("pipeline") or [], "golive": v.get("golive") or {}})


def _self_watch(service: str, every: float = 3.0) -> int:
    """`eki self --watch`: the pipeline, drawn again every few seconds."""
    try:
        while True:
            v = call("GET", "/api/self", service)
            lines = _pipeline_lines(v)
            out = [f"eki self — {time.strftime('%H:%M:%S')}   (Ctrl-C stops watching)", ""]
            out += (lines if lines else ["nothing on its way in"]) if lines is not None else \
                ["this engine doesn't say its stages yet — it's older than `eki self --watch`"]
            if sys.stdout.isatty():
                sys.stdout.write("\033[H\033[2J")
            print("\n".join(out), flush=True)
            time.sleep(every)
    except KeyboardInterrupt:
        return 0


def _timeline_lines(c: Dict[str, Any]) -> List[str]:
    """`eki self show`: where it stands, and each point it passed, with times."""
    st, tl = c.get("stage") or {}, c.get("timeline") or []
    out = [f"now: {st['text']}"] if st.get("text") else []
    if tl:
        out.append("timeline:")
    for r in tl:
        out.append(f"  {_ago(r['at'])}  {r['step']}" + (f" — {r['note']}" if r.get("note") else ""))
    return out


def _self_verb(args, verb: str, rest: List[str]) -> int:
    s = args.service
    arg = rest[0] if rest else ""
    if verb in ("on", "off"):
        got = call("POST", "/api/self/on", s, json={"on": verb == "on"})
        g = got.get("goal")
        print("eki works on itself whenever the machine has room — changes come to you as proposals "
              "(Goals → Self, or `eki self`)" if verb == "on" and g else
              "paused — nothing new starts; what's running finishes" if g else "it wasn't on")
        return 0
    if verb == "autonomy":
        if not rest:
            v = call("GET", "/api/self", s)
            print(f"{v['autonomy']}; areas: {json.dumps(v.get('areas') or {})}")
            return 0
        body: Dict[str, Any] = {}
        areas: Dict[str, str] = {}
        for word in rest:
            if "=" in word:
                path, _, mode = word.partition("=")
                areas[path] = mode
            else:
                body["autonomy"] = word
        if areas:
            v = call("GET", "/api/self", s)
            merged = {**(v.get("areas") or {}), **areas}
            body["areas"] = {k: m for k, m in merged.items() if m in ("apply", "propose")}
        print(json.dumps(call("PUT", "/api/self/settings", s, json=body)))
        return 0
    if verb == "parallel":
        if not arg:
            print(call("GET", "/api/self", s).get("parallel", 1))
            return 0
        got = call("PUT", "/api/self/settings", s, json={"parallel": int(arg)})
        print(f"at most {got['self_parallel']} at once — fewer when your subscriptions' spare room is short")
        return 0
    if verb == "release":
        if arg:
            got = call("PUT", "/api/self/settings", s, json={"release_minutes": int(arg)})
            print(f"applied changes go live together, at most once every {got['self_release_minutes']} min")
            return 0
        got = call("POST", "/api/self/release", s)
        print(f"going live now, carrying {', '.join('self/' + x for x in got.get('cars') or []) or 'what was waiting'}"
              if got.get("build") else got.get("why") or "nothing waiting to go live")
        return 0
    if verb == "next":
        v = call("GET", "/api/self", s)
        for i in v["working"] + v["queue"]:
            print(f"{i['source']:<8} {i['title']}{_after(i)}")
        for n in v["roadmap"]["next"]:
            print(f"roadmap  {n['section']}: {n['title']}{_after(n)}")
        return 0
    if verb == "digest":
        v = call("GET", "/api/self", s)
        page = call("POST", "/api/self/digest", s) if arg == "now" or not v.get("digest") else v["digest"]
        print(f"eki's day, {page['id']}\n\n{digest_mod.plain(page['text'])}")
        return 0
    if verb == "note":
        v = call("GET", "/api/self", s)
        if arg == "now" or not v.get("note"):
            got = call("POST", "/api/self/note", s)
            print(f"writing this week's note: run {got.get('run')} — `eki watch {got.get('run')}`")
            return 0
        note = v["note"]
        print(f"eki's note of {note['id']}\n\n{note['text']}\n")
        for i, sug in enumerate(note.get("suggestions") or []):
            print(f"{i}. {sug['title']} [{sug['kind']}]{' — ' + sug['picked'] if sug.get('picked') else ''}"
                  f"\n   {sug['why']}")
        return 0
    if not arg:
        print(f"eki self {verb} <id>", file=sys.stderr)
        return 2
    if verb in ("retry", "drop", "mine"):
        got = call("POST", f"/api/self/items/{arg}/{'person' if verb == 'mine' else verb}", s)
        print(f"{got['title']}: {got['state']}")
        return 0
    if verb == "diff":
        print(call("GET", f"/api/self/changes/{arg}/diff", s)["diff"])
        return 0
    if verb == "show":
        c = call("GET", f"/api/self/changes/{arg}", s)
        tl = _timeline_lines(c)
        print("\n".join(c["lines"] + ([""] + tl if tl else [])))
        return 0
    body: Dict[str, Any] = {}
    if verb == "apply":
        if getattr(args, "now", False):
            body["now"] = True
        c = call("GET", f"/api/self/changes/{arg}", s)
        if c.get("protected"):
            if not _confirm_protected(c, getattr(args, "yes", False)):
                print("not applied", file=sys.stderr)
                return 1
            body["confirm"] = True
    if verb in ("apply", "undo"):
        print("· a change put on top of your checkout is judged again first — a minute or two",
              file=sys.stderr)
    try:
        # applying may rebase and re-run the checks: longer than an ordinary call
        r = httpx.post(f"{s}/api/self/changes/{arg}/{verb}", json=body or None, timeout=1800,
                       headers=parent_headers())
    except httpx.HTTPError as e:
        print(f"! lost the engine: {e}", file=sys.stderr)
        return 1
    got = r.json()
    if r.status_code >= 400:
        print(f"! {got.get('detail') or r.text[:200]}", file=sys.stderr)
        return 1
    say = {"applying": ("applying — it goes live " + ("now" if body.get("now") else
                        "with the next release train (`eki self` says when; `--now` doesn't wait)")
                        + ": the supervisor swaps it in (runs still going carry on in it), watches it, "
                        "and goes back if it isn't healthy (eki builds)"),
           "applied": got.get("merged") or "applied",
           "conflicts": _conflicts_said(got),
           "unfit": "it didn't pass its checks on top of your checkout"
                    + (f": {got['why']}" if got.get("why") else ""),
           "queued": f"in line: {got.get('why') or 'it lands with the next release train'}",
           "proposed": f"not applied: {got.get('why') or 'see `eki self show ' + arg + '`'}",
           "discarded": "discarded", "not undone": f"not undone: {got.get('why')}"}
    print(say.get(got.get("state") or "", json.dumps(got)))
    return 0


_TREE_STATE = {"merging": "merging", "checking": "judging the batch", "bisecting": "bisecting a failed batch",
               "merged": "merged"}


def _tree(t: Dict[str, Any], last: Optional[Dict[str, Any]] = None,
          golive: Optional[Dict[str, Any]] = None) -> List[str]:
    """This train's merge, level by level: its groups, its pairs round by
    round, and which worker merges what (eki/treemerge.py). With none
    running, it says so — and how the last one went, so you can see it worked."""
    if not t or t.get("state") in ("merged", "failed"):
        out = ["\ntree merge: no merge running — the next go-live merges whatever is ready"]
        return out + _last_train(last or {}, golive or {})
    ids = lambda xs: " + ".join(x[:8] for x in xs) or "your checkout"   # noqa: E731
    n = len(t.get("straight") or []) + sum(len(g["changes"]) for g in t.get("groups") or [])
    out = [f"\ntree merge — {n} change{'s' if n != 1 else ''}, {_TREE_STATE.get(t.get('state'), t.get('state'))}"
           f" · {len(t.get('rounds') or [])} round(s) · up to {t.get('workers')} at once:"]
    if t.get("straight"):
        out.append("  straight in (no file shared): " + ", ".join(s["id"] for s in t["straight"]))
    for gi, g in enumerate(t.get("groups") or [], 1):
        out.append(f"  group {gi} — {', '.join(g['files'])}: " + ", ".join(c["id"] for c in g["changes"]))
    for ri, rows in enumerate(t.get("rounds") or [], 1):
        out.append(f"  round {ri}:")
        for r in rows:
            who = f"worker {r['worker']}: " if r.get("worker") else ""
            out.append(f"    {who}{ids(r['a'])} ⨝ {ids(r['b'])} — {r['state']}"
                       + (f" ({r['why'][:80]})" if r.get("why") else ""))
    for c in t.get("checks") or []:
        out.append(f"  checked {len(c['ids'])} together — {c['state']}" + (f": {c['why'][:80]}" if c.get("why") else ""))
    for cid, why in (t.get("dropped") or {}).items():
        out.append(f"  dropped {cid}: {why[:100]}")
    return out


def _last_train(t: Dict[str, Any], golive: Dict[str, Any]) -> List[str]:
    """The last train that finished: when, what it carried, how many rounds,
    how it came out — and, once it left, how its go-live went."""
    if not t.get("changes"):
        return []
    n, r = len(t["changes"]), int(t.get("rounds") or 0)
    if t.get("outcome") == "failed":
        how = f"failed: {t.get('why') or 'the merge broke off'}"[:120]
    else:
        how = f"{len(t.get('landed') or [])} landed" + (f", {len(t['dropped'])} dropped" if t.get("dropped") else "")
        went = {c.get("id") for c in golive.get("carrying") or []}
        if went & set(t.get("landed") or []) and golive.get("text"):
            how += f" — {golive['text']}"
    out = [f"  last train {_ago(t.get('ended') or t.get('at') or 0)}: {n} change{'s' if n != 1 else ''}, "
           f"{r} round{'s' if r != 1 else ''} — {how}"]
    out += [f"    {c['id']}  {(c.get('title') or '')[:64]}" for c in t["changes"]]
    out += [f"    dropped {cid}: {why[:90]}" for cid, why in (t.get("dropped") or {}).items()]
    return out


def _conflicts_said(got: Dict[str, Any]) -> str:
    """An apply that conflicts usually isn't a failure: eki has the conflicts
    fixed in a run of its own and applies it after. Retry is only worth
    mentioning when no such run started (resolving is off)."""
    rid = got.get("resolving")
    if rid:
        return (f"it conflicts with your checkout, so eki is fixing the conflicts itself and "
                f"applies it when that's done — nothing for you to do (follow it: eki watch {rid})")
    return "it no longer goes on top of your checkout — `eki self retry` lets eki try again"


def _confirm_protected(c: Dict[str, Any], yes: bool) -> bool:
    """A change touching what eki may not change alone goes in only when you
    say so, having seen which files: a y/N prompt, or --yes."""
    print(f"self/{c['id']} touches what eki may not change alone:", file=sys.stderr)
    for f in c["protected"]:
        print(f"  {f}", file=sys.stderr)
    print(f"  (read it first: eki self diff {c['id']})", file=sys.stderr)
    if yes:
        return True
    if not sys.stdin.isatty():
        print("! not asked from a terminal — add --yes to apply it", file=sys.stderr)
        return False
    try:
        return input("apply it anyway? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _batch(args) -> List[str]:
    """The requests given with -r/--request and --batch (a file, `-` for
    standard input: one request per line, or paragraphs split by blank lines
    when any request runs over several lines)."""
    asked = [r.strip() for r in args.requests or [] if r.strip()]
    if args.batch:
        text = sys.stdin.read() if args.batch == "-" else Path(args.batch).expanduser().read_text()
        parts = [x for x in text.split("\n\n")] if "\n\n" in text.strip() else text.splitlines()
        asked += [" ".join(x.split()) for x in parts if x.strip() and not x.strip().startswith("#")]
    return asked


def _self_batch(args, asked: List[str]) -> int:
    """Several changes at once: each queued as its own item; they start as
    room allows, side by side where their areas don't overlap."""
    ensure_engine(args.service)
    queued, goal = 0, False
    for request in asked:
        got = call("POST", "/api/self", args.service,
                   json={"request": request, "when": "later", "apply": args.apply,
                         "backend": args.backend or ""})
        goal = goal or bool(got.get("goal"))
        queued += 1
        print(f"  queued: {request.splitlines()[0][:70]}")
    print(f"{queued} queued — they start as room allows (`eki self` shows them)" if goal else
          f"{queued} queued — but eki isn't working on itself yet: `eki self on`")
    return 0


def _self_drill(rest: List[str], as_json: bool) -> int:
    """The restart drill (eki/drill.py): eki's engine restarted in the middle
    of each kind of work, in a sandbox — nothing of yours is touched. Runs
    here, not in the engine: it restarts engines. `quick`: the candidate
    check's short one."""
    from .. import drill
    quick = bool(rest) and rest[0] == "quick"
    say = None if as_json else (lambda line: print(line, file=sys.stderr, flush=True))
    if not as_json:
        print("the restart drill — a sandboxed engine, restarted mid-work"
              + (" (quick)" if quick else "; about 15 minutes") + "…", file=sys.stderr)
    results = drill.run(drill.QUICK if quick else drill.FULL, launchd=False if quick else None,
                        say=say, test_seconds=3.0 if quick else 4.0)
    if not quick:
        drill.save(results)                 # the weekly note says how the last one went
    if as_json:
        print(json.dumps([r.to_json() for r in results], indent=2))
    else:
        print("\n".join(drill.table(results)))
    return 0 if results and all(r.ok for r in results) else 1


def _self_offer(rest: List[str], yes: bool, as_json: bool) -> int:
    """A change offered upstream as a pull request (eki/upstream.py). Runs
    here, as you, with your git and `gh` — publishing is yours to ask for,
    so it asks first."""
    from .. import selfwork, settings, upstream
    if not rest:
        print("eki self offer <id>", file=sys.stderr)
        return 2
    prefs = settings.load()
    onto, fork = prefs.get("self_upstream") or "origin/main", prefs.get("self_offer_remote") or ""
    try:
        c = selfwork.change(rest[0])
    except selfwork.SelfWorkError as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    print(f"self/{c['id']}: {c['title'][:70]}\n  its commits go on top of {onto} as eki/{c['id']}, "
          f"pushed to {fork or onto.partition('/')[0]}, and a pull request is opened — "
          "anyone who can see the repo can read it", file=sys.stderr)
    if not yes:
        if not sys.stdin.isatty():
            print("! not asked from a terminal — add --yes to offer it", file=sys.stderr)
            return 1
        try:
            if input("offer it? [y/N] ").strip().lower() not in ("y", "yes"):
                print("not offered", file=sys.stderr)
                return 1
        except EOFError:
            return 1
    try:
        got = upstream.offer(c["id"], upstream=onto, push_to=fork)
    except selfwork.SelfWorkError as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(got, indent=2))
    elif got.get("url"):
        print(f"{'opened' if got['opened'] else 'already open'}: {got['url']} — merging it stays with "
              "whoever looks after the repo")
    else:
        print(f"pushed {got['branch']} to {got['remote']}; the pull request wasn't opened "
              f"({got.get('why', '')})" + (f" — open it at {got['compare']}" if got.get("compare") else ""))
    return 0


def cmd_self(args) -> int:
    """eki, working on eki (eki/selfwork.py, eki/selfloop.py, docs/self-build.md)."""
    words = list(args.request or [])
    asked = _batch(args)
    if asked:
        # several at once: the words given without a flag are one more
        return _self_batch(args, asked + ([" ".join(words).strip()] if words else []))
    verb = words[0].lower() if words else ""
    if verb == "drill" and len(words) <= 2:
        return _self_drill(words[1:], args.json)
    if verb == "offer" and len(words) <= 2:
        return _self_offer(words[1:], getattr(args, "yes", False), args.json)
    if verb in SELF_VERBS and (len(words) <= 2 or verb == "autonomy"):
        ensure_engine(args.service)
        return _self_verb(args, verb, words[1:])
    request = " ".join(words).strip()
    if not request:
        ensure_engine(args.service)
        if getattr(args, "watch", False):
            return _self_watch(args.service)
        return _self_status(args.service, args.limit, args.all)
    ensure_engine(args.service)
    body = {"request": request, "when": "later" if args.later else "now", "apply": args.apply,
            "base": args.base if args.base != "HEAD" else "", "check_base": not args.anyway,
            "backend": args.backend or ""}
    got = call("POST", "/api/self", args.service, json=body)
    if got.get("queued"):
        print("queued — eki takes it when the machine has room" if got.get("goal") else
              "queued — but eki isn't working on itself yet: `eki self on`")
        return 0
    print(f"· run {got['run']} — its own thread in the app; Ctrl-C stops watching, not the work",
          file=sys.stderr)
    watch(got["run"], args.service)
    item = call("GET", f"/api/self/items/{got['item']}", args.service)
    c = item.get("change_detail")
    if not c:
        print(f"\n{item['state']}: {item.get('note') or 'no change was made'}")
        return 1
    if args.json:
        print(json.dumps(c, indent=2))
    return 0 if c.get("fit") else 1
