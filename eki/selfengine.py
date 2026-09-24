# SPDX-License-Identifier: Apache-2.0
"""The engine's side of eki building eki (eki/selfloop.py, eki/selfwork.py).

One pipeline for every change eki makes to itself, whoever wanted it — a run
whose payload names a self-work item:

    begin      its own worktree of eki's source; the base checked first
    the agent  an ordinary run in that worktree — routed, streamed, resumable
               — told how to work on eki (selfwork.brief)
    conclude   committed (with the ROADMAP tick), then the candidate check
    then       applied or proposed, as the autonomy setting says; the thread
               says what came of it, in eki's own words

Started by a chat message that asks eki to change itself (in that thread, at
once), by `eki self "…"` and the board (at once, or later), by a fault in
eki's own code, and by the goal "eki works on itself", whose turns take the
next item whenever the machine has room (Engine.shift_tick).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from . import builds as builds_mod
from . import candidate
from . import capacity as capacity_mod
from . import goals as goals_mod
from . import observe as observe_mod
from . import roadmap
from . import selfloop
from . import selfwork
from . import settings as settings_mod
from . import shift as shift_mod
from .adapters.base import BackendError


class _NoItem:
    """What `_self_says` reads from an item, for a change without one."""

    def __init__(self, c: Dict[str, Any]):
        self.title, self.state, self.note, self.attempts = c.get("title") or "", "", "", 0

Piece = Union[str, Dict[str, str]]
#: the goal with nothing to take waits this long before looking again (or until woken)
IDLE_RECHECK = 900
#: what one change to eki is reckoned to cost, in the requests capacity.py
#: measures: an agent's long run, not one question
SELF_COST = 3
#: how often a change waiting in the merge queue looks whether it's its turn
MERGE_POLL = 2.0


def _payload(run: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return dict(json.loads(run.get("payload") or "{}") or {})
    except (TypeError, ValueError):
        return {}


class SelfLoop:
    """Mixed into Engine: everything that makes eki change eki."""

    #: judges a change; tests put a stand-in here
    self_check = staticmethod(candidate.check)

    # ---- where, and whether -------------------------------------------------------

    def _self_root(self) -> Path:
        return builds_mod.source()

    def _self_why_not(self) -> str:
        """Why eki can't work on its own code here, or ""."""
        root = self._self_root()
        why = builds_mod.cant_change_code(root)
        if why:
            return why
        if not (Path(root) / ".git").exists():
            return f"{root} isn't a git checkout — eki works on itself only where its source is a repo"
        return ""

    def _self_goal(self) -> Optional[goals_mod.Goal]:
        return next((g for g in goals_mod.all_goals() if g.kind == "self"), None)

    def _self_wake(self) -> None:
        """Something changed that the loop was waiting on: look again now."""
        g = self._self_goal()
        if g is not None and g.state == "active" and g.next_at > time.time():
            goals_mod.update(g.id, next_at=0, note="")
        self.shift_wake.set()                                           # type: ignore[attr-defined]

    def _self_board(self, cid: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/goals#/self" + (f"/{cid}" if cid else "")   # type: ignore[attr-defined]

    # ---- starting a piece of self-work ------------------------------------------------

    async def self_ask(self, request: str, *, when: str = "now", conversation: str = "",
                       apply: bool = False, base: str = "", check_base: bool = True,
                       backend: str = "", title: str = "") -> Dict[str, Any]:
        """Something you want changed in eki: at once, in a thread you can
        watch; or later, when the loop has room for it."""
        why = self._self_why_not()
        if why:
            raise ValueError(why)
        if not request.strip():
            raise ValueError("say what to change")
        first = title or request.strip().splitlines()[0]
        it = selfloop.add("asked", first, request, when=when, apply=apply, base=base,
                          check_base=check_base, backend=backend, conversation=conversation)
        if when == "later":
            self._self_wake()
            g = self._self_goal()
            return {"item": it.id, "queued": True,
                    "goal": bool(g and g.state == "active")}
        return {**await self._self_start(it), "item": it.id}

    async def _self_start(self, it: selfloop.Item, goal: Optional[goals_mod.Goal] = None,
                          allowed: Optional[List[str]] = None, planned: str = "") -> Dict[str, str]:
        """A run for this item, in its thread (a new one unless it has one).
        Returns at once; the run is the pipeline (`_self_work`)."""
        cid = it.conversation
        if not cid:
            title = f"eki · {it.title}"[:80]
            cid = self.store.new_conversation(title)                    # type: ignore[attr-defined]
            self.store.set_conversation(cid, title=title)               # type: ignore[attr-defined]
        shown = ("[eki · self] Carrying on: " if it.state == "working" else "[eki · self] ") + it.title
        meta: Dict[str, Any] = {"self_item": it.id}
        payload: Dict[str, Any] = {"self_item": it.id, "route": it.title or it.request}
        if goal is not None:
            meta["goal"] = goal.id
            payload.update(goal=goal.id, allowed=list(allowed or []), planned=planned)
        turn = self.store.add_turn(cid, "user", shown, meta=meta)       # type: ignore[attr-defined]
        rid = self.runs.create(shown, conversation=cid, user_turn=turn,  # type: ignore[attr-defined]
                               requested=it.backend, payload=json.dumps(payload))
        selfloop.update(it.id, state="working", run=rid, conversation=cid)
        await self.runner.submit(rid)                                   # type: ignore[attr-defined]
        return {"run": rid, "conversation": cid}

    async def _self_from_chat(self, run: Dict[str, Any], when: str) -> AsyncIterator[Piece]:
        """A chat message asking eki to change itself: the thread becomes the
        change's thread — at once, or queued for the loop."""
        cid = run["conversation_id"]
        first = run["prompt"].strip().splitlines()[0][:120]
        it = selfloop.add("asked", first, run["prompt"], when=when, conversation=cid,
                          backend=(run.get("requested") or "").partition(":")[0])
        g = self._self_goal()
        if when == "later" and g is not None and g.state == "active":
            text = ("*eki: that's a change to eki itself — queued. The goal “eki works on itself” "
                    "takes it when the machine has room, and the result comes back to this thread "
                    f"([Self]({self._self_board()})).*")
            self.store.add_turn(cid, "assistant", text, "eki", "self-work",   # type: ignore[attr-defined]
                                meta={"run": run["id"], "self_item": it.id})
            self._self_wake()
            yield text
            return
        payload = json.dumps({**_payload(run), "self_item": it.id, "route": run["prompt"]})
        self.runs.update(run["id"], payload=payload)                    # type: ignore[attr-defined]
        async for piece in self._self_work({**run, "payload": payload}):
            yield piece

    # ---- the pipeline -------------------------------------------------------------------

    async def _self_work(self, run: Dict[str, Any]) -> AsyncIterator[Piece]:
        p = _payload(run)
        try:
            it = selfloop.get(str(p.get("self_item") or ""))
        except KeyError:
            raise BackendError("that piece of self-work isn't there any more") from None
        cid = run["conversation_id"]
        it = selfloop.update(it.id, state="working", run=run["id"], conversation=cid or it.conversation)
        if it.source == "note":
            async for piece in self._self_note(run, it):
                yield piece
            return
        root = self._self_root()
        python = sys.executable
        plan = roadmap.read(root)
        prop: Optional[selfwork.Proposal] = None
        where = str(it.open.get("worktree") or "")
        if where and Path(where).is_dir():
            prop = selfwork.Proposal(**{k: v for k, v in it.open.items()
                                        if k in selfwork.Proposal.__dataclass_fields__})
        elif it.open.get("id"):
            # cut off after its change was judged (one with nothing in it leaves
            # no worktree): close that change rather than start the item over
            try:
                c = selfwork.change(str(it.open["id"]))
            except selfwork.SelfWorkError:
                c = None
            if c is not None:
                done = selfwork.Proposal(**{k: v for k, v in c.items()
                                            if k in selfwork.Proposal.__dataclass_fields__})
                async for piece in self._self_close(run, it, done, {}):
                    yield piece
                return
        if prop is None:
            yield ("*eki: a change to eki itself — in a worktree of its own source"
                   + (", once the source passes its own tests" if it.check_base else "") + "…*\n\n")
            try:
                prop = await asyncio.to_thread(
                    selfwork.begin, selfloop.request_for(it, plan), root=root, base=it.base or "HEAD",
                    python=python, check_base=it.check_base, source=it.source, item=it.id,
                    conversation=cid, title=it.title, ticks=it.key if it.source == "roadmap" else "")
            except selfwork.SelfWorkError as e:
                selfloop.update(it.id, state="done" if it.source in ("asked", "undo") else "gave up",
                                run="", note=str(e)[:300])
                raise BackendError(str(e)) from None
            if prop.verdict:                            # the base didn't pass its own tests
                async for piece in self._self_close(run, it, prop, {}):
                    yield piece
                return
            it = selfloop.update(it.id, open=prop.to_json(), change=prop.id, phase="agent")
            self.runs.update(run["id"], cwd=prop.worktree)              # type: ignore[attr-defined]
        state = str(it.open.get("agent_state") or "done")
        if it.phase in ("", "agent"):
            ask = selfwork.brief(prop.request, python, prop.worktree)
            if p.get("resume_of") or it.open.get("agent_began"):
                ask = "Carry on where you left off. The change, again:\n\n" + ask
            selfloop.update(it.id, open={**prop.to_json(), "agent_began": True})
            inner = {**run, "cwd": prop.worktree, "_self_inner": True, "_as": ask}
            parts: List[str] = []
            state = "done"
            try:
                async for piece in self._dispatch(inner):               # type: ignore[attr-defined]
                    if isinstance(piece, str):
                        parts.append(piece)
                    yield piece
            except BackendError as e:
                state = "failed"
                parts.append(f"\n[{e}]")
            answer = "".join(parts)
            fresh = self.runs.get(run["id"]) or run                     # type: ignore[attr-defined]
            prop.run, prop.backend = run["id"], fresh.get("backend") or prop.backend
            prop.said = selfloop.said(answer)
            prop.summary = selfwork.summary_of(answer)
            it = selfloop.update(it.id, phase="checking", open={
                **prop.to_json(), "agent_state": state, "reason": selfloop.reason(answer)})
        merging = it.phase == "merging"
        if merging:                                 # cut off while in the merge queue: back in line
            c = selfwork.change(prop.id)
            prop = selfwork.Proposal(**{k: v for k, v in c.items() if k in selfwork.Proposal.__dataclass_fields__})
        else:
            pending = await asyncio.to_thread(selfwork.changed, Path(prop.worktree)) \
                if Path(prop.worktree).is_dir() else []
            if pending and not selfwork.docs_only(pending):
                yield "\n\n*eki: judging the change — its tests, then a candidate engine on a spare port…*\n"
            tick = None
            if it.source == "roadmap" and prop.said in ("done", "already"):
                key, mark = it.key, f"*(eki: self/{prop.id})*"
                tick = lambda where: roadmap.tick_file(where, key, mark)   # noqa: E731
            prop = await asyncio.to_thread(
                selfwork.conclude, prop, {"state": state, "run": run["id"], "backend": prop.backend},
                python=python, check=self.self_check, before_commit=tick)
        applied: Dict[str, Any] = {}
        if prop.commit and prop.fit and not prop.protected:
            mode = "apply" if it.apply or merging else \
                selfloop.autonomy_for(prop.files, self.settings)       # type: ignore[attr-defined]
            if mode == "apply":
                # finished changes are applied one at a time, in the order they
                # finished: in line, it takes no room from the work still going
                ahead = selfloop.merge_join(prop.id, item=it.id, title=it.title, run=run["id"], area=it.area)
                it = selfloop.update(it.id, phase="merging")
                if ahead:
                    yield (f"\n\n*eki: fit — {ahead} change{'s' if ahead != 1 else ''} finished before it; "
                           "it's applied after, on top of what they bring…*\n")
                applied = await self._self_merge(prop.id)
        async for piece in self._self_close(run, it, prop, applied):
            yield piece

    async def _self_merge(self, cid: str) -> Dict[str, Any]:
        """Its turn in the merge queue, then applied: put on top of what landed
        before it, judged again, swapped in — its conflicts resolved first if
        it no longer goes on top, and the next in line waits for that. Waiting
        is never a failure."""
        try:
            while not selfloop.merge_turn(cid, self.runner.running):   # type: ignore[attr-defined]
                await asyncio.sleep(MERGE_POLL)
            selfloop.merge_mark(cid, applying=True)
            applied = await self._self_apply_now(cid)
            if applied.get("state") == "conflicts":
                started = await self._self_resolve_start(cid)
                task = self.runner.tasks.get(started["resolving"]) if started else None   # type: ignore[attr-defined]
                if task is not None:
                    await asyncio.wait([task])
                    c = selfwork.change(cid)
                    applied = {"state": c["state"], "id": cid, "why": c.get("why") or ""}
                else:
                    applied = started or applied
            return applied
        finally:
            selfloop.merge_leave(cid)

    async def _self_apply_now(self, cid: str, raising: bool = False,
                              by_person: bool = False) -> Dict[str, Any]:
        """selfwork.apply, one at a time in this engine: two applies at once
        would each build on a checkout without the other. `by_person` only
        where a person asked — it's what lets a protected change in."""
        lock = vars(self).setdefault("_self_applying", asyncio.Lock())
        async with lock:
            try:
                return await asyncio.to_thread(selfwork.apply, cid, python=sys.executable,
                                               check=self.self_check, by_person=by_person)
            except (selfwork.SelfWorkError, ValueError, RuntimeError) as e:
                if raising:
                    raise
                return {"state": "proposed", "id": cid, "why": str(e)[:300]}

    async def _self_close(self, run: Dict[str, Any], it: selfloop.Item, prop: selfwork.Proposal,
                          applied: Dict[str, Any]) -> AsyncIterator[Piece]:
        """Where the item stands now, a line in the thread, and — for work nobody
        was watching — a notification."""
        c = selfwork.change(prop.id)
        reason = str(it.open.get("reason") or "")
        if c["state"] == "no change" and reason:
            c["why"] = reason
        it = selfloop.after_change(it, c)
        selfloop.update(it.id, open={}, run="")
        if it.source == "fault" and it.key:
            observe_mod.mark(it.key, state="proposed" if c.get("commit") else "not proposed",
                             self=c["id"], branch=c.get("branch"), fit=c.get("fit"),
                             verdict=c.get("verdict"), run=run["id"], backend=c.get("backend"),
                             files=c.get("files"), at=int(time.time()))
        text = self._self_says(c, it, applied)
        cid = run["conversation_id"]
        if cid:
            self.store.add_turn(cid, "assistant", text, "eki", "self-work",   # type: ignore[attr-defined]
                                meta={"run": run["id"], "self": c["id"], "self_item": it.id})
        yield "\n\n" + text
        watched = it.source == "asked" and it.when == "now"
        if not watched and self.settings.get("notify_learned", True):   # type: ignore[attr-defined]
            head = {"applying": "Applying", "applied": "Applied", "proposed": "Proposed",
                    "conflicts": "Proposed"}.get(c["state"], "Tried")
            first = next((x.strip("-• ") for x in str(c.get("summary") or "").splitlines() if x.strip()), "")
            await self._notify(f"eki · {head}: {it.title}"[:120],           # type: ignore[attr-defined]
                               (first or c.get("verdict") or c["state"])[:200])
        self._self_wake()

    def _self_says(self, c: Dict[str, Any], it: selfloop.Item, applied: Dict[str, Any]) -> str:
        """What eki tells the thread about a change to itself."""
        cid, state = c["id"], c["state"]
        lines: List[str] = []
        head = {
            "proposed": "fit to run — proposed, waiting for you",
            "conflicts": "fit, but it no longer goes on top of your checkout",
            "applying": "fit to run — being applied",
            "applied": "applied",
            "unfit": "not fit to run",
            "stopped": "the agent's run didn't finish",
            "no change": "nothing changed",
            "not started": "not started",
        }.get(state, state)
        if c.get("protected") and state == "proposed":
            head = "passes, but touches what eki may not change alone — for you to read line by line"
        lines.append(f"**eki · change to itself: {head}.**")
        if c.get("summary"):
            lines.append("**What's new**\n\n" + str(c["summary"]).strip())
        if c.get("resolving"):
            lines.append("It conflicted with your checkout; eki is having the conflicts resolved, "
                         "then judges it again and applies it — nothing for you to do.")
        if (c.get("resolved") or {}).get("files"):
            r = c["resolved"]
            lines.append(f"Conflicts in {', '.join(f'`{f}`' for f in r['files'])} were resolved by "
                         f"{r.get('by') or 'an agent'}" + (f": {r['how']}" if r.get("how") else "."))
        if c.get("commit"):
            files = c.get("files") or []
            who = f"written by {c.get('backend')}" if c.get("backend") and c.get("backend") != "eki" else "made by eki"
            lines.append(f"`self/{cid}` — {who}; {len(files)} file{'s' if len(files) != 1 else ''}: "
                         + ", ".join(f"`{f}`" for f in files[:6]) + (" …" if len(files) > 6 else ""))
        pictures = sorted((selfwork.SHOTS / cid).glob("*.png")) if (selfwork.SHOTS / cid).is_dir() else []
        if pictures:
            # before first, then after — side by side is how a look is judged
            pictures = sorted(pictures, key=lambda p: (not p.name.startswith("before"), p.name))[:8]
            lines.append("**Before / after**\n\n" + "\n\n".join(f"![{p.stem}]({p})" for p in pictures))
        checks = (c.get("report") or {}).get("checks") or []
        if checks:
            lines.append("Checks: " + " · ".join(
                f"{k['name']} {'–' if k.get('skipped') else '✓' if k.get('ok') else '✗'}" for k in checks))
            failed = next((k for k in checks if not k.get("ok") and not k.get("skipped")), None)
            if failed:
                lines.append(f"What failed: {str(failed.get('detail') or '')[:400]}")
        if c.get("ticks") and c.get("commit") and c.get("said") in ("done", "already"):
            lines.append(f"It ticks the ROADMAP item “{it.title}”.")
        if state == "applying":
            lines.append("The supervisor swaps it in within a couple of minutes — runs still going "
                         "carry on in the new engine — watches it, and goes "
                         "back to what ran before if it isn't healthy.")
        elif state == "applied":
            lines.append(str(c.get("how") or c.get("merged") or "In your checkout."))
        elif state in ("proposed", "conflicts") and c.get("protected"):
            lines.append(f"eki won't apply it on its own. Read it with `eki self diff {cid}`; "
                         f"to take it, `eki self apply {cid}` or Apply on the board "
                         f"([Self]({self._self_board(cid)})) — it asks you to confirm first.")
        elif state in ("proposed", "conflicts"):
            if applied.get("why"):
                lines.append(f"Not applied: {applied['why']}")
            elif c.get("why"):
                lines.append(str(c["why"]))
            lines.append(f"Apply it on the board ([Self]({self._self_board(cid)})) or with "
                         f"`eki self apply {cid}`; read it with `eki self diff {cid}`.")
        elif state == "no change":
            if it.state == "person":
                lines.append(f"Left for you: {it.note}")
            elif it.state == "done" and it.note == "already done":
                lines.append("It was already done.")
            else:
                lines.append("The agent didn't change anything.")
        elif state in ("unfit", "stopped"):
            if it.state == "queued":
                lines.append(f"eki tries again later ({it.attempts} of {selfloop.MAX_ATTEMPTS} attempts).")
            elif it.state == "gave up":
                lines.append("Left for you after two attempts.")
            if c.get("commit"):
                lines.append(f"The branch is kept for reading: `eki self diff {cid}`.")
        elif state == "not started":
            lines.append(str(c.get("verdict") or ""))
        return "\n\n".join(x for x in lines if x)

    # ---- the weekly note ----------------------------------------------------------------

    async def _self_note(self, run: Dict[str, Any], it: selfloop.Item) -> AsyncIterator[Piece]:
        """What eki noticed this week and two or three suggestions — written by
        a model from the journal, the shift's report and the roadmap."""
        evidence = await asyncio.to_thread(self._self_evidence)
        inner = {**run, "cwd": "", "_self_inner": True, "_as": selfloop.note_prompt(evidence)}
        parts: List[str] = []
        try:
            async for piece in self._dispatch(inner):                   # type: ignore[attr-defined]
                if isinstance(piece, str):
                    parts.append(piece)
                yield piece
        except BackendError as e:
            selfloop.update(it.id, state="queued", run="", note=str(e)[:300])
            raise
        text, suggestions = selfloop.parse_note("".join(parts))
        fresh = self.runs.get(run["id"]) or run                         # type: ignore[attr-defined]
        note = selfloop.save_note(text, suggestions, run=run["id"], conversation=run["conversation_id"],
                                  backend=fresh.get("backend") or "")
        selfloop.update(it.id, state="done", run="", note="")
        if self.settings.get("notify_learned", True):                   # type: ignore[attr-defined]
            await self._notify("eki's weekly note",                         # type: ignore[attr-defined]
                               f"{len(suggestions)} suggestion{'s' if len(suggestions) != 1 else ''} "
                               "— Goals → Self")
        yield f"\n\n*eki: kept as the note of {note['id']} — its suggestions are on the board ([Self]({self._self_board()})).*"

    def _self_evidence(self) -> Dict[str, Any]:
        """The week, in numbers, for the note: nothing a model made up."""
        now, week = time.time(), 7 * 86400
        rows = observe_mod.entries(now - week)
        kinds = Counter(e.get("kind") for e in rows)
        summary = observe_mod.summary(7)
        friction = Counter(f"{e.get('signal')} · {e.get('backend') or '?'} · {e.get('task') or '?'}"
                           for e in rows if e.get("kind") == "friction")
        failed = Counter(f"{e.get('backend') or '?'}: {str(e.get('error') or '')[:80]}"
                         for e in rows if e.get("kind") == "failed")
        gaps = [{"why": str(e.get("why") or e.get("what") or "")[:200], "task": e.get("task"),
                 "request": str(e.get("request") or "")[:120]} for e in rows if e.get("kind") == "gap"][-8:]
        history = [str(e.get("what") or "") for e in rows if e.get("kind") == "history"][-20:]
        local = [b.key for b in self.backends if b.info.cost.tier == 0]   # type: ignore[attr-defined]
        busy = self.runs.busy_seconds(now - week)                       # type: ignore[attr-defined]
        local_s = sum(v for k, v in busy.items() if k in local)
        changes = [c for c in selfwork.changes() if float(c.get("at") or 0) >= now - week]
        plan = roadmap.read(self._self_root())
        items = roadmap.parse(plan)
        try:
            from . import mcpregistry
            servers = sorted(mcpregistry.load().keys())
        except Exception:                           # noqa: BLE001
            servers = []
        return {
            "week_ending": time.strftime("%Y-%m-%d", time.localtime(now)),
            "journal": dict(kinds),
            "faults": [{"where": f["signature"], "times": f["count"], "error": str(f.get("error") or "")[:160],
                        "fix": (f.get("proposal") or {}).get("state", "none")} for f in summary["faults"][:8]],
            "corrections_and_retries": friction.most_common(10),
            "providers_saying_no": failed.most_common(8),
            "requests_nothing_could_take": gaps,
            "notable": history,
            "work": {"busy_hours_by_backend": {k: round(v / 3600, 2) for k, v in busy.items()},
                     "local_hours": round(local_s / 3600, 2),
                     "local_share_of_the_week": round(local_s / week, 4),
                     "baseline_local_share": 0.01},
            "goals": self.goals_report(hours=168),                      # type: ignore[attr-defined]
            "self_changes": Counter(c["state"] for c in changes),
            "self_change_titles": [f"{c['state']}: {c.get('title')}" for c in changes[:12]],
            "roadmap": {**roadmap.counts(items),
                        "next": [f"{i.section.split(' — ')[0]}: {i.title}" for i in roadmap.workable(items)[:5]]},
            "providers": [{"key": b.key, "what": b.info.label, "tier": b.info.cost.tier}  # type: ignore[attr-defined]
                          for b in self.backends],                      # type: ignore[attr-defined]
            "mcp_servers": servers,
        }

    # ---- the goal ------------------------------------------------------------------------

    def _self_allowed(self, g: goals_mod.Goal, it: selfloop.Item) -> List[str]:
        """eki's own code is worked on by your subscriptions' spare room — the
        frontier harnesses — unless `self_local` lets the local models try too.
        The weekly note is writing: the local models may always write it."""
        allowed = self._goal_allowed(g)                                 # type: ignore[attr-defined]
        if it.source == "note" or self.settings.get("self_local", False):   # type: ignore[attr-defined]
            return allowed
        local = {b.key for b in self.backends if b.info.cost.tier == 0}     # type: ignore[attr-defined]
        return [k for k in allowed if k not in local]

    async def _self_plan(self, g: goals_mod.Goal, now: int) -> Union[str, Tuple[Any, ...]]:
        """The goal's next turn: (goal, backend, allowed, item) — or why there's none."""
        return await self._self_next(g)

    def _self_parallel(self) -> int:
        return max(1, min(8, int(self.settings.get("self_parallel", selfloop.PARALLEL))))   # type: ignore[attr-defined]

    def _self_busy(self) -> Dict[str, int]:
        """Self runs going now, by the backend each is on. A change waiting
        its turn in the merge queue holds none."""
        live = set(self.runner.running)                                 # type: ignore[attr-defined]
        out: Counter = Counter()
        for i in selfloop.items():
            if i.state == "working" and i.phase != "merging" and i.run in live:
                r = self.runs.get(i.run) or {}                          # type: ignore[attr-defined]
                out[r.get("backend") or _payload(r).get("planned") or r.get("requested") or "?"] += 1
        return dict(out)

    def _self_slots(self, allowed: List[str]) -> Dict[str, int]:
        """How many self runs each backend it may use can carry now. A
        subscription: what its spare room holds, never the part kept for you
        (capacity.spare) — one, until eki knows what a request costs there."""
        reserve = float(self.settings.get("background_reserve", shift_mod.RESERVE))   # type: ignore[attr-defined]
        data = capacity_mod.load()
        out: Dict[str, int] = {}
        for key in allowed:
            b = self.get(key)                                           # type: ignore[attr-defined]
            if b is None:
                continue
            if b.info.cost.tier == 0:
                out[key] = 1
                continue
            quota = getattr(self, "quota", None)
            reading = quota.latest.get(b.info.quota_source or "") if quota else None
            left = capacity_mod.spare(data, b.info.quota_source or "", reading.windows, reserve) \
                if reading is not None else None
            out[key] = 1 if left is None else int(left // SELF_COST)
        return out

    def _self_sweep(self) -> None:
        """Runs of self-work that ended in an error with nobody to notice (the
        ones beside the goal's turn): their items are tried again, or given
        up. And the merge queue forgets changes that aren't waiting any more."""
        live = set(self.runner.running)                                 # type: ignore[attr-defined]
        for i in selfloop.items():
            if i.state != "working" or not i.run or i.run in live:
                continue
            r = self.runs.get(i.run) or {}                              # type: ignore[attr-defined]
            if r.get("state") == "failed":
                attempts = i.attempts + 1
                selfloop.update(i.id, run="", attempts=attempts, open={}, phase="",
                                state="gave up" if attempts >= selfloop.MAX_ATTEMPTS else "queued",
                                note=(r.get("error") or "its run failed")[:300])
        items = {i.id: i for i in selfloop.items()}
        for row in selfloop.merge_queue():
            it = items.get(row.get("item") or "")
            if row.get("run") in live:
                continue
            if it is None or it.state != "working" or it.phase != "merging":
                selfloop.merge_leave(row["change"])

    async def _self_next(self, g: goals_mod.Goal, beside: bool = False) -> Union[str, Tuple[Any, ...]]:
        """The next piece of self-work that may start now: (goal, backend,
        allowed, item) — or why there's none. Held to `self_parallel`, to
        the subscriptions' spare room, and to an area nobody is working in.
        `beside`: next to the goal's turn — subscriptions only, since only
        the goal's own turn gives way when you need the machine."""
        why = self._self_why_not()
        if why:
            return why
        await asyncio.to_thread(self._self_reconcile)
        if selfloop.note_due(since=g.created_at):
            selfloop.add("note", "eki's weekly note")
        self._self_sweep()
        root = self._self_root()
        review_max = int(self.settings.get("self_review_max", selfloop.REVIEW_MAX))   # type: ignore[attr-defined]
        queued = {r["change"] for r in selfloop.merge_queue()}
        waiting = [c for c in selfwork.waiting() if c["id"] not in queued]
        it, why = selfloop.pick(roadmap.read(root), waiting=len(waiting), review_max=review_max,
                                live=self.runner.running,               # type: ignore[attr-defined]
                                parallel=self._self_parallel(),
                                files=await asyncio.to_thread(selfloop.repo_files, root), root=root)
        if it is None:
            return why
        allowed = self._self_allowed(g, it)
        local = {b.key for b in self.backends if b.info.cost.tier == 0}     # type: ignore[attr-defined]
        if beside:
            allowed = [k for k in allowed if k not in local]
        if allowed:
            more, free = selfloop.room(self._self_parallel(), self._self_slots(allowed), self._self_busy(), local)
            if more <= 0:
                return "no more room beside what's running" if beside else \
                    "what's working now takes all the spare room there is"
            allowed = [k for k in allowed if k in free]
        elif beside:
            return "no more room beside what's running"
        folder = "" if it.source == "note" else str(root)
        _, _, need, choice = await self._route(it.title, "", folder, allowed=allowed)   # type: ignore[attr-defined]
        if choice.backend is None:
            if beside:
                return "no more room beside what's running"
            if not allowed or not any(k not in local for k in allowed):
                if not (g.spare or self.settings.get("self_local", False)):     # type: ignore[attr-defined]
                    return "let it use your subscriptions' spare room (or the local models) to start"
                return "your subscriptions have no spare room right now (under pace, the last 30% kept for you)"
            return f"nothing it may use can take “{it.title[:60]}” right now"
        return (g, choice.backend.key, allowed, it)

    async def _self_beside(self, g: goals_mod.Goal) -> List[str]:
        """While the goal has its turn, more self-work beside it — as much as
        `self_parallel` and the spare room allow, each in its own area."""
        if g.state != "active":
            return []
        started: List[str] = []
        for _ in range(self._self_parallel() - 1):
            plan = await self._self_next(g, beside=True)
            if isinstance(plan, str):
                break
            _, key, allowed, it = plan
            got = await self._self_start(it, goal=g, allowed=allowed, planned=key)
            goals_mod.update(g.id, turns=goals_mod.get(g.id).turns + 1, last_turn_at=int(time.time()))
            task = self.runner.tasks.get(got["run"])                    # type: ignore[attr-defined]
            if task is not None:                    # the next starts the moment one ends
                task.add_done_callback(lambda _t: self.shift_wake.set())   # type: ignore[attr-defined]
            started.append(got["run"])
        return started

    @staticmethod
    def _self_status(g: goals_mod.Goal) -> str:
        """The board's badge for the self goal when it isn't working."""
        if "waiting for you" in g.note:
            return "waiting"
        if g.note.startswith("nothing to do"):
            return "idle"
        return "blocked"

    def _self_goal_finished(self, g: goals_mod.Goal, run: Dict[str, Any], entry: Dict[str, Any]) -> None:
        """A turn of the self goal ended (not stepped out): the next item is
        taken when there's room; three failed turns in a row pause it."""
        ok = run.get("state") == "done"
        fields: Dict[str, Any] = {"last_turn_at": int(time.time())}
        if ok:
            fields.update(failures=0, next_at=0, note="")
        else:
            failures = g.failures + 1
            fields["failures"] = failures
            if failures >= goals_mod.MAX_FAILURES:
                fields.update(state="stuck", note=f"{failures} turns in a row failed — see its threads")
            else:
                fields["next_at"] = int(time.time() + goals_mod.RETRY_SECONDS)
        goals_mod.update(g.id, **fields)
        entry["outcome"] = "done" if ok else "failed"
        if not ok:
            entry["error"] = (run.get("error") or "")[:200]
            p = _payload(run)
            try:
                it = selfloop.get(str(p.get("self_item") or ""))
                if it.state == "working" and it.run == run["id"]:
                    attempts = it.attempts + 1
                    selfloop.update(it.id, run="", attempts=attempts, open={}, phase="",
                                    state="gave up" if attempts >= selfloop.MAX_ATTEMPTS else "queued",
                                    note=(run.get("error") or "its run failed")[:300])
            except KeyError:
                pass

    def self_on(self, on: bool = True, spare: Optional[bool] = None) -> Dict[str, Any]:
        """Start (or pause) the goal that has eki work on itself when there's room."""
        why = self._self_why_not()
        if why and on:
            raise ValueError(why)
        g = self._self_goal()
        if g is None:
            if not on:
                return {"goal": None}
            g = goals_mod.create(selfloop.GOAL_TEXT, folder=str(self._self_root()),
                                 spare=True if spare is None else bool(spare), kind="self")
            observe_mod.note("history", what="eki works on itself: on", goal=g.id)
        else:
            fields: Dict[str, Any] = {"state": "active" if on else "paused"}
            if on:
                fields.update(note="", next_at=0, failures=0)
            if spare is not None:
                fields["spare"] = bool(spare)
            g = goals_mod.update(g.id, **fields)
        self.shift_wake.set()                                           # type: ignore[attr-defined]
        return {"goal": g.to_json()}

    # ---- deciding on a change -------------------------------------------------------------

    def _self_reconcile(self, force: bool = False) -> None:
        """Changes you merged or deleted by hand: their items follow."""
        for cid in selfwork.reconcile(force=force):
            try:
                c = selfwork.change(cid)
            except selfwork.SelfWorkError:
                continue
            it = self._self_item_of(c)
            if it is not None:
                selfloop.after_change(it, c)

    def _self_item_of(self, c: Dict[str, Any]) -> Optional[selfloop.Item]:
        try:
            return selfloop.get(c["item"]) if c.get("item") else None
        except KeyError:
            return None

    def _self_follow(self, cid: str) -> Dict[str, Any]:
        """After a decision: the change's item follows it, and the loop looks again."""
        c = selfwork.change(cid)
        it = self._self_item_of(c)
        if it is not None:
            selfloop.after_change(it, c)
        self._self_wake()
        return c

    async def self_apply(self, cid: str, confirmed: bool = False) -> Dict[str, Any]:
        """You apply a change (`eki self apply`, the board's Apply). One that
        touches what eki may not change alone goes in too — once you've
        confirmed, having been shown which protected files it touches."""
        c = selfwork.change(cid)
        if c.get("protected") and not confirmed:
            raise selfwork.SelfWorkError(
                "it touches what eki may not change alone: " + ", ".join(c["protected"])
                + " — confirm to apply it (`eki self apply " + c["id"] + "`, or Apply on the board)")
        got = await self._self_apply_now(cid, raising=True, by_person=True)
        if got.get("state") == "conflicts":
            # it no longer goes on top of your checkout: not handed back to
            # you — an agent resolves it, and eki applies it when that holds
            got = await self._self_resolve_start(got.get("id") or cid, person=True) or got
        self._self_follow(got.get("id") or cid)
        return got

    # ---- conflicts, resolved (eki/selfwork.py begin_rebase / finish_rebase) --------------

    async def _self_resolve_start(self, cid: str, person: bool = False) -> Optional[Dict[str, Any]]:
        """A run that has the change's conflicts resolved and then applies it,
        in the change's own thread. None when resolving is off. `person`: you
        asked for the apply, so it goes on as yours once resolved."""
        if not self.settings.get("self_resolve", True):                 # type: ignore[attr-defined]
            return None
        c = selfwork.change(cid)
        convo = c.get("conversation") or ""
        if not convo:
            title = f"eki · {c.get('title') or cid}"[:80]
            convo = self.store.new_conversation(title)                  # type: ignore[attr-defined]
            self.store.set_conversation(convo, title=title)             # type: ignore[attr-defined]
        shown = f"[eki · self] Resolving conflicts: {c.get('title') or 'self/' + c['id']}"[:200]
        turn = self.store.add_turn(convo, "user", shown, meta={"self": c["id"]})  # type: ignore[attr-defined]
        wrote = c.get("backend") or ""
        rid = self.runs.create(shown, conversation=convo, user_turn=turn,  # type: ignore[attr-defined]
                               requested=wrote if self._self_can_resolve(wrote) else "",
                               payload=json.dumps({"self_resolve": c["id"], "route": "resolve git conflicts",
                                                   "person": person}))
        selfwork.set_state(c["id"], "conflicts", resolving=rid, resolving_at=int(time.time()),
                           why="eki is having its conflicts resolved")
        await self.runner.submit(rid)                                   # type: ignore[attr-defined]
        return {"state": "conflicts", "id": c["id"], "resolving": rid, "conversation": convo,
                "why": "it conflicted with your checkout — eki is resolving it, then applies it"}

    def _self_can_resolve(self, key: str) -> bool:
        """The program that wrote the change resolves it, if it's a harness that's here."""
        b = self.get(key) if key else None                              # type: ignore[attr-defined]
        return b is not None and b.info.capabilities.tools and b.info.capabilities.repo

    async def _self_resolve(self, run: Dict[str, Any]) -> AsyncIterator[Piece]:
        """Put the change on top of your checkout; have an agent resolve what
        conflicts, in the change's worktree; finish the rebase; apply."""
        cid = str(_payload(run).get("self_resolve") or "")
        c = selfwork.change(cid)
        convo = run["conversation_id"]

        def give_up(why: str) -> str:
            selfwork.set_state(c["id"], "conflicts", resolving="", why=why[:300])
            self._self_follow(c["id"])
            return f"**eki · change to itself: still conflicts with your checkout.**\n\n{why}\n\n" \
                   f"Read it with `eki self diff {c['id']}`; `eki self retry` has eki make it again."
        yield "*eki: putting the change on top of your checkout…*\n\n"
        try:
            info = await asyncio.to_thread(selfwork.begin_rebase, c["id"])
        except selfwork.SelfWorkError as e:
            text = give_up(str(e))
            self.store.add_turn(convo, "assistant", text, "eki", "self-work",   # type: ignore[attr-defined]
                                meta={"run": run["id"], "self": c["id"]})
            yield text
            return
        if info["files"]:
            yield (f"*eki: it conflicts in {', '.join(info['files'])} — having them resolved in its "
                   "worktree…*\n\n")
            landed = await asyncio.to_thread(selfwork.landed_since, c["id"], info["onto"], info["files"])
            ask = selfwork.resolve_brief(c, info["files"], landed, sys.executable, info["where"])
            inner = {**run, "cwd": info["where"], "_self_inner": True, "_as": ask}
            parts: List[str] = []
            failed = ""
            try:
                async for piece in self._dispatch(inner):               # type: ignore[attr-defined]
                    if isinstance(piece, str):
                        parts.append(piece)
                    yield piece
            except BackendError as e:
                failed = f"the agent couldn't resolve it: {e}"
            fresh = self.runs.get(run["id"]) or run                     # type: ignore[attr-defined]
            why = failed or await asyncio.to_thread(
                selfwork.finish_rebase, c["id"], info, fresh.get("backend") or "",
                selfloop.reason("".join(parts), 500))
            if failed:
                await asyncio.to_thread(selfwork._put_back, Path(info["where"]), info.get("commit") or "")
            if why:
                text = give_up(f"eki had its conflicts resolved, but it didn't hold: {why}"
                               if not failed else why)
                self.store.add_turn(convo, "assistant", text, "eki", "self-work",  # type: ignore[attr-defined]
                                    meta={"run": run["id"], "self": c["id"]})
                yield "\n\n" + text
                return
            yield "\n\n*eki: resolved — judging it again on top of your checkout, then applying it…*\n"
        selfwork.set_state(c["id"], "conflicts", resolving="", why="")
        applied = await self._self_apply_now(c["id"], by_person=bool(_payload(run).get("person")))
        now = self._self_follow(c["id"])
        it = self._self_item_of(now) or _NoItem(now)
        text = self._self_says(now, it, applied)                        # type: ignore[arg-type]
        self.store.add_turn(convo, "assistant", text, "eki", "self-work",   # type: ignore[attr-defined]
                            meta={"run": run["id"], "self": c["id"]})
        yield "\n\n" + text
        if self.settings.get("notify_learned", True):                   # type: ignore[attr-defined]
            head = {"applying": "Applying", "applied": "Applied"}.get(now["state"], "Still waiting")
            await self._notify(f"eki · {head}: {now.get('title') or 'self/' + now['id']}"[:120],  # type: ignore[attr-defined]
                               "its conflicts with your checkout were resolved"[:200])

    async def self_discard(self, cid: str) -> Dict[str, Any]:
        await asyncio.to_thread(selfwork.discard, cid)
        return self._self_follow(selfwork.change(cid)["id"])

    async def self_undo(self, cid: str) -> Dict[str, Any]:
        """You take a change back: its revert, judged, then applied — you asked."""
        p = await asyncio.to_thread(selfwork.undo, cid, python=sys.executable, check=self.self_check)
        if not p.commit or not p.fit:
            return {"state": "not undone", "why": p.verdict, "id": p.id}
        got = await self._self_apply_now(p.id, raising=True)
        if got.get("state") == "applied":                              # documentation: no swap to wait for
            selfwork.set_state(selfwork.change(cid)["id"], "undone", by=p.id)
        self._self_follow(selfwork.change(cid)["id"])
        return {**got, "undo": p.id}

    def self_diff(self, cid: str) -> str:
        return selfwork.diff(cid)

    def self_settled(self, done: Dict[str, Any]) -> None:
        """The supervisor's outcome for a swap that carried a self change."""
        if not done.get("self"):
            return
        c = selfwork.settled(done["self"], done.get("state") or "", merged=done.get("merged") or "",
                             why=done.get("why") or "")
        if c:
            self._self_follow(done["self"])

    def self_item_action(self, iid: str, action: str) -> Dict[str, Any]:
        """drop (off the queue) · retry (take it again) · person (leave it for me)."""
        it = selfloop.get(iid)
        if action == "drop":
            it = selfloop.update(it.id, state="dropped", note="you took it off the list")
        elif action == "retry":
            it = selfloop.update(it.id, state="queued", attempts=0, note="", open={}, phase="", run="")
        elif action == "person":
            it = selfloop.update(it.id, state="person", note="you're doing this one", seen="")
        else:
            raise ValueError("drop, retry or person")
        self._self_wake()
        return it.to_json()

    def self_roadmap_action(self, key: str, action: str) -> Dict[str, Any]:
        """A ROADMAP item the loop hasn't taken yet: leave it for me, or skip it."""
        entry = roadmap.find(roadmap.read(self._self_root()), key)
        if entry is None:
            raise KeyError(key)
        it = selfloop.by_key(key, "roadmap") or selfloop.add("roadmap", entry.title, key=key)
        return self.self_item_action(it.id, action)

    def self_settings(self, **fields: Any) -> Dict[str, Any]:
        keep = {}
        if "autonomy" in fields:
            if fields["autonomy"] not in ("propose", "apply"):
                raise ValueError("autonomy is propose or apply")
            keep["self_autonomy"] = fields["autonomy"]
        if "areas" in fields:
            areas = fields["areas"] or {}
            if not isinstance(areas, dict) or any(v not in ("propose", "apply") for v in areas.values()):
                raise ValueError("areas map a path to propose or apply")
            keep["self_autonomy_areas"] = {str(k).strip(): v for k, v in areas.items() if str(k).strip()}
        if "review_max" in fields:
            keep["self_review_max"] = max(1, min(20, int(fields["review_max"])))
        if "local" in fields:
            keep["self_local"] = bool(fields["local"])
        if "parallel" in fields:
            keep["self_parallel"] = max(1, min(8, int(fields["parallel"])))
        self.settings = settings_mod.save({**settings_mod.load(), **keep})   # type: ignore[attr-defined]
        self._self_wake()
        return {k: self.settings.get(k) for k in keep}                  # type: ignore[attr-defined]

    async def self_note_now(self) -> Dict[str, Any]:
        it = next((i for i in selfloop.items() if i.source == "note" and i.state in ("queued", "working")), None)
        if it is not None and it.state == "working":
            return {"item": it.id, "run": it.run, "conversation": it.conversation}
        it = it or selfloop.add("note", "eki's weekly note")
        return {**await self._self_start(it), "item": it.id}

    async def self_suggestion(self, nid: str, index: int, action: str) -> Dict[str, Any]:
        """A suggestion from a weekly note, picked: made a request, put on the
        roadmap, or dismissed."""
        note = next((n for n in selfloop.notes() if n.get("id") == nid), None)
        if note is None:
            raise KeyError(nid)
        s = (note.get("suggestions") or [])[index]
        if action == "ask":
            got = await self.self_ask(s["do"], when="later", title=s["title"])
            selfloop.pick_suggestion(nid, index, "asked")
            return got
        if action == "roadmap":
            root = self._self_root()
            path = root / roadmap.NAME
            text = path.read_text()
            section = s.get("stage") or ""
            names = {i.section for i in roadmap.parse(text)}
            if section not in names:
                section = ""
            path.write_text(roadmap.add(text, section, f"**{s['title'].rstrip('.')}.** {s['do']}"
                                        if s["do"] != s["title"] else f"**{s['title'].rstrip('.')}.**"))
            got = await asyncio.to_thread(self._self_commit_roadmap, f"roadmap: {s['title'][:60]}")
            selfloop.pick_suggestion(nid, index, "roadmap")
            self._self_wake()
            return {"roadmap": got, "section": section or "Inbox"}
        if action == "dismiss":
            return selfloop.pick_suggestion(nid, index, "dismissed")
        raise ValueError("ask, roadmap or dismiss")

    def _self_commit_roadmap(self, message: str) -> str:
        """You picked it: into ROADMAP.md in your checkout, as its own commit."""
        root = self._self_root()
        selfwork.git(root, "add", roadmap.NAME)
        selfwork.git(root, "-c", "user.name=eki", "-c", "user.email=eki@localhost", "commit", "-q",
                     "--no-verify", "-m", f"{message}\n\nPicked from eki's weekly note.", "--", roadmap.NAME)
        return selfwork.git(root, "rev-parse", "--short", "HEAD")

    # ---- the view ------------------------------------------------------------------------

    def self_view(self) -> Dict[str, Any]:
        why_not = self._self_why_not()
        root = self._self_root()
        if not why_not:
            self._self_reconcile()
        g = self._self_goal()
        rows = selfwork.changes(limit=60)
        items = selfloop.items()
        text = roadmap.read(root) if not why_not else ""
        plan = roadmap.parse(text)
        known = {i.key: i for i in items if i.source == "roadmap"}
        upcoming = [e for e in roadmap.workable(plan)
                    if e.key not in known or known[e.key].state not in selfloop.SETTLED][:5]
        by_id = {c["id"]: c for c in rows}
        live = set(self.runner.running)                                 # type: ignore[attr-defined]

        def item_row(i: selfloop.Item) -> Dict[str, Any]:
            row = i.to_json()
            row.pop("open", None)
            row["live"] = i.run in live
            row["areas"] = [selfloop.lane_name(a) for a in i.area]
            if i.change and i.change in by_id:
                row["change_state"] = by_id[i.change]["state"]
            return row

        goal = None
        if g is not None:
            goal = g.to_json()
            goal["working"] = (self._shift_goal == g.id and self._shift_run in live) or \
                any(i.state == "working" and i.run in live for i in items)   # type: ignore[attr-defined]
        return {
            "can": not why_not, "why_not": why_not, "root": str(root),
            "running": builds_mod.running(),
            "going_live": builds_mod.going_live(),                     # a new version on its way in
            "goal": goal,
            "autonomy": self.settings.get("self_autonomy", "propose"),   # type: ignore[attr-defined]
            "areas": self.settings.get("self_autonomy_areas") or {},     # type: ignore[attr-defined]
            "review_max": int(self.settings.get("self_review_max", selfloop.REVIEW_MAX)),  # type: ignore[attr-defined]
            "local": bool(self.settings.get("self_local", False)),       # type: ignore[attr-defined]
            "parallel": self._self_parallel(),
            "merging": [{**r, "live": r.get("run") in live, "areas": [selfloop.lane_name(a) for a in r.get("area") or []]}
                        for r in selfloop.merge_queue()],
            "working": [item_row(i) for i in items if i.state == "working"],
            "queue": [item_row(i) for i in items if i.state == "queued"],
            "left": [item_row(i) for i in items if i.state in ("person", "gave up")],
            "waiting": [c for c in rows if c["state"] in ("proposed", "conflicts") and c.get("fit")],
            "changes": rows[:40],
            "roadmap": {**roadmap.counts(plan), "next": [e.to_json() for e in upcoming]},
            "note": selfloop.latest_note(),
            "shift": self._shift_state,                                 # type: ignore[attr-defined]
        }
