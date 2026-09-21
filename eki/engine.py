# SPDX-License-Identifier: Apache-2.0
"""The part that isn't a CLI or a window.

Owns the backends, the router, the conversation store and the runs, so that
`eki ask` in a terminal and the Mac app are two views of the same engine —
one routing decision, one history, and work that outlives whichever of them
asked for it.

A request is never answered *inside* a request. `ask` writes the question
down, starts a run, and returns; the answer is produced by the engine and
streamed to anyone watching. That is the whole trick behind "close the window
and it keeps going": nothing about the work belongs to the window.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote

from . import config as config_mod
from . import policy as policy_mod
from . import priors
from . import public_scores
from .adapters import base as adapters
from .adapters.base import Backend, BackendError, Health, Message
from . import deploy as deploy_mod
from . import bench
from . import classify
from . import discover_models
from . import live
from . import measure
from . import secrets
from . import settings as settings_mod
from . import titles
from .models import DEFAULT_IDLE_MINUTES, LocalModel, ModelManager
from .capability import SOLID_ITEMS, Registry
from .providers import Provider, ProviderStore, seed_from_config
from .quota import QuotaBoard, QuotaProvider
from .quota import claude_bridge
from .quota.claude import ClaudeStatusLine
from .quota.codex import CodexAppServer
from .router import Need, Router
from .runs import Runner, RunStore
from .store import Store

HEALTH_TTL = 20.0


class Engine:
    def __init__(self, cfg, owner: bool = False, port: int = 8787):
        #: where the service listens; Codex is pointed here for local models
        self.port = port
        """`owner=True` only for the service: the process that executes runs."""
        self.cfg = cfg
        self.store = Store(cfg.db)
        # same file the job queue used, so its history carries over
        self.runs = RunStore(Path(cfg.db).expanduser().with_name("jobs.db"),
                             owner=owner)
        # Providers live in the database; config.yaml only seeds a fresh one.
        self.providers = ProviderStore(cfg.db)
        if self.providers.empty():
            for provider in seed_from_config(cfg):
                self.providers.upsert(provider)
        self.policy = policy_mod.load()
        self._build()
        self.runner = Runner(self.runs, self._dispatch)
        self._health: Dict[str, Tuple[float, Any]] = {}
        self._side_tasks: set = set()               # titles and the like
        #: Claude Code kept open per conversation (see eki/live.py)
        self.live: Dict[str, live.LiveSession] = {}
        #: a question or permission prompt a run is waiting on: run id → event
        self.pending: Dict[str, Dict[str, Any]] = {}
        #: sessions opened ahead of a thread (for the composer's command list,
        #: and a faster first answer), by folder; adopted by the first run
        self.warm: Dict[str, live.LiveSession] = {}

    # ---- backends ----------------------------------------------------

    def _build(self) -> None:
        """Backends, local runtimes, quota and router, from the provider table."""
        self.backends: List[Backend] = []
        self.failed: Dict[str, str] = {}
        self.options: Dict[str, Dict[str, Any]] = {}
        local: List[LocalModel] = []
        for p in self.providers.all():
            if p.runtime.get("port"):
                r = p.runtime
                local.append(LocalModel(
                    key=p.key, label=r.get("label") or p.label, port=int(r["port"]),
                    start=r.get("start", ""), stop=r.get("stop", ""),
                    gb=float(r.get("gb", 0) or 0), note=r.get("note", ""),
                    backend=p.key, kind=r.get("kind", "llm"),
                    idle_minutes=float(r.get("idle_minutes", DEFAULT_IDLE_MINUTES) or 0)))
            if not p.enabled:
                continue
            options = dict(p.options)
            if options.get("secret"):
                key = secrets.get(p.key)
                if not key:
                    self.failed[p.key] = "no API key in the Keychain"
                    continue
                options["api_key"] = key
            self.options[p.key] = options
            try:
                self.backends.append(adapters.build(p.info(), options))
            except Exception as e:                  # noqa: BLE001
                self.failed[p.key] = str(e)
        previous = getattr(self, "models", None)
        self.models = ModelManager(local, self.cfg.memory_ceiling_gb)
        if previous is not None:                    # keep who-started-what across reloads
            self.models.adopt(previous)
        self.quota = QuotaBoard(self._quota_providers(),
                                ceiling=self.policy.quota_ceiling or self.cfg.quota_ceiling)
        self.settings = settings_mod.load()
        self.classifier = self._classifier()
        if not hasattr(self, "registry"):
            self.registry = Registry(self.cfg.db)
        self._companions()
        self.router = Router(self.backends, self.quota, policy=self.policy,
                             is_up=self._is_up,
                             reserved={self.settings["router_model"]}
                             if self.settings["router_model"] else set(),
                             models_for=self.registry.for_provider)
        self._health = {}

    def _companions(self) -> None:
        """Codex driving each local model: derived, never stored.

        A local model has no harness; Codex has one and takes any provider
        that speaks the Responses API, which eki does for its own models
        (eki/gateway.py). So every local text model big enough to be worth
        it gets a "codex-<key>" backend: free, offline, edits files, and in
        the running for repo work like any other. Added or removed with the
        model itself.
        """
        codex = next((b for b in self.backends if b.info.kind == "codex"
                      and getattr(b, "bin", None)), None)
        if codex is None:
            return
        reserved = self.settings.get("router_model") or ""
        for p in self.providers.all():
            if (not p.enabled or p.kind not in ("mlx", "openai_compat") or not p.runtime.get("port")
                    or p.runtime.get("kind", "llm") != "llm" or not p.options.get("model")
                    or p.key == reserved):
                continue
            klass = priors.AGENT_OF.get(
                priors.class_of_model(p.kind, str(p.options["model"]), p.options), "")
            if not klass:
                continue                            # a 2B can't carry a harness
            key = f"codex-{p.key}"
            if self.get(key) or self.providers.get(key):
                continue
            context = int(p.capabilities.get("context_tokens") or 32000)
            info = adapters.BackendInfo(
                key=key, kind="codex", label=f"Codex on {p.label}",
                capabilities=adapters.Capabilities(context_tokens=context, text=True,
                                                   tools=True, repo=True),
                cost=adapters.Cost(tier=0, note="local, through Codex"))
            options = {**self.options.get(codex.key, {}), "model": p.key, "local_model": p.key,
                       "gateway": f"http://127.0.0.1:{self.port}/v1", "context_tokens": context}
            options.pop("secret", None)
            try:
                self.backends.append(adapters.build(info, options))
            except Exception as e:                  # noqa: BLE001
                self.failed[key] = str(e)
                continue
            self.options[key] = options
            # one model behind it — the local one; anything else listed under
            # this key (Codex's own models, from an earlier discovery) goes
            for rec in self.registry.for_provider(key, enabled_only=False):
                if rec.model:
                    self.registry.remove(key, rec.model)
            self.registry.seen(key, "", label=info.label, context_tokens=context, klass=klass,
                               source="derived",
                               public=public_scores.lookup(p.kind, str(p.options["model"])))

    def _local_for(self, key: str) -> Optional[LocalModel]:
        """The local server behind a backend: its own, or the one a Codex
        companion drives."""
        local_key = self.options.get(key, {}).get("local_model") or key
        return self.models.for_backend(local_key)

    def _classifier(self) -> classify.Classifier:
        """The small local model that labels requests, if one is set up."""
        key = self.settings.get("router_model") or ""
        provider = self.providers.get(key) if key else None
        if provider is None:
            return classify.Classifier(None, use_model=False)
        base = str(provider.options.get("base_url", "")).rstrip("/")
        model = str(provider.options.get("model", ""))
        return classify.Classifier(classify.ModelClassifier(base, model),
                                   use_model=self.settings.get("router") == "model")

    async def discover_models(self) -> Dict[str, List[str]]:
        """Ask every reachable provider which models it offers."""
        found: Dict[str, List[str]] = {}
        for b in list(self.backends):
            if self._is_up(b.key) is False:
                continue
            default = ""
            if b.info.kind == "claude_code":
                reading = claude_bridge.reading() or {}
                default = str(reading.get("model") or "")
            try:
                found[b.key] = await discover_models.discover(b, self.registry, default)
            except Exception:                       # noqa: BLE001
                found[b.key] = []
        return found

    async def reload(self) -> None:
        """Pick up provider changes without restarting: runs in flight keep
        the adapter instance they were built with."""
        old_quota, old_backends = self.quota, self.backends
        self._build()
        await old_quota.stop()
        self.quota.start()
        for b in old_backends:
            try:
                await b.close()
            except Exception:                       # noqa: BLE001
                pass

    def get(self, key: str) -> Optional[Backend]:
        return next((b for b in self.backends if b.key == key), None)

    def _quota_providers(self) -> List[QuotaProvider]:
        """One quota source per provider a backend spends, read through that
        provider's own sanctioned interface — never through its credentials."""
        wanted = {b.info.quota_source: b for b in self.backends if b.info.quota_source}
        out: List[QuotaProvider] = []
        if "claude" in wanted:
            out.append(ClaudeStatusLine("claude"))
        if "codex" in wanted:
            out.append(CodexAppServer("codex", binary=getattr(wanted["codex"], "bin", "") or ""))
        return out

    def _is_up(self, key: str) -> Optional[bool]:
        """Cheap liveness for routing: a port check, or nothing.

        Only local servers can be answered for free; for a CLI backend this
        returns None, which the router reads as "don't know" rather than
        "down". A slow health probe has no business inside a routing decision.
        """
        model = self._local_for(key)
        if model is None:
            return None
        if model.running:
            return True
        # stopped, but eki can bring it up: still a candidate, started on
        # demand — otherwise a free local model loses every request to a paid
        # one just because it was idle-unloaded
        return None if self.models.can_start(model.key, eager=True) else False

    def set_policy(self, policy) -> None:
        self.policy = policy
        self.router.policy = policy
        if self.router.quota:
            self.router.quota.ceiling = policy.quota_ceiling or self.cfg.quota_ceiling
        policy_mod.save(policy)

    async def health(self, key: str) -> Any:
        hit = self._health.get(key)
        if hit and time.time() - hit[0] < HEALTH_TTL:
            return hit[1]
        backend = self.get(key)
        if backend is None:
            return None
        try:
            result = await backend.health()
        except Exception as e:                      # noqa: BLE001
            result = Health(False, str(e)[:200])
        self._health[key] = (time.time(), result)
        return result

    async def describe(self) -> List[Dict[str, Any]]:
        out = []
        for b in self.backends:
            h = await self.health(b.key)
            caps = b.info.capabilities
            out.append({
                "key": b.key,
                "label": b.info.label,
                "kind": b.info.kind,
                "tier": b.info.cost.tier,
                "cost_note": b.info.cost.note,
                "quota_source": b.info.quota_source,
                "role": "router" if b.key == self.settings.get("router_model") else "",
                "capabilities": {
                    "repo": caps.repo, "tools": caps.tools, "vision": caps.vision,
                    "images_out": caps.images_out, "text": caps.text,
                    "context_tokens": caps.context_tokens,
                },
                "ok": bool(h and h.ok),
                "detail": h.detail if h else "",
            })
        for key, why in self.failed.items():
            out.append({"key": key, "label": key, "kind": "?", "tier": 999, "role": "",
                        "ok": False, "detail": f"unavailable: {why}",
                        "capabilities": {}, "cost_note": "", "quota_source": ""})
        return out

    # ---- asking ------------------------------------------------------

    async def ask(self, prompt: str, *, conversation: str = "", backend_key: str = "",
                  repo: str = "", images: bool = False) -> Dict[str, str]:
        """Write the question down and start answering it. Returns at once.

        The question is stored before anything runs, so a conversation
        reopened mid-answer already shows what was asked.
        """
        cid = conversation or self.store.new_conversation()
        turn = self.store.add_turn(cid, "user", prompt)
        rid = self.runs.create(prompt, conversation=cid, cwd=repo,
                               requested=backend_key, images=images, user_turn=turn)
        await self.runner.submit(rid)
        return {"run": rid, "conversation": cid}

    async def retry(self, rid: str) -> Optional[Dict[str, str]]:
        """Answer the same question again, as a new run.

        Deliberately manual. An interrupted run that had a folder may have
        already made some of its edits; replaying it by itself on restart
        could make them twice. Retrying reuses the original question's turn,
        so the thread doesn't grow a duplicate of it.
        """
        old = self.runs.get(rid)
        if not old or old["state"] not in ("failed", "cancelled", "interrupted"):
            return None
        new = self.runs.create(old["prompt"], conversation=old["conversation_id"],
                               cwd=old["cwd"], requested=old["requested"],
                               images=bool(old["images"]), user_turn=old["user_turn"])
        await self.runner.submit(new)
        return {"run": new, "conversation": old["conversation_id"]}

    async def _dispatch(self, run: Dict[str, Any]
                        ) -> AsyncIterator[Union[str, Dict[str, str]]]:
        """One run, start to finish. Yields a routing note, then the answer.

        Everything the conversation needs is written here, by the engine, so
        it happens whether or not anyone is watching.
        """
        if run.get("kind") == "deploy":
            async for piece in self._deploy(run):
                yield piece
            return
        if run.get("kind") == "measure":
            async for piece in self._measure(run):
                yield piece
            return

        cid = run["conversation_id"]
        shown = self._picture_before(run)
        label = await self._label(run, after_image=bool(shown))
        # "claude" asks for the provider; "claude:opus" for one of its models
        requested, _, wanted_model = (run["requested"] or "").partition(":")
        need = Need(repo=bool(run["cwd"]), tools=bool(run["cwd"]),
                    images_out=bool(run["images"]) or label.task == "image",
                    backend=requested or None,
                    task=label.task, difficulty=label.difficulty)
        choice = self.router.choose(need)
        if choice.backend is not None and wanted_model:
            choice.model = wanted_model
            choice.reason = f"{choice.backend.key} ({wanted_model}): asked for by name"
        if choice.backend is None:
            why = choice.reason
            if choice.rejected:
                why += " — " + "; ".join(choice.rejected)
            if cid:
                self.store.add_turn(cid, "assistant", f"[{why}]", "", choice.reason,
                                    meta={"run": run["id"], "failed": True})
            raise BackendError(why)

        reason = choice.reason
        model = self._local_for(choice.backend.key)
        meta_label = label.to_json()
        if model is not None and not model.running:
            reason += f"; starting {model.label}"
        yield {"backend": choice.backend.key, "reason": reason}
        if model is not None:
            if not model.running:
                # the user is waiting on this one: it may unload a server that
                # was answering a minute ago, which background work may not
                message = await self.models.start(model.key, eager=True)
                if not model.running:
                    raise BackendError(message)
                if "make room" in message:
                    yield {"backend": choice.backend.key, "reason": reason + "; " +
                           message[message.index("(") + 1:-1]}
            self.models.hold(model.key)

        # A fresh instance per run. Adapters keep per-call state — the
        # session id to resume, the token usage — on themselves, and two runs
        # on one shared instance would hand each other their sessions.
        options = dict(self.options.get(choice.backend.key, {}))
        if choice.model:
            options["model"] = choice.model
        backend = adapters.build(choice.backend.info, options)

        # Everything up to and including this run's question — and nothing a
        # parallel run in the same conversation added after it.
        history = [Message(t["role"], t["content"]) for t in self.store.turns(cid)
                   if t["id"] <= run["user_turn"]] if cid else []
        if not history:
            history = [Message("user", run["prompt"])]

        kw: Dict[str, Any] = {}
        if run["cwd"]:
            kw["cwd"] = run["cwd"]
        # "make it bluer", said to a picture: the image model is handed the
        # picture to change. "try again" repeats whatever made it, anew.
        drawn: Dict[str, str] = {}
        if backend.info.capabilities.images_out and not backend.info.capabilities.text:
            drawn = {"prompt": run["prompt"], "source": ""}
            follow = classify.image_followup(run["prompt"]) if shown else ""
            if follow == "edit":
                drawn["source"] = shown["path"]
            elif follow == "redo" and shown.get("prompt"):
                drawn = {"prompt": shown["prompt"], "source": shown.get("source", "")}
            if drawn["source"] and not os.path.isfile(drawn["source"]):
                drawn["source"] = ""
            kw["prompt"] = drawn["prompt"]
            if drawn["source"]:
                kw["edit"] = drawn["source"]
        resumed = self.store.session(cid, backend.key) if cid else None
        if resumed:
            kw["resume"] = resumed

        meta: Dict[str, Any] = {"run": run["id"], "label": meta_label}
        if run["cwd"]:
            meta["cwd"] = run["cwd"]
        if drawn:
            meta["image"] = drawn               # what a later "try again" repeats
        parts: List[str] = []
        stream = (self._live_turn(run, cid, backend, choice.model)
                  if backend.info.kind == "claude_code" and self.settings.get("live_claude", True)
                  else backend.stream(history, **kw))
        try:
            async for chunk in stream:
                if isinstance(chunk, str):
                    parts.append(chunk)
                yield chunk
        except BackendError as e:
            if cid:
                self.store.add_turn(cid, "assistant", "".join(parts) or f"[failed: {e}]",
                                    backend.key, choice.reason,
                                    meta={**meta, "failed": True})
            raise
        except (asyncio.CancelledError, GeneratorExit):
            # stopped on purpose: keep what arrived, and say it was cut short
            if cid and parts:
                self.store.add_turn(cid, "assistant", "".join(parts) + "\n\n*[stopped]*",
                                    backend.key, choice.reason,
                                    meta={**meta, "stopped": True})
            raise
        finally:
            try:
                await backend.close()
            except Exception:                       # noqa: BLE001
                pass
            if model is not None:
                self.models.release(model.key)

        # usage is whatever the backend volunteered, normalised only in name:
        # an invented number would be worse than an absent one
        usage = dict(getattr(backend, "last_usage", {}) or {})
        if "prompt_tokens" in usage:                # OpenAI-shaped → our names
            usage.setdefault("input_tokens", usage.pop("prompt_tokens"))
            usage.setdefault("output_tokens", usage.pop("completion_tokens", 0))
        if usage:
            meta["usage"] = usage
        if cid:
            self.store.add_turn(cid, "assistant", "".join(parts), backend.key,
                                choice.reason, meta=meta)
            session = getattr(backend, "last_session", None)
            if session:
                self.store.set_session(cid, backend.key, session)
            # name the thread now that it has an answer in it; not on the
            # run's clock, and never a reason for the run to fail
            task = asyncio.create_task(self._entitle(cid))
            self._side_tasks.add(task)
            task.add_done_callback(self._side_tasks.discard)

    # ---- Claude Code, kept open --------------------------------------

    async def _live_session(self, cid: str, backend: Backend, cwd: str) -> live.LiveSession:
        """The conversation's session, started or resumed as needed."""
        session = self.live.get(cid)
        if session is not None and session.alive:
            return session
        resume = self.store.session(cid, backend.key) if cid else None
        warm = self.warm.pop(cwd or "", None)
        if warm is not None and warm.alive and not warm.busy and not resume:
            if cid:
                self.live[cid] = warm
            return warm
        if warm is not None:
            await warm.close()
        import uuid
        argv = backend.live_argv(cwd or None, resume, str(uuid.uuid4()))   # type: ignore[attr-defined]
        session = live.LiveSession(argv, cwd or None, None,
                                   str(self.settings.get("claude_system_prompt", "")))
        try:
            await session.start()
        except RuntimeError as e:
            if resume:
                # the old session is gone (deleted, or another machine's): start over
                argv = backend.live_argv(cwd or None, None, str(uuid.uuid4()))  # type: ignore[attr-defined]
                session = live.LiveSession(argv, cwd or None, None,
                                           str(self.settings.get("claude_system_prompt", "")))
                await session.start()
            else:
                raise BackendError(str(e))
        if cid:
            self.live[cid] = session
        return session

    async def _live_turn(self, run: Dict[str, Any], cid: str, backend: Backend,
                         model: str) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """One turn through the open session: text as it streams, tool
        activity as lines, questions and permission prompts as events the
        app turns into cards and answers through `answer()`."""
        session = await self._live_session(cid, backend, run["cwd"] or "")
        if model and session.model and model not in session.model:
            try:
                await session.set_model(model)
            except RuntimeError:
                pass
        rid = run["id"]
        usage: Dict[str, Any] = {}
        try:
            await session.send(run["prompt"])
            async for ev in session.turn():
                kind = ev["kind"]
                if kind == "text":
                    yield ev["text"]
                elif kind == "activity":
                    yield {"kind": "activity", "text": live.summarize_activity(ev["tool"], ev["input"])}
                elif kind == "note":
                    yield {"kind": "activity", "text": ev["text"]}
                elif kind in ("ask", "permission"):
                    self.pending[rid] = {**ev, "run": rid}
                    yield {"kind": kind, **{k: v for k, v in ev.items() if k != "kind"}}
                elif kind == "cancel":
                    self.pending.pop(rid, None)
                    yield {"kind": "cancel", "request_id": ev["request_id"]}
                elif kind == "rate_limit":
                    self._note_rate_limits(ev["info"])
                elif kind == "result":
                    usage = ev.get("usage") or {}
                    if ev.get("is_error"):
                        raise BackendError(str(ev.get("result") or "Claude Code reported an error")[:300])
                elif kind == "exit":
                    self.live.pop(cid, None)
                    raise BackendError(f"Claude Code stopped: {ev.get('error', '')}"[:300])
        except (asyncio.CancelledError, GeneratorExit):
            self.pending.pop(rid, None)
            if session.alive:
                await session.interrupt()
            raise
        except RuntimeError as e:
            raise BackendError(str(e))
        finally:
            self.pending.pop(rid, None)
        backend.last_usage = usage                                          # type: ignore[attr-defined]
        backend.last_session = session.session_id                           # type: ignore[attr-defined]

    def _note_rate_limits(self, info: Dict[str, Any]) -> None:
        """Claude Code reports its windows as it goes; the quota board may
        use them (see quota.claude) — for now they're kept on the engine."""
        self.last_rate_limits = info

    async def answer(self, rid: str, request_id: str, response: Dict[str, Any]) -> bool:
        """The user's answer to a question or permission prompt of a run."""
        pending = self.pending.get(rid)
        if not pending or pending.get("request_id") != request_id:
            return False
        run = self.runs.get(rid) or {}
        session = self.live.get(run.get("conversation_id", ""))
        if session is None:
            return False
        ok = await session.answer(request_id, response)
        if ok:
            self.pending.pop(rid, None)
        return ok

    async def commands_for(self, cid: str = "", cwd: str = "") -> List[Dict[str, Any]]:
        """The slash commands for a thread; for a thread not started yet, a
        session is opened ahead for its folder and kept for the first run."""
        session = self.live.get(cid) if cid else None
        if session is None or not session.alive:
            session = self.warm.get(cwd or "")
        if session is None or not session.alive:
            backend = next((b for b in self.backends if b.info.kind == "claude_code"
                            and getattr(b, "bin", None)), None)
            if backend is None or not self.settings.get("live_claude", True):
                return []
            import uuid
            argv = backend.live_argv(cwd or None, None, str(uuid.uuid4()))   # type: ignore[attr-defined]
            session = live.LiveSession(argv, cwd or None, None,
                                       str(self.settings.get("claude_system_prompt", "")))
            try:
                await session.start()
            except RuntimeError:
                return []
            self.warm[cwd or ""] = session
        return list(session.commands)

    async def reap_live(self, now: Optional[float] = None) -> int:
        """Close sessions idle for a while; they resume by id when needed."""
        now = now or time.time()
        closed = 0
        for table in (self.live, self.warm):
            for key, session in list(table.items()):
                if not session.busy and now - session.last_used > live.IDLE_SECONDS:
                    await session.close()
                    table.pop(key, None)
                    closed += 1
                elif not session.alive:
                    table.pop(key, None)
        return closed

    def _titler(self, exclude: str = "") -> Optional[Backend]:
        """A local model that's up and answers in words, cheapest first —
        the router model if it's loaded, else whatever local server is."""
        wanted = []
        router = self.settings.get("router_model") or ""
        if router:
            wanted.append(router)
        wanted += [b.key for b in sorted(self.backends, key=lambda b: b.info.cost.tier)
                   if b.info.cost.tier == 0 and b.info.capabilities.text]
        for key in wanted:
            backend = self.get(key)
            if backend is None or key == exclude:
                continue
            if self._is_up(key) is not True:
                continue
            return adapters.build(backend.info, self.options.get(key, {}))
        return None

    async def _entitle(self, cid: str, force: bool = False) -> bool:
        if self.store.title_source(cid) == "user":
            return False
        turns = self.store.turns(cid)
        answers = sum(1 for t in turns if t["role"] == "assistant")
        # once when the thread gets its first answer, again after it's grown
        if not answers or (not force and answers not in (1, 4)):
            return False
        backend = self._titler()
        if backend is None:
            return False
        try:
            title = await titles.suggest(backend, turns)
        except Exception:                           # noqa: BLE001
            return False
        return bool(title) and self.store.suggest_title(cid, title)

    async def backfill_titles(self) -> int:
        """Name the threads that still carry their first line — one at a
        time, in the background, only while a local model is up."""
        named = 0
        for row in self.store.untitled():
            if self._titler() is None:
                break
            if await self._entitle(row["id"], force=True):
                named += 1
            await asyncio.sleep(0.5)                # never a burst
        return named

    _PICTURE = re.compile(r"!\[[^\]]*\]\(([^)\s]+\.(?:png|jpe?g|webp))\)", re.I)

    def _picture_before(self, run: Dict[str, Any]) -> Dict[str, str]:
        """The picture this request was said to, if the last answer was one.

        Only the answer immediately before counts: once the thread has moved
        on to words, "make it shorter" is about the words. Returns its path
        and, when known, the request that drew it and what it was drawn from."""
        cid = run.get("conversation_id")
        if not cid:
            return {}
        try:
            before = [t for t in self.store.turns(cid) if t["id"] < run["user_turn"]]
        except Exception:                           # noqa: BLE001
            return {}
        last = next((t for t in reversed(before) if t["role"] == "assistant"), None)
        if last is None:
            return {}
        backend = self.get(last["backend"] or "")
        if backend is None or not backend.info.capabilities.images_out:
            return {}
        found = self._PICTURE.findall(last["content"] or "")
        if not found:
            return {}
        try:
            made = (json.loads(last["meta"] or "{}") or {}).get("image") or {}
        except (TypeError, ValueError):
            made = {}
        asked = next((t["content"] for t in reversed(before)
                      if t["role"] == "user" and t["id"] < last["id"]), "")
        return {"path": unquote(found[-1]), "prompt": made.get("prompt") or asked,
                "source": made.get("source") or ""}

    async def _label(self, run: Dict[str, Any], after_image: bool = False) -> classify.Label:
        """What kind of request this is. Never allowed to fail a run."""
        try:
            label = await self.classifier.label(run["prompt"], has_folder=bool(run["cwd"]),
                                                after_image=after_image)
        except Exception:                           # noqa: BLE001
            label = classify.rules(run["prompt"], bool(run["cwd"]), after_image)
        self.runs.update(run["id"], label=json.dumps(label.to_json()))
        key = self.settings.get("router_model") or ""
        if key and self.classifier.use_model:
            model = self.models.for_backend(key)
            if model is not None and not model.running and self.models.can_start(key):
                # the labeller was asleep; wake it for next time rather than
                # making this request wait for it
                asyncio.create_task(self.models.start(key))
            elif model is not None:
                self.models.touch(key)
        return label

    # ---- measuring ---------------------------------------------------

    async def measure(self, provider: str, model: str = "") -> Dict[str, str]:
        if self.get(provider) is None:
            raise KeyError(provider)
        if self.registry.get(provider, model) is None:
            raise KeyError(f"{provider}:{model}")
        label = f"{provider} ({model})" if model else provider
        rid = self.runs.create(f"Measure {label}", requested="eki", kind="measure",
                               payload=json.dumps({"provider": provider, "model": model}))
        await self.runner.submit(rid)
        return {"run": rid}

    async def _measure(self, run: Dict[str, Any]) -> AsyncIterator[Union[str, Dict[str, str]]]:
        job = json.loads(run["payload"] or "{}")
        provider, model = job["provider"], job.get("model", "")
        backend = self.get(provider)
        if backend is None:
            raise BackendError(f"no provider {provider}")
        yield {"backend": "eki", "reason": f"measuring {provider} {model}".rstrip()}
        options = dict(self.options.get(provider, {}))
        if model:
            options["model"] = model
        local = self._local_for(provider)
        if local is not None:
            if not local.running:
                message = await self.models.start(local.key)
                if not local.running:
                    raise BackendError(message)
            self.models.hold(local.key)
        subject = adapters.build(backend.info, options)

        def judge() -> Optional[Backend]:
            # a local grader only, and never the model being measured
            return self._titler(exclude=provider)

        yield f"Running the battery against {provider} {model}…\n".replace("  ", " ")
        final: Dict[str, Any] = {}
        recorded: Dict[str, Dict[str, Any]] = {}
        try:
            async for piece in measure.run(subject, judge):
                if isinstance(piece, dict) and "slot" in piece:
                    # kept the moment a slot completes, so a run cut short
                    # still leaves what it learned
                    task, _, difficulty = piece["slot"].partition("/")
                    self.registry.record(provider, model, task, piece["score"], piece["n"],
                                         difficulty=difficulty or "easy")
                    recorded[piece["slot"]] = piece
                    yield f"  → {piece['slot']}: {piece['score']:.2f} over {piece['n']}\n"
                elif isinstance(piece, dict):
                    final = piece
                else:
                    yield piece
        finally:
            try:
                await subject.close()
            except Exception:                       # noqa: BLE001
                pass
            if local is not None:
                self.models.release(local.key)
        results = final.get("results", {})
        for slot, r in results.items():
            if slot in recorded:
                continue
            task, _, difficulty = slot.partition("/")
            self.registry.record(provider, model, task, r["score"], r["n"],
                                 difficulty=difficulty or "easy")
        if final.get("tok_s"):
            self.registry.record_speed(provider, model, final["tok_s"])
        note = f" ({final['skipped']} needed a judge and none was up)" if final.get("skipped") else ""
        speed = f" at {final['tok_s']} tokens/s" if final.get("tok_s") else ""
        yield f"\nMeasured{speed}: {measure.summary(results)}{note}\n"

    # ---- measuring on its own ----------------------------------------

    AUTO_DONE = Path("~/.eki/bench/done.json").expanduser()
    #: a complete measurement stands this long before eki repeats it on its own
    AUTO_REPEAT_DAYS = 60
    #: one cut short (a restart, a model that fell over) is retried after this
    AUTO_RETRY_HOURS = 6
    #: what a subscription window may already have used before eki spends
    #: some of it on measuring — a full battery is a real bite
    AUTO_QUOTA = {"five_hour": 0.2, "seven_day": 0.5, "seven_day_fable": 0.5}

    def _auto_done(self) -> Dict[str, Any]:
        try:
            return json.loads(self.AUTO_DONE.read_text())
        except (OSError, ValueError):
            return {}

    def _auto_mark(self, provider: str, model: str, note: str) -> None:
        done = self._auto_done()
        done[f"{provider}/{model}"] = {"at": int(time.time()), "note": note}
        self.AUTO_DONE.parent.mkdir(parents=True, exist_ok=True)
        self.AUTO_DONE.write_text(json.dumps(done, indent=1))

    def _quota_idle(self, key: str) -> Tuple[bool, str]:
        reading = self.quota.latest.get(key)
        if reading is None or not reading.windows:
            return True, ""
        for w in reading.windows:
            limit = self.AUTO_QUOTA.get(w.key)
            if limit is not None and w.used > limit:
                return False, f"{w.label} at {int(w.used * 100)}%"
        return True, ""

    def auto_measure_due(self) -> List[Dict[str, str]]:
        """What eki would measure next, and what holds each back.

        Only a provider's default model, only when nothing solid is measured
        for it yet (or the last run is old), and only in the mode set:
        "local" spends nothing but time; "all" also spends subscription
        windows, when they're nearly idle.
        """
        conf = settings_mod.load()
        mode = conf.get("auto_measure", "local")
        out: List[Dict[str, str]] = []
        if mode == "off":
            return out
        done = self._auto_done()
        for backend in self.backends:
            key = backend.info.key
            rec = self.registry.get(key, "")
            if rec is None or not rec.enabled or rec.klass == "image":
                continue
            wanted = {f"{s['task']}/{s['difficulty']}" for s in bench.SETS.values()}
            solid = {slot for slot, m in rec.measured.items() if m.get("n", 0) >= SOLID_ITEMS}
            complete = wanted <= solid
            last = done.get(f"{key}/", {}).get("at", 0)
            wait = self.AUTO_REPEAT_DAYS * 86400 if complete else self.AUTO_RETRY_HOURS * 3600
            if time.time() - last < wait:
                continue
            if complete:
                continue
            local = self._local_for(key)
            hold = ""
            if local is None and mode != "all":
                hold = "only local models are measured on their own"
            elif local is not None and not local.running and not self.models.can_start(local.key):
                hold = "not enough memory to load it"
            elif local is None:
                ok, why = self._quota_idle(key)
                if not ok:
                    hold = f"waiting for a quieter window ({why})"
            out.append({"provider": key, "hold": hold})
        return out

    async def auto_measure_once(self) -> Optional[str]:
        """Start one measurement that's due, if now is a good time."""
        if self.runner.running:
            return None                             # never alongside your own work
        for due in self.auto_measure_due():
            if due["hold"]:
                continue
            key = due["provider"]
            self._auto_mark(key, "", "started")
            try:
                started = await self.measure(key)
            except Exception as e:                  # noqa: BLE001
                self._auto_mark(key, "", f"failed: {e}")
                continue
            return started["run"]
        return None

    # ---- models ------------------------------------------------------

    def free_key(self, key: str) -> str:
        if self.providers.get(key) is None:
            return key
        n = 2
        while self.providers.get(f"{key}-{n}"):
            n += 1
        return f"{key}-{n}"

    async def deploy(self, repo: str, label: str = "") -> Dict[str, str]:
        rid = self.runs.create(f"Set up {repo}", requested="eki", kind="deploy",
                               payload=json.dumps({"repo": repo, "label": label}))
        await self.runner.submit(rid)
        return {"run": rid}

    async def _deploy(self, run: Dict[str, Any]) -> AsyncIterator[Union[str, Dict[str, str]]]:
        job = json.loads(run["payload"] or "{}")
        repo = job["repo"]
        yield {"backend": "eki", "reason": f"setting up {repo}"}
        for p in self.providers.all():
            if p.runtime.get("repo") == repo:
                raise BackendError(f"{repo} is already set up as “{p.label}” ({p.key})")
        yield f"Looking up {repo}…\n"
        d = await deploy_mod.details(repo)
        if d["gated"]:
            raise BackendError(f"{repo} is gated on Hugging Face; accept its terms there first")
        if not d["weights_gb"]:
            raise BackendError(f"{repo} has no safetensors weights")
        budget = int(deploy_mod.settings()["context_budget"])
        room = deploy_mod.fit(d, self.models.memory().free_gb, budget)
        yield (f"{d['weights_gb']} GB of weights; needs about {room['need_gb']} GB with a "
               f"{room['context'] // 1024}k context — {room['verdict']} "
               f"({room['free_gb']} GB free now).\n")
        if d["license"]:
            yield f"License: {d['license']}.\n"

        yield f"Downloading {d['download_gb']} GB…\n"
        async for line in deploy_mod.download(repo, int(d["download_gb"] * 1024**3)):
            yield line
        yield "Downloaded.\n"

        key = self.free_key(deploy_mod.slug(repo))
        taken = [m.port for m in self.models.models.values()]
        port = deploy_mod.free_port(taken)
        samp = d["sampling"]
        thinking = False if deploy_mod.has_thinking_switch(repo) else None
        scripts = deploy_mod.write_scripts(key, repo, port, samp, thinking=thinking)
        label = job.get("label") or repo.split("/")[-1]
        options: Dict[str, Any] = {"base_url": f"http://127.0.0.1:{port}", "model": repo}
        if "temperature" in samp:
            options["temperature"] = samp["temperature"]
        provider = Provider(
            key=key, kind="mlx", label=label, tier=0, note="local, free",
            capabilities={"context_tokens": room["context"], "text": True},
            options=options,
            runtime={"port": port, "gb": room["need_gb"], "idle_minutes": DEFAULT_IDLE_MINUTES,
                     "thinking": "off" if thinking is False else "n/a",
                     "label": label, "kind": "llm", "repo": repo, **scripts})
        self.providers.upsert(provider)
        await self.reload()
        yield f"Added as “{label}” ({key}) on port {port}"
        if samp:
            yield ", using the author's sampling: " + ", ".join(f"{k} {v}" for k, v in samp.items())
        yield ".\n"

        if not room["fits"]:
            yield ("Not starting it: it won't fit beside what's loaded. Stop a model "
                   "in Models and it will start on first use.\n")
            return
        yield "Starting it for a smoke test…\n"
        message = await self.models.start(key)
        model = self.models.get(key)
        if not model or not model.running:
            raise BackendError(f"{message} — see ~/.eki/models/{key}/server.log")
        try:
            result = await deploy_mod.smoke(port, samp)
        except Exception as e:                      # noqa: BLE001
            raise BackendError(f"it started but didn't answer: {e}") from e
        self.models.touch(key)
        provider.runtime["tok_s"] = result["tok_s"]
        self.providers.upsert(provider)
        speed = f" at {result['tok_s']} tokens/s" if result["tok_s"] else ""
        yield (f"It answered in {result['seconds']} s{speed}:\n\n> {result['reply']}\n\n"
               f"Ready. eki will route to it when it's the right fit, and unload it "
               f"after 30 minutes unused.\n")

    # ---- looking -----------------------------------------------------

    def conversation(self, cid: str) -> Dict[str, Any]:
        """A thread, plus the run it's waiting on, so a viewer can reattach."""
        active = self.runs.active(cid)
        return {"id": cid, "turns": self.store.turns(cid),
                "active_run": active["id"] if active else None}

    def diff(self, rid: str) -> str:
        """The change a run left behind, as the repo sees it."""
        run = self.runs.get(rid)
        if not run or not run["cwd"]:
            return ""
        try:
            stat = subprocess.run(["git", "diff", "--stat", "HEAD"], cwd=run["cwd"],
                                  capture_output=True, text=True, timeout=20)
            full = subprocess.run(["git", "diff", "HEAD"], cwd=run["cwd"],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            return f"(diff unavailable: {e})"
        if stat.returncode != 0:
            return f"(not a git repo: {run['cwd']})"
        return (stat.stdout + "\n" + full.stdout).strip()

    async def close(self) -> None:
        for session in list(self.live.values()) + list(self.warm.values()):
            try:
                await session.close()
            except Exception:                       # noqa: BLE001
                pass
        self.live.clear()
        for b in self.backends:
            try:
                await b.close()
            except Exception:                       # noqa: BLE001
                pass


def open_engine(path: str = "") -> Engine:
    return Engine(config_mod.load(path or str(config_mod.default_path())))
