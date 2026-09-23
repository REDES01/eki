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
from . import goals as goals_mod
from . import observe as observe_mod
from . import roadmap
from . import selfloop
from . import selfwork
from . import settings as settings_mod
from .adapters.base import BackendError

Piece = Union[str, Dict[str, str]]
#: the goal with nothing to take waits this long before looking again (or until woken)
IDLE_RECHECK = 900


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
                          allowed: Optional[List[str]] = None) -> Dict[str, str]:
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
            payload.update(goal=goal.id, allowed=list(allowed or []))
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
        if it.open and Path(str(it.open.get("worktree") or "")).is_dir():
            prop = selfwork.Proposal(**{k: v for k, v in it.open.items()
                                        if k in selfwork.Proposal.__dataclass_fields__})
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
            it = selfloop.update(it.id, phase="checking", open={
                **prop.to_json(), "agent_state": state, "reason": selfloop.reason(answer)})
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
            mode = "apply" if it.apply else selfloop.autonomy_for(prop.files, self.settings)  # type: ignore[attr-defined]
            if mode == "apply":
                try:
                    applied = await asyncio.to_thread(selfwork.apply, prop.id, python=python,
                                                      check=self.self_check)
                except (selfwork.SelfWorkError, ValueError, RuntimeError) as e:
                    applied = {"state": "proposed", "why": str(e)[:300]}
        async for piece in self._self_close(run, it, prop, applied):
            yield piece

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
            await self._notify(f"eki · {head}: {it.title}"[:120],           # type: ignore[attr-defined]
                               (c.get("verdict") or c["state"])[:200])
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
        if c.get("commit"):
            files = c.get("files") or []
            who = f"written by {c.get('backend')}" if c.get("backend") and c.get("backend") != "eki" else "made by eki"
            lines.append(f"`self/{cid}` — {who}; {len(files)} file{'s' if len(files) != 1 else ''}: "
                         + ", ".join(f"`{f}`" for f in files[:6]) + (" …" if len(files) > 6 else ""))
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
            lines.append("The supervisor swaps it in once nothing is running, watches it, and goes "
                         "back to what ran before if it isn't healthy.")
        elif state == "applied":
            lines.append(str(c.get("how") or c.get("merged") or "In your checkout."))
        elif state in ("proposed", "conflicts") and not c.get("protected"):
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
        why = self._self_why_not()
        if why:
            return why
        if selfloop.note_due(since=g.created_at):
            selfloop.add("note", "eki's weekly note")
        review_max = int(self.settings.get("self_review_max", selfloop.REVIEW_MAX))   # type: ignore[attr-defined]
        it, why = selfloop.pick(roadmap.read(self._self_root()), waiting=len(selfwork.waiting()),
                                review_max=review_max, live=self.runner.running)   # type: ignore[attr-defined]
        if it is None:
            return why
        allowed = self._self_allowed(g, it)
        folder = "" if it.source == "note" else str(self._self_root())
        _, _, need, choice = await self._route(it.title, "", folder, allowed=allowed)   # type: ignore[attr-defined]
        if choice.backend is None:
            if not allowed or not any(k not in {b.key for b in self.backends if b.info.cost.tier == 0}  # type: ignore[attr-defined]
                                      for k in allowed):
                if not (g.spare or self.settings.get("self_local", False)):     # type: ignore[attr-defined]
                    return "let it use your subscriptions' spare room (or the local models) to start"
                return "your subscriptions have no spare room right now (under pace, the last 30% kept for you)"
            return f"nothing it may use can take “{it.title[:60]}” right now"
        return (g, choice.backend.key, allowed, it)

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

    async def self_apply(self, cid: str) -> Dict[str, Any]:
        got = await asyncio.to_thread(selfwork.apply, cid, python=sys.executable, check=self.self_check)
        self._self_follow(got.get("id") or cid)
        return got

    async def self_discard(self, cid: str) -> Dict[str, Any]:
        await asyncio.to_thread(selfwork.discard, cid)
        return self._self_follow(selfwork.change(cid)["id"])

    async def self_undo(self, cid: str) -> Dict[str, Any]:
        """You take a change back: its revert, judged, then applied — you asked."""
        p = await asyncio.to_thread(selfwork.undo, cid, python=sys.executable, check=self.self_check)
        if not p.commit or not p.fit:
            return {"state": "not undone", "why": p.verdict, "id": p.id}
        got = await asyncio.to_thread(selfwork.apply, p.id, python=sys.executable, check=self.self_check)
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
            it = selfloop.update(it.id, state="person", note="you're doing this one")
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
            if i.change and i.change in by_id:
                row["change_state"] = by_id[i.change]["state"]
            return row

        goal = None
        if g is not None:
            goal = g.to_json()
            goal["working"] = self._shift_goal == g.id and self._shift_run in live   # type: ignore[attr-defined]
        return {
            "can": not why_not, "why_not": why_not, "root": str(root),
            "running": builds_mod.running(),
            "goal": goal,
            "autonomy": self.settings.get("self_autonomy", "propose"),   # type: ignore[attr-defined]
            "areas": self.settings.get("self_autonomy_areas") or {},     # type: ignore[attr-defined]
            "review_max": int(self.settings.get("self_review_max", selfloop.REVIEW_MAX)),  # type: ignore[attr-defined]
            "local": bool(self.settings.get("self_local", False)),       # type: ignore[attr-defined]
            "working": [item_row(i) for i in items if i.state == "working"],
            "queue": [item_row(i) for i in items if i.state == "queued"],
            "left": [item_row(i) for i in items if i.state in ("person", "gave up")],
            "waiting": [c for c in rows if c["state"] in ("proposed", "conflicts") and c.get("fit")],
            "changes": rows[:40],
            "roadmap": {**roadmap.counts(plan), "next": [e.to_json() for e in upcoming]},
            "note": selfloop.latest_note(),
            "shift": self._shift_state,                                 # type: ignore[attr-defined]
        }
