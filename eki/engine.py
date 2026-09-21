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
from . import context as context_mod
from . import profile as profile_mod
from . import policy as policy_mod
from . import priors
from . import public_scores
from . import schedules as schedules_mod
from .adapters import base as adapters
from .adapters.base import Backend, BackendError, Health, Message
from . import deploy as deploy_mod
from . import engines as engines_mod
from . import gguf as gguf_mod
from . import identify as identify_mod
from . import bench
from . import classify
from . import codex_live
from . import discover_models
from . import live
from . import measure
from . import measure_images
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
        self.live: Dict[str, Any] = {}
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
        providers = self.providers.all()
        for p in providers:
            if p.runtime.get("port"):
                r = p.runtime
                local.append(LocalModel(
                    key=p.key, label=r.get("label") or p.label, port=int(r["port"]),
                    start=r.get("start", ""), stop=r.get("stop", ""),
                    gb=float(r.get("gb", 0) or 0), note=r.get("note", ""),
                    backend=p.key, kind=r.get("kind", "llm"),
                    idle_minutes=float(r.get("idle_minutes", DEFAULT_IDLE_MINUTES) or 0),
                    context=r.get("context") or {}, profile=r.get("profile") or {},
                    engine=str(r.get("engine") or ("mlx" if p.kind == "mlx" and r.get("start") else ""))))
        previous = getattr(self, "models", None)
        self.models = ModelManager(local, self.cfg.memory_ceiling_gb)
        if previous is not None:                    # keep who-started-what across reloads
            self.models.adopt(previous)
        self._profile_models(providers)
        for p in providers:
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
        self.quota = QuotaBoard(self._quota_providers(),
                                ceiling=self.policy.quota_ceiling or self.cfg.quota_ceiling)
        self.settings = settings_mod.load()
        self.classifier = self._classifier()
        if not hasattr(self, "registry"):
            self.registry = Registry(self.cfg.db)
            self.schedules = schedules_mod.Schedules(self.cfg.db)
        self._companions()
        self.router = Router(self.backends, self.quota, policy=self.policy,
                             is_up=self._is_up,
                             reserved={self.settings["router_model"]}
                             if self.settings["router_model"] else set(),
                             models_for=self.registry.for_provider)
        self._health = {}

    def _profile_models(self, providers: List[Provider]) -> None:
        """Describe each local model from its own files, and give it the
        context it can actually use.

        The build on disk (eki/profile.py) says what it is — bits, weights,
        layers, tools, sampling; the memory beside its weights and a speed
        cap say how much context it gets (eki/context.py). Worked out here,
        every time the providers load, so nothing a harness is handed is a
        catalog's guess, and a model added by hand is described like one
        eki set up. A model whose weights aren't cached yet keeps what it
        has.
        """
        memory = self.models.memory()
        for p in providers:
            if p.kind not in ("mlx", "llamacpp") or not p.runtime.get("port") or not p.options.get("model"):
                continue
            if p.runtime.get("kind", "llm") != "llm":
                continue
            if p.kind == "llamacpp":
                prof = profile_mod.read_gguf(p.key, p.runtime)
            else:
                prof = profile_mod.read(str(p.options["model"]))
            if prof is None:
                continue
            before = (int(p.capabilities.get("context_tokens") or 0),
                      p.runtime.get("context"), p.runtime.get("profile"), p.runtime.get("gb"))
            p.runtime["profile"] = prof.as_dict()
            if not p.runtime.get("gb") and prof.weights_gb:
                # not measured yet: the weights plus what the server adds
                p.runtime["gb"] = round(prof.weights_gb + profile_mod.OVERHEAD_GB, 1)
            model = self.models.models.get(p.key)
            weights = float(p.runtime.get("gb", 0) or 0)
            # sized against the most this Mac can give it — not what happens
            # to be free this minute, which would make the window flap with
            # every other model loaded or unloaded beside it
            room = memory.ceiling_gb - weights
            pin = int(p.runtime.get("context_pin") or 0)
            window = (context_mod.pinned(prof.config, pin, room) if pin
                      else context_mod.size(prof.config, room))
            if window is not None:
                p.capabilities["context_tokens"] = window.tokens
                p.runtime["context"] = window.as_dict()
            if model is not None:
                model.context = p.runtime.get("context") or {}
                model.profile = p.runtime["profile"]
                model.gb = model.gb or weights
            after = (int(p.capabilities.get("context_tokens") or 0),
                     p.runtime.get("context"), p.runtime.get("profile"), p.runtime.get("gb"))
            if before != after:
                self.providers.upsert(p)

    def _board_name(self, key: str) -> str:
        """What to look a local model up as on the public boards: the base
        model it was built from when the setup recorded one, else its repo,
        else whatever the provider calls it (a GGUF's path says nothing)."""
        p = self.providers.get(key)
        if p is None:
            return ""
        return str(p.runtime.get("base_id") or p.runtime.get("repo") or p.options.get("model") or "")

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
            if (not p.enabled or p.kind not in ("mlx", "llamacpp", "openai_compat") or not p.runtime.get("port")
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
            prof = p.runtime.get("profile") or {}
            unfit = ""
            if not context_mod.harness_ready(context):
                unfit = (f"{p.label}'s {context // 1024}k context is too small for "
                         f"Codex, which needs {context_mod.HARNESS_MIN // 1024}k")
            elif prof.get("tools") is False:
                unfit = f"{p.label}'s chat template has no tool calling, so Codex can't work through it"
            if unfit:
                self.failed[key] = unfit
                for rec in self.registry.for_provider(key, enabled_only=False):
                    self.registry.remove(key, rec.model)
                continue
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
                               public=public_scores.lookup(p.kind, self._board_name(p.key)))

    def _local_for(self, key: str) -> Optional[LocalModel]:
        """The local server behind a backend: its own, or the one a Codex
        companion drives."""
        local_key = self.options.get(key, {}).get("local_model") or key
        own = self.models.for_backend(local_key)
        if own is not None:
            return own
        # another provider on the same local server — a second ComfyUI
        # workflow, say — shares its lifecycle: held while in use, never
        # unloaded from under it
        base = str(self.options.get(key, {}).get("base_url") or "")
        m = re.search(r"127\.0\.0\.1:(\d+)|localhost:(\d+)", base)
        if m:
            port = int(m.group(1) or m.group(2))
            return next((lm for lm in self.models.models.values() if lm.port == port), None)
        return None

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
            elif b.info.kind in ("mlx", "llamacpp"):
                default = self._board_name(b.key)
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

    def note_interruptions(self) -> int:
        """A line in each thread whose run the previous engine took with it,
        so the thread says what happened and offers to carry on."""
        noted = 0
        for run in self.runs.just_interrupted:
            cid = run.get("conversation_id")
            if not cid or run.get("kind") != "ask":
                continue
            self.store.add_turn(cid, "assistant", "*[interrupted — the engine restarted]*",
                                run.get("backend") or "", run.get("reason") or "",
                                meta={"run": run["id"], "interrupted": True,
                                      **({"cwd": run["cwd"]} if run.get("cwd") else {})})
            noted += 1
        self.runs.just_interrupted = []
        return noted

    async def resume(self, rid: str) -> Optional[Dict[str, str]]:
        """Carry on after an interruption. A thread whose program keeps its
        own session (Claude Code, Codex) is told to continue where it left
        off, with everything it knew; anything else gets the question again."""
        old = self.runs.get(rid)
        if not old or old["state"] not in ("failed", "cancelled", "interrupted"):
            return None
        cid = old["conversation_id"]
        backend = old.get("backend") or ""
        if cid and backend and self.store.session(cid, backend):
            prompt = "Carry on where you left off."
            turn = self.store.add_turn(cid, "user", prompt)
            new = self.runs.create(prompt, conversation=cid, cwd=old["cwd"],
                                   requested=backend, images=False, user_turn=turn)
            await self.runner.submit(new)
            return {"run": new, "conversation": cid, "resumed": "session"}
        got = await self.retry(rid)
        if got:
            got["resumed"] = "again"
        return got

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
            job = json.loads(run.get("payload") or "{}")
            async for piece in (self._install_engine(run) if job.get("engine") else self._deploy(run)):
                yield piece
            return
        if run.get("kind") == "measure":
            async for piece in self._measure(run):
                yield piece
            return
        if run.get("payload") and json.loads(run["payload"]).get("continuation"):
            async for piece in self._continuation(run):
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
        paused = self._yield_measurements(choice.backend.key)
        if paused:
            reason += "; paused a measurement to make way"
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
                  if self._lives(backend) else backend.stream(history, **kw))
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

    # ---- Claude Code and Codex, kept open --------------------------------

    def _lives(self, backend: Backend) -> bool:
        """Whether a backend runs as an open session under eki's interface."""
        kind = backend.info.kind
        if kind == "claude_code":
            return bool(self.settings.get("live_claude", True))
        if kind == "codex":
            return bool(self.settings.get("live_codex", True))
        return False

    SCRATCH = Path("~/.eki/scratch").expanduser()

    def _workdir(self, cwd: str) -> str:
        """Where a program runs: the folder you gave, else eki's scratch
        folder — never the engine's working directory, which is wherever
        eki happens to be installed."""
        if cwd:
            return cwd
        self.SCRATCH.mkdir(parents=True, exist_ok=True)
        return str(self.SCRATCH)

    async def _live_session(self, cid: str, backend: Backend, cwd: str, model: str = "") -> Any:
        """The conversation's session, started or resumed as needed."""
        session = self.live.get(cid)
        if session is not None and session.alive:
            session.backend_key = backend.key                              # type: ignore[attr-defined]
            return session
        warm = self.warm.pop(cwd or "", None)
        cwd = self._workdir(cwd)
        resume = self.store.session(cid, backend.key) if cid else None
        if backend.info.kind == "codex":
            if warm is not None:
                self.warm[cwd] = warm                   # not ours to take
            return await self._codex_session(cid, backend, cwd, model, resume)
        if warm is not None and warm.alive and not warm.busy and not resume:
            if cid:
                self.live[cid] = warm
            warm.backend_key = backend.key                                 # type: ignore[attr-defined]
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
        session.backend_key = backend.key                                  # type: ignore[attr-defined]
        return session

    async def _codex_session(self, cid: str, backend: Backend, cwd: str, model: str,
                             resume: Optional[str]) -> codex_live.CodexSession:
        argv = backend.live_argv()                                          # type: ignore[attr-defined]
        wanted = model or str(self.options.get(backend.key, {}).get("model") or "")
        permissions = str(self.settings.get("permissions", "auto"))
        local = bool(self.options.get(backend.key, {}).get("local_model"))
        guidance = codex_live.LOCAL_INSTRUCTIONS if local else ""
        session = codex_live.CodexSession(argv, cwd or None, None, model=wanted,
                                          permissions=permissions, resume=resume or "",
                                          developer_instructions=guidance)
        try:
            await session.start()
        except RuntimeError as e:
            if resume:
                session = codex_live.CodexSession(argv, cwd or None, None, model=wanted,
                                                  permissions=permissions,
                                                  developer_instructions=guidance)
                await session.start()
            else:
                raise BackendError(str(e))
        if cid:
            self.live[cid] = session
        session.backend_key = backend.key                                  # type: ignore[attr-defined]
        return session

    async def follow_live(self) -> int:
        """Turns the programs took on their own — a background download
        finishing, say — become turns in their threads. A wrapper that only
        listened while answering would lose them, or worse, hand them to
        the next question as a stale reply."""
        started = 0
        for cid, session in list(self.live.items()):
            if not cid or not session.alive or not session.stirred():
                continue
            key = getattr(session, "backend_key", "") or ""
            busy_here = any((self.runs.get(r) or {}).get("conversation_id") == cid
                            for r in self.runner.running)
            if not key or busy_here:
                continue
            rid = self.runs.create("", conversation=cid, cwd=session.cwd or "", requested=key,
                                   payload=json.dumps({"continuation": True}))
            await self.runner.submit(rid)
            started += 1
        return started

    async def _continuation(self, run: Dict[str, Any]) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """A run for a turn the program started itself: no question, no
        routing — read what it did and write it into the thread."""
        cid = run["conversation_id"]
        backend = self.get(run["requested"] or "")
        if backend is None:
            raise BackendError(f"no provider {run['requested']}")
        reason = "carried on by itself"
        yield {"backend": backend.key, "reason": reason}
        parts: List[str] = []
        lines: List[str] = []
        meta: Dict[str, Any] = {"run": run["id"], "continued": True}
        if run["cwd"]:
            meta["cwd"] = run["cwd"]
        try:
            # a lull of a few seconds ends it: progress lines on their own
            # never come with a result
            async for chunk in self._live_turn(run, cid, backend, "", send=False, until_quiet=8.0):
                if isinstance(chunk, str):
                    parts.append(chunk)
                elif isinstance(chunk, dict) and chunk.get("kind") == "activity":
                    lines.append(str(chunk.get("text") or ""))
                yield chunk
        except BackendError as e:
            self.store.add_turn(cid, "assistant", "".join(parts) or f"[failed: {e}]", backend.key, reason,
                                meta={**meta, "failed": True})
            raise
        usage = dict(getattr(backend, "last_usage", {}) or {})
        if usage:
            meta["usage"] = usage
        text = "".join(parts).strip()
        if not text and not lines:
            return                                  # nothing worth a turn
        if not text:
            # only progress: the lines are the turn, kept in the thread
            text = "\n".join(f"*{ln}*" for ln in lines if ln)
        self.store.add_turn(cid, "assistant", text, backend.key, reason, meta=meta)

    async def _live_turn(self, run: Dict[str, Any], cid: str, backend: Backend,
                         model: str, send: bool = True, until_quiet: float = 0.0
                         ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """One turn through the open session: text as it streams, tool
        activity as lines, questions and permission prompts as events the
        app turns into cards and answers through `answer()`."""
        session = await self._live_session(cid, backend, run["cwd"] or "", model)
        if cid and session.session_id:
            # known from the handshake: a turn cut short can still be resumed
            self.store.set_session(cid, backend.key, session.session_id)
        if model and (not session.model or model not in session.model):
            try:
                await session.set_model(model)
            except RuntimeError:
                pass
        rid = run["id"]
        usage: Dict[str, Any] = {}
        context: Dict[str, int] = {}
        local = bool(self.options.get(backend.key, {}).get("local_model"))
        nudges = 0
        try:
            prompt = run["prompt"]
            while True:
                if send:
                    await session.send(prompt)
                send = True                         # a nudge, if one follows, is sent
                tail: List[str] = []                # words since the last tool call
                async for ev in session.turn(until_quiet=until_quiet):
                    kind = ev["kind"]
                    if kind == "text":
                        tail.append(ev["text"])
                        yield ev["text"]
                    elif kind == "activity":
                        if ev["tool"] not in ("error", "approved"):
                            tail = []
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
                    elif kind == "context":
                        # how full the thread is — shown as a meter, and kept
                        # with the answer so it's known when the thread is idle
                        context = {"used": int(ev["used"]), "window": int(ev.get("window") or 0)}
                        yield {"kind": "context", **context}
                    elif kind == "result":
                        usage = ev.get("usage") or {}
                        if ev.get("is_error"):
                            raise BackendError(str(ev.get("result") or f"{backend.info.label} reported an error")[:300])
                    elif kind == "exit":
                        self.live.pop(cid, None)
                        raise BackendError(f"{backend.info.label} stopped: {ev.get('error', '')}"[:300])
                # a local model that announced work and stopped — or whose
                # tool call came out malformed and vanished — is told to go on
                if local and nudges < 2 and live.sounds_unfinished("".join(tail)):
                    nudges += 1
                    prompt = "Go ahead — do it now, with the tools. Don't stop to announce."
                    yield {"kind": "activity", "text": "Nudged to carry on"}
                    yield "\n\n"
                    continue
                break
        except (asyncio.CancelledError, GeneratorExit):
            self.pending.pop(rid, None)
            if session.alive:
                await session.interrupt()
            raise
        except RuntimeError as e:
            raise BackendError(str(e))
        finally:
            self.pending.pop(rid, None)
        if context.get("used"):
            usage = {**usage, "context_used": context["used"], "context_window": context["window"]}
            self._learn_window(backend.key, model, context["window"])
        backend.last_usage = usage                                          # type: ignore[attr-defined]
        backend.last_session = session.session_id                           # type: ignore[attr-defined]

    def _learn_window(self, key: str, model: str, window: int) -> None:
        """A harness said how big its model's context is: the registry and
        the provider keep that, not the number a template guessed."""
        if not window:
            return
        for alias in {model or "", ""} if not model else {model}:
            rec = self.registry.get(key, alias)
            if rec is not None and rec.context_tokens != window:
                self.registry.seen(key, alias, context_tokens=window)
        p = self.providers.get(key)
        if p is not None and not model and int(p.capabilities.get("context_tokens") or 0) != window:
            p.capabilities["context_tokens"] = window
            self.providers.upsert(p)

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

    async def commands_for(self, cid: str = "", cwd: str = "",
                           backend_key: str = "") -> List[Dict[str, Any]]:
        """The slash commands for a thread; for a thread not started yet, a
        session is opened ahead for its folder and kept for the first run.
        A thread that is (or will be) Codex gets eki's short list for it."""
        session = self.live.get(cid) if cid else None
        if session is not None and isinstance(session, codex_live.CodexSession):
            return list(session.commands)
        if session is None and backend_key:
            chosen = self.get(backend_key.partition(":")[0])
            if chosen is not None and chosen.info.kind == "codex":
                return list(codex_live.COMMANDS)
            if chosen is not None and chosen.info.kind != "claude_code":
                return []
        if session is None or not session.alive:
            session = self.warm.get(cwd or "")
        if session is None or not session.alive:
            backend = next((b for b in self.backends if b.info.kind == "claude_code"
                            and getattr(b, "bin", None)), None)
            if backend is None or not self.settings.get("live_claude", True):
                return []
            import uuid
            argv = backend.live_argv(cwd or None, None, str(uuid.uuid4()))   # type: ignore[attr-defined]
            session = live.LiveSession(argv, self._workdir(cwd), None,
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

    def _seer(self) -> Optional[Backend]:
        """A model that can look at a picture, cheapest first: Claude Code on
        a subscription before a metered API — never a local image model."""
        able = [b for b in self.backends
                if b.info.capabilities.vision and b.info.capabilities.text and self.get(b.key) is not None]
        able.sort(key=lambda b: (b.info.kind != "claude_code", b.info.cost.tier))
        for b in able:
            if self._is_up(b.key) is not False:
                return adapters.build(b.info, self.options.get(b.key, {}))
        return None

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

        is_image = priors.class_of(backend.info.kind, options, backend.info.capabilities) == "image"
        if is_image:
            seer = self._seer()
            yield (f"Drawing {len(measure_images.ITEMS)} pictures with {provider}, checked by "
                   f"{seer.info.label if seer else 'nobody'}…\n")
            battery = measure_images.run(subject, lambda: seer)
        else:
            yield f"Running the battery against {provider} {model}…\n".replace("  ", " ")
            battery = measure.run(subject, judge)
        final: Dict[str, Any] = {}
        recorded: Dict[str, Dict[str, Any]] = {}
        try:
            async for piece in battery:
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
        shown = results or {slot: {"score": r["score"], "n": r["n"]} for slot, r in recorded.items()}
        yield f"\nMeasured{speed}: {measure.summary(shown)}{note}\n"

    # ---- on a timetable -------------------------------------------------

    async def fire(self, schedule: schedules_mod.Schedule) -> Dict[str, str]:
        """One firing: a fresh thread named after the schedule and the time,
        the request routed like anything typed (or pinned to its provider)."""
        stamp = time.strftime("%b %-d, %H:%M")
        cid = self.store.new_conversation(f"{schedule.name} · {stamp}")
        self.store.set_conversation(cid, title=f"{schedule.name} · {stamp}")
        started = await self.ask(schedule.prompt, conversation=cid,
                                 backend_key=schedule.backend, repo=schedule.cwd)
        self.schedules.advance(schedule.id, ran=True, conversation=cid, run=started["run"])
        if self.settings.get("notify_scheduled", True):
            task = asyncio.create_task(self._notify_when_done(started["run"], schedule.name))
            self._side_tasks.add(task)
            task.add_done_callback(self._side_tasks.discard)
        return started

    async def fire_due(self) -> List[str]:
        """Every schedule whose time has come, one run each."""
        fired = []
        for s in self.schedules.due():
            try:
                started = await self.fire(s)
                fired.append(started["run"])
            except Exception as e:                  # noqa: BLE001
                self.schedules.advance(s.id, ran=True, run=f"failed: {e}"[:120])
        return fired

    async def _notify_when_done(self, rid: str, name: str) -> None:
        """A macOS notification when a scheduled run ends, app open or not."""
        queue = self.runner.subscribe(rid)
        state = ""
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=6 * 3600)
                if event.get("event") == "state" and event.get("state") in ("done", "failed", "cancelled"):
                    state = event["state"]
                    break
        except asyncio.TimeoutError:
            return
        finally:
            self.runner.unsubscribe(rid, queue)
        run = self.runs.get(rid) or {}
        text = (run.get("output") or "").strip().splitlines()
        body = (text[-1][:120] if text else "finished") if state == "done" else f"{state}: {run.get('error') or ''}"[:120]
        try:
            await asyncio.create_subprocess_exec(
                "osascript", "-e",
                f'display notification "{body.replace(chr(34), chr(39))}" with title "eki" '
                f'subtitle "{name.replace(chr(34), chr(39))}"',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            pass

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
            if rec is None or not rec.enabled:
                continue
            image = rec.klass == "image"
            if image:
                wanted = {measure_images.SLOT}
            else:
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
            if image and not hold:
                # the pictures are free; the judge that looks at them isn't
                seer = next((b for b in self.backends if b.info.capabilities.vision
                             and b.info.capabilities.text), None)
                if seer is None:
                    hold = "no model here can look at pictures"
                else:
                    ok, why = self._quota_idle(seer.key)
                    if not ok:
                        hold = f"waiting for {seer.info.label}'s window to be quieter ({why})"
            out.append({"provider": key, "hold": hold})
        return out

    #: a measurement paused for your work is tried again after this
    AUTO_PAUSE_MINUTES = 30

    def _yield_measurements(self, backend_key: str) -> List[str]:
        """Your request comes first: a measurement running on the same
        provider — or the same local model behind another provider — is
        stopped, its finished slots kept, and picked up again later."""
        mine = self._local_for(backend_key)
        stopped: List[str] = []
        for rid in list(self.runner.running):
            run = self.runs.get(rid) or {}
            if run.get("kind") != "measure":
                continue
            job = json.loads(run.get("payload") or "{}")
            provider = job.get("provider", "")
            same = provider == backend_key or (
                mine is not None and self._local_for(provider) is mine)
            if not same:
                continue
            if self.runner.cancel(rid):
                stopped.append(provider)
                done = self._auto_done()
                done[f"{provider}/{job.get('model', '')}"] = {
                    "at": int(time.time()) - self.AUTO_RETRY_HOURS * 3600
                    + self.AUTO_PAUSE_MINUTES * 60, "note": "paused for a request"}
                self.AUTO_DONE.parent.mkdir(parents=True, exist_ok=True)
                self.AUTO_DONE.write_text(json.dumps(done, indent=1))
        return stopped

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

    async def deploy(self, repo: str, label: str = "", quant: str = "", path: str = "") -> Dict[str, str]:
        what = path or repo
        rid = self.runs.create(f"Set up {Path(what).name if path else what}", requested="eki", kind="deploy",
                               payload=json.dumps({"repo": repo, "label": label, "quant": quant, "path": path}))
        await self.runner.submit(rid)
        return {"run": rid}

    async def install_engine(self, name: str) -> Dict[str, str]:
        engine = engines_mod.get(name)
        rid = self.runs.create(f"Get {engine.info.title}", requested="eki", kind="deploy",
                               payload=json.dumps({"engine": name}))
        await self.runner.submit(rid)
        return {"run": rid}

    async def _install_engine(self, run: Dict[str, Any]) -> AsyncIterator[Union[str, Dict[str, str]]]:
        name = json.loads(run["payload"] or "{}")["engine"]
        engine = engines_mod.get(name)
        yield {"backend": "eki", "reason": f"getting {engine.info.title}"}
        if engine.installed():
            ok, detail = await engine.health()
            yield f"{engine.info.title} is already here ({detail}).\n"
            return
        yield (f"Getting {engine.info.title} {engine.info.version} ({engine.info.size_mb} MB) "
               f"from {engine.info.source} into eki's own folder…\n")
        async for line in engine.install():
            yield line

    async def _deploy(self, run: Dict[str, Any]) -> AsyncIterator[Union[str, Dict[str, str]]]:
        """Set a model up from the Hub: identify its format, get the engine
        that serves it (installing it into eki's own space if this is the
        first model of that kind), size it, download it, write its start
        script, register it, and prove it answers."""
        job = json.loads(run["payload"] or "{}")
        repo = job.get("repo") or job.get("path") or ""
        local_path = Path(os.path.expanduser(job["path"])) if job.get("path") else None
        yield {"backend": "eki", "reason": f"setting up {repo}"}
        for p in self.providers.all():
            if p.runtime.get("repo") == repo and (not job.get("quant") or p.runtime.get("quant") == job.get("quant")):
                raise BackendError(f"{repo} is already set up as “{p.label}” ({p.key})")
        if local_path is not None:
            yield f"Reading {local_path.name}…\n"
            try:
                d, _ = identify_mod.local_details(local_path)
            except (OSError, ValueError) as e:
                raise BackendError(str(e))
        else:
            yield f"Looking up {repo}…\n"
            d = await deploy_mod.details(repo, quant=job.get("quant") or "")
            if d["gated"]:
                raise BackendError(f"{repo} is gated on Hugging Face; accept its terms there first")
            if d.get("picture"):
                raise BackendError(f"{repo} is an image model, not a language model — eki serves those "
                                   "through ComfyUI (Add provider → ComfyUI, with a workflow for it)")
            if not d["weights_gb"]:
                raise BackendError(f"{repo} has no weights eki can serve (safetensors or GGUF)")
        fmt = d["format"]
        engine = engines_mod.for_format(fmt)
        if engine is None:
            raise BackendError(f"nothing here serves {fmt} models")
        mem = self.models.memory()
        room = deploy_mod.fit(d, mem.free_gb, mem.ceiling_gb)
        prof = profile_mod.from_hub(repo, d)
        yield prof.describe() + ".\n"
        yield (f"Needs about {room['need_gb']} GB with a {room['context'] // 1024}k context"
               + (f" ({room['window']['limited_by']}-limited)" if room.get("window") else "")
               + f" — {room['verdict']} on this Mac ({room['ceiling_gb']} GB for models)"
               + ("" if room["fits_now"] else
                  f"; {room['free_gb']} GB free now, so it starts once there's room")
               + ".\n")
        if d["license"]:
            yield f"License: {d['license']}.\n"

        if not engine.installed():
            yield (f"{engine.info.title} isn't here yet — it runs {fmt.upper()} models. "
                   f"Getting {engine.info.title} {engine.info.version} ({engine.info.size_mb} MB) "
                   f"into eki's own folder…\n")
            async for line in engine.install():
                yield line

        if local_path is not None:
            served = str(local_path)                # already here: served from where it is
        elif fmt == "gguf":
            yield f"Downloading {d['quant']} ({d['download_gb']} GB)…\n"
            async for line in deploy_mod.download_gguf(repo, d["chosen"]):
                yield line
            served = str(gguf_mod.local_path(repo, gguf_mod.first_shard(d["chosen"])))
            yield "Downloaded.\n"
        else:
            yield f"Downloading {d['download_gb']} GB…\n"
            async for line in deploy_mod.download(repo, int(d["download_gb"] * 1024**3)):
                yield line
            served = repo
            yield "Downloaded.\n"

        stem = local_path.stem if local_path is not None else repo
        key = self.free_key(deploy_mod.slug(stem) + (f"-{d['quant'].lower()}" if fmt == "gguf" and d.get("quant")
                                                     and not (local_path and d["quant"].lower() in stem.lower()) else ""))
        taken = [m.port for m in self.models.models.values()]
        port = deploy_mod.free_port(taken)
        samp = d["sampling"]
        switch = prof.thinking_switch if (fmt == "gguf" or local_path is not None) else deploy_mod.has_thinking_switch(repo)
        thinking = False if switch else None
        scripts = deploy_mod.write_scripts(key, served, port, samp, thinking=thinking,
                                           engine_name=engine.info.name,
                                           context=room["context"] if fmt == "gguf" else 0)
        label = job.get("label") or (local_path.stem if local_path is not None
                                     else repo.split("/")[-1] + (f" {d['quant']}" if fmt == "gguf" else ""))
        options: Dict[str, Any] = {"base_url": f"http://127.0.0.1:{port}", "model": served}
        if "temperature" in samp:
            options["temperature"] = samp["temperature"]
        runtime: Dict[str, Any] = {"port": port, "gb": room["need_gb"], "idle_minutes": DEFAULT_IDLE_MINUTES,
                                   "thinking": "off" if thinking is False else "n/a",
                                   "label": label, "kind": "llm", "repo": repo, "format": fmt,
                                   "base_id": d.get("base_id") or "", **scripts}
        if fmt == "gguf":
            runtime["quant"] = d["quant"]
            runtime["profile"] = prof.as_dict()
            # the cache's shape, for sizing the window on later loads
            (deploy_mod.HOME / "models" / key / "config.json").write_text(json.dumps(d["config"] or {}))
        provider = Provider(
            key=key, kind="llamacpp" if fmt == "gguf" else "mlx", label=label, tier=0, note="local, free",
            capabilities={"context_tokens": room["context"], "text": True},
            options=options, runtime=runtime)
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
