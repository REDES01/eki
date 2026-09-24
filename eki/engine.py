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
import sys
import re
import shutil
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
from . import imagespec
from . import learn as learn_mod
from . import handoff as handoff_mod
from . import failover as failover_mod
from . import goals as goals_mod
from . import shift as shift_mod
from . import models as models_mod
from . import table as table_mod
from . import capacity as capacity_mod
from . import prefs as prefs_mod
from . import watch as watch_mod
from . import observe as observe_mod
from . import selfloop
from . import selfengine as selfengine_mod
from .selfengine import SelfLoop
from . import workspace as workspace_mod
from . import live
from . import mcpbridge
from . import mcpregistry
from . import skills as skills_mod
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


def _payload(run: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return dict(json.loads(run.get("payload") or "{}") or {})
    except (TypeError, ValueError):
        return {}


def _goal_of(run: Dict[str, Any]) -> str:
    """The goal a run is a turn of, or ""."""
    return str(_payload(run).get("goal") or "")


#: a goal that uses the screen, waiting for you to leave it
SCREEN_WAIT = "uses the screen — waits until you're away"


def _allowed_of(run: Dict[str, Any]) -> Optional[List[str]]:
    """The providers a run may use (a goal's budget), or None for any."""
    allowed = _payload(run).get("allowed")
    return [str(a) for a in allowed] if isinstance(allowed, list) else None


def _yours(run: Dict[str, Any]) -> bool:
    """A request you made — not a goal's turn, not an agent asking eki for a
    step through its tools, not a program carrying on by itself."""
    p = _payload(run)
    return run.get("kind", "ask") == "ask" and not p.get("goal") and p.get("via") != "agent" \
        and not p.get("continuation")


class Engine(SelfLoop):
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
        self.runner.on_error = self._on_run_error
        self._fixing = asyncio.Lock()
        self._watching = asyncio.Lock()
        self._health: Dict[str, Tuple[float, Any]] = {}
        self._side_tasks: set = set()               # titles and the like
        self._folder_locks: Dict[str, asyncio.Lock] = {}
        self._in_copy: set = set()                  # worktrees a run is working in
        # the idle shift (eki/goals.py, eki/shift.py): the piece in progress,
        # the models it loaded, pieces resting after a failure, what it's doing
        self._shift_run: str = ""
        self._shift_goal: str = ""
        self._shift_local = False                  # the turn in progress is on a model on this machine
        self._pressure = 0                         # ticks in a row with memory pressure
        self._shift_loaded: set = set()
        self._shift_state: Dict[str, Any] = {"state": "idle", "why": "not started yet"}
        self._awake = shift_mod.Awake()
        #: the turn in progress uses the screen, since when; and who's at the keyboard
        self._shift_screen = False
        self._shift_on_time = False                # …runs on time: it doesn't step aside
        self._shift_started = 0.0
        self._presence = shift_mod.Presence()
        #: set when a piece ends, so the next one starts at once, not on the next tick
        self.shift_wake = asyncio.Event()
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
            # schedules were the other way to repeat a request: goals now (eki/goals.py)
            adopted = goals_mod.adopt_schedules(self.cfg.db)
            if adopted:
                observe_mod.note("history", what="schedules became goals", goals=[g.id for g in adopted])
        self._companions()
        self.router = Router(self.backends, self.quota, policy=self.policy,
                             is_up=self._is_up,
                             reserved={self.settings["router_model"]}
                             if self.settings["router_model"] else set(),
                             models_for=self.registry.for_provider,
                             ladder_for=watch_mod.ladder,
                             with_tools=self._local_with_tools)
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
                                                   tools=True, repo=True,
                                                   # the web only through a search server
                                                   # eki's registry gives Codex
                                                   web=mcpregistry.provides("codex", "web")),
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
                    "images_out": caps.images_out, "text": caps.text, "web": caps.web,
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
                  repo: str = "", images: bool = False,
                  image: Optional[Dict[str, Any]] = None,
                  attachments: Optional[List[str]] = None, via: str = "") -> Dict[str, str]:
        """Write the question down and start answering it. Returns at once.

        The question is stored before anything runs, so a conversation
        reopened mid-answer already shows what was asked. Attachments are
        pictures on this Mac, shown with the question and given to the
        program that answers (Claude Code and Codex take them).
        """
        cid = conversation or self.store.new_conversation()
        attached = [p for p in (attachments or []) if p and os.path.isfile(os.path.expanduser(p))]
        shown = prompt + "".join(f"\n\n![attachment]({p.replace(' ', '%20')})" for p in attached)
        turn = self.store.add_turn(cid, "user", shown,
                                   meta={"attachments": attached} if attached else None)
        # a size or a count given outright, beside the words (see imagespec)
        image = {k: v for k, v in (image or {}).items() if v}
        payload: Dict[str, Any] = {}
        if image:
            payload["image"] = image
        if attached:
            payload["attachments"] = attached
        if via:
            payload["via"] = via                    # "agent": a program asking through eki's tools
        rid = self.runs.create(prompt, conversation=cid, cwd=repo,
                               requested=backend_key, images=images or bool(image), user_turn=turn,
                               payload=json.dumps(payload) if payload else "")
        if self._shift_local and via != "agent":
            # a goal's turn on the model your request may need: yours first
            self._shift_step_out("your request comes first")
        await self.runner.submit(rid)
        return {"run": rid, "conversation": cid}

    def _resumable(self, run: Dict[str, Any]) -> bool:
        """Carried on by itself after a restart: a run in a program that keeps
        its own session (Claude Code, Codex), once — a run that was itself a
        resumption isn't resumed again, so a crash can't loop."""
        cid, backend = run.get("conversation_id"), run.get("backend") or ""
        if not self.settings.get("resume_interrupted", True) or not cid or not backend:
            return False
        try:
            if (json.loads(run.get("payload") or "{}") or {}).get("resume_of"):
                return False
        except (TypeError, ValueError):
            return False
        return bool(self.store.session(cid, backend))

    def note_interruptions(self) -> int:
        """A line in each thread whose run the previous engine took with it,
        so the thread says what happened — and, where the program kept its
        session, that it is carrying on (`resume_interrupted`)."""
        noted = 0
        self._to_resume: List[str] = []
        for run in self.runs.just_interrupted:
            cid = run.get("conversation_id")
            if not cid or run.get("kind") != "ask":
                continue
            carry = self._resumable(run)
            if carry:
                self._to_resume.append(run["id"])
            self.store.add_turn(cid, "assistant", "*[interrupted — the engine restarted"
                                + ("; carrying on]*" if carry else "]*"),
                                run.get("backend") or "", run.get("reason") or "",
                                meta={"run": run["id"], "interrupted": True,
                                      **({"cwd": run["cwd"]} if run.get("cwd") else {})})
            noted += 1
        self.runs.just_interrupted = []
        return noted

    async def resume_interrupted(self) -> int:
        """Start the carrying-on runs `note_interruptions` decided on."""
        started = 0
        for rid in getattr(self, "_to_resume", []):
            try:
                if await self.resume(rid):
                    started += 1
            except Exception:                       # noqa: BLE001
                continue
        self._to_resume = []
        return started

    async def catch_up_checkout(self) -> str:
        """What the engine runs that your checkout missed (a healthy swap
        couldn't merge — you had edits in it): brought in once it can go
        in, and said once. Nothing while the checkout still has edits."""
        from . import builds as builds_mod
        src = builds_mod.source()
        if not await asyncio.to_thread(builds_mod.behind, src):
            return ""
        got = await asyncio.to_thread(builds_mod.catch_up, src)
        if got.startswith("merged"):
            observe_mod.note("history", what="caught up", how=got)
            if self.settings.get("notify_learned", True):
                await self._notify("eki · your checkout caught up", got[:200])
        return got

    async def settle_swap(self) -> Dict[str, Any]:
        """A swap the supervisor finished: bring a healthy self-change into
        your checkout if it goes in cleanly, and say how it went — once."""
        from . import builds as builds_mod
        done = await asyncio.to_thread(builds_mod.settle_swap)
        if not done:
            return {}
        what = f"self/{done['self']}" if done.get("self") else Path(done.get("target") or "").name
        if done.get("self"):
            try:
                # the change it carried: applied, or rolled back — and its item follows
                await asyncio.to_thread(self.self_settled, done)
            except Exception:                       # noqa: BLE001
                pass
        if done["state"] == "healthy":
            title = f"now running {what}"
            body = done.get("merged") or "the new build is healthy"
        else:
            observe_mod.note("history", what=f"swap {done['state']}", target=done.get("target"),
                             why=done.get("why"), self=done.get("self"))
            title = f"{what} was rolled back"
            body = done.get("why") or done["state"]
        if self.settings.get("notify_learned", True):
            await self._notify(title, body)
        return done

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
            # a change to eki itself carries on as one: the same item, its worktree
            was = _payload(old)
            carried = {k: was[k] for k in ("self_item", "goal", "allowed", "route") if k in was} \
                if was.get("self_item") else {}
            # resume_of: the same copy of the folder, as the run left it
            new = self.runs.create(prompt, conversation=cid, cwd=old["cwd"],
                                   requested=backend, images=False, user_turn=turn,
                                   payload=json.dumps({"resume_of": rid, **carried}))
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
        if _payload(run).get("self_resolve") and not run.get("_self_inner"):
            # a change to eki that conflicts with your checkout: resolved, then applied
            async for piece in self._self_resolve(run):
                yield piece
            return
        if _payload(run).get("self_item") and not run.get("_self_inner"):
            # a change to eki itself: worktree, agent, checks (eki/selfengine.py)
            async for piece in self._self_work(run):
                yield piece
            return

        cid = run["conversation_id"]
        talk = prefs_mod.addressed(run["prompt"], self._route_names(), has_folder=bool(run["cwd"])) \
            if cid and not run["requested"] and not run["images"] and not run.get("_self_inner") else ""
        if talk:
            async for piece in self._routing_talk(run, cid, talk):
                yield piece
            return
        if cid and not run["cwd"] and not run["images"] and not run.get("_self_inner") \
                and not _goal_of(run) and _payload(run).get("via") != "agent":
            # "eki, make the chat list show the project name": eki changing itself
            wants = selfloop.addressed(run["prompt"])
            if wants and not self._self_why_not():
                async for piece in self._self_from_chat(run, wants):
                    yield piece
                return
        shown = self._picture_before(run)
        # a goal's turn is routed on the goal's own words — eki's framing isn't the work
        routed = {**run, "prompt": _payload(run).get("route") or run["prompt"]}
        label = await self._label(routed, after_image=bool(shown))
        # "claude" asks for the provider; "claude:opus" for one of its models
        requested, _, wanted_model = (run["requested"] or "").partition(":")
        # Tools decide the harness: a request that needs tools — files,
        # commands, the screen, the web — goes to a harness (Claude Code,
        # Codex, a local model with Codex's hands); one that doesn't goes to
        # a model directly (a local one says "hello" in ~4 s, ~37 s under
        # Codex's instructions — runs.db, 2026-09-23).
        wants_harness = self._wants_harness(label, run["cwd"], bool(run["images"]), requested)
        # a goal that uses the screen: whatever its words, it needs the screen tools
        screen_goal = bool(_payload(run).get("screen"))
        try:
            before = [t for t in self.store.turns(cid) if t["id"] < int(run.get("user_turn") or 0)] \
                if cid else []
        except Exception:                           # noqa: BLE001
            before = []
        row, row_why, targets, stays, escalate = self._plan(label, routed, before, cid, requested)
        if stays and self._bare(stays) and not screen_goal:
            # staying with a model that has no tools: it decides itself, and
            # hands the thread over when the request needs more (eki/handoff.py)
            wants_harness = False
        wants_harness = wants_harness or screen_goal
        need = Need(repo=bool(run["cwd"]), escalate=escalate, vision=screen_goal,
                    tools=bool(run["cwd"]) or wants_harness,
                    images_out=bool(run["images"]) or label.task == "image",
                    # research means looking things up: a provider with the web
                    web=label.task == "research" and not requested and not stays,
                    backend=requested or None,
                    task=label.task, difficulty=label.difficulty,
                    row=row, row_title=table_mod.TITLES.get(row, row), targets=targets,
                    allowed=_allowed_of(run))
        choice = self.router.choose(need)
        if requested and cid and not _goal_of(run):
            # you picked it — learned from; a goal's turn is eki's own pick, not yours
            self._learn_override(need, requested, cid)
        if choice.backend is None and wants_harness:
            # it needs tools and nothing with tools can take it now: a model
            # without them would only describe what it can't do — say so
            choice.reason = "this needs tools (files, commands, the screen or the web), and nothing " \
                            "with tools can take it right now"
        if choice.backend is not None and wanted_model:
            choice.model = wanted_model
            choice.reason = f"{choice.backend.key} ({wanted_model}): asked for by name"
        if choice.backend is None:
            why = choice.reason
            if choice.rejected:
                why += " — " + "; ".join(choice.rejected)
            observe_mod.note("gap", what="nothing could take the request", why=why[:300],
                             task=label.task, run=run["id"], request=run["prompt"][:200])
            if cid:
                self.store.add_turn(cid, "assistant", f"[{why}]", "", choice.reason,
                                    meta={"run": run["id"], "failed": True})
            raise BackendError(why)

        reason = choice.reason
        paused = self._yield_measurements(choice.backend.key)
        if paused:
            reason += "; paused a measurement to make way"
        model = self._local_for(choice.backend.key)
        meta_label = {**label.to_json(), "row": row, "row_why": row_why}
        chosen_model = choice.model
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
        # A folder run works in its thread's own copy of the folder (a git
        # worktree), or takes its turn with the folder's lock — never two
        # agents editing the same files at once (eki/workspace.py).
        ws: Optional[workspace_mod.Workspace] = None
        held: Optional[asyncio.Lock] = None
        work_run = run
        if run.get("_as"):
            # eki's own words to the agent (self-work's brief, the weekly
            # note's evidence) in place of the line the thread shows
            work_run = {**run, "prompt": run["_as"]}
            if history and history[-1].role == "user":
                history = history[:-1] + [Message("user", run["_as"])]
        # A program that keeps its own session (Claude Code, Codex) only ever
        # sees what was said to it. Joining a thread others have spoken in —
        # handed over, or picked — it is given the conversation so far first.
        earlier = self._earlier_for(backend, cid, run) if cid else ""
        fo = run.get("_failover") or {}
        if run.get("_handoff") or earlier or fo:
            ho = run.get("_handoff") or {}
            told = run.get("_as") or run["prompt"]
            asked = (f"{ho.get('brief')}\n\n(Their message: {told})" if ho.get("brief") else told)
            if fo:
                asked = failover_mod.brief(fo.get("label") or fo.get("from") or "The model before",
                                           fo.get("why") or "", fo.get("partial") or "") + asked
            work_run = {**work_run, "prompt": earlier + asked}
            if history and history[-1].role == "user":
                history = history[:-1] + [Message("user", work_run["prompt"])]
        if run["cwd"]:
            if self._folder_lock(run["cwd"]).locked():
                yield {"backend": choice.backend.key,
                       "reason": reason + "; waiting for another run in this folder"}
            ws, held = await self._open_workspace(run, cid)
            kw["cwd"] = ws.path
            brief = workspace_mod.brief(ws)
            work_run = {**work_run, "cwd": ws.path, "prompt": brief + work_run["prompt"]}
            if brief and history and history[-1].role == "user":
                history = history[:-1] + [Message("user", brief + history[-1].content)]
        # "make it bluer", said to a picture: the image model is handed the
        # picture to change. "try again" repeats whatever made it, anew.
        drawn: Dict[str, Any] = {}
        if backend.info.capabilities.images_out and not backend.info.capabilities.text:
            # "four of them, at 1536x1024" is about the paper, not the picture:
            # taken out of what the model reads, and handed over as numbers
            said = imagespec.read(run["prompt"])
            paper = said.as_dict()
            words = said.prompt
            drawn = {"prompt": words or run["prompt"], "source": ""}
            follow = classify.image_followup(run["prompt"]) if shown else ""
            if follow == "edit":
                drawn["source"] = shown["path"]
            elif follow == "redo" and shown.get("prompt"):
                drawn = {"prompt": shown["prompt"], "source": shown.get("source", "")}
                # the same again means the same paper, unless this names another:
                # a new size or shape replaces the old one, whichever it was
                before = dict(shown.get("paper") or {})
                if "width" in paper or "aspect" in paper:
                    for k in ("width", "height", "aspect"):
                        before.pop(k, None)
                paper = {**before, **paper}
            if drawn["source"] and not os.path.isfile(drawn["source"]):
                drawn["source"] = ""
            # what the app sent outright (a size picker, the API) wins over words
            try:
                paper.update({k: v for k, v in (json.loads(run.get("payload") or "{}").get("image") or {}).items()
                              if k in ("width", "height", "aspect", "batch") and v})
            except (TypeError, ValueError, AttributeError):
                pass
            if paper.get("width") and paper.get("height"):
                paper.pop("aspect", None)
            kw["prompt"] = drawn["prompt"]
            kw.update(paper)
            if paper:
                drawn["paper"] = paper
            if drawn["source"]:
                kw["edit"] = drawn["source"]
        resumed = self.store.session(cid, backend.key) if cid else None
        if resumed:
            kw["resume"] = resumed

        meta: Dict[str, Any] = {"run": run["id"], "label": meta_label, "model": chosen_model}
        if run.get("_handoff"):
            meta["handoff"] = run["_handoff"]
        if run.get("_failover"):
            fo = run["_failover"]
            meta["failover"] = {k: fo[k] for k in ("from", "why", "hops", "tried") if k in fo}
        if run["cwd"]:
            meta["cwd"] = run["cwd"]
        if ws is not None and ws.mode == "worktree":
            meta["worktree"] = {"path": ws.path, "branch": ws.branch}
        if drawn:
            meta["image"] = drawn               # what a later "try again" repeats
        parts: List[str] = []
        hand_to = self._handoff_targets(_allowed_of(run))
        may_hand_off = self._bare(backend.key) and not requested and bool(cid) \
            and self.settings.get("handoff", True) and bool(hand_to)
        if may_hand_off:
            # a real tool call where the server takes `tools`; the marker elsewhere
            as_tool = bool(getattr(backend, "accepts_tools", False)) \
                and self.settings.get("handoff_tool", True)
            if as_tool:
                kw["tools"] = [handoff_mod.tool(hand_to)]
            history = [Message("system", handoff_mod.instructions(hand_to, as_tool))] + history
        if self._lives(backend):
            stream = self._live_turn(work_run, cid, backend, choice.model)
        elif self._needs_skill_loader(backend):
            stream = self._skilled(backend, history, kw, meta)
        else:
            stream = backend.stream(history, **kw)
        if may_hand_off:
            stream = handoff_mod.watch(stream)
        if run.get("_handoff"):
            stream = self._with_note(stream, run["_handoff"])
        if run.get("_failover"):
            stream = self._carried_on(stream, run["_failover"], backend)
        handed: Optional[handoff_mod.HandOff] = None
        ran_out: Optional[Dict[str, Any]] = None
        try:
            async for chunk in stream:
                if isinstance(chunk, str):
                    parts.append(chunk)
                yield chunk
        except handoff_mod.HandOff as h:
            handed = h
        except BackendError as e:
            if self._fails_over(e, run, requested):
                # out of usage mid-run: the thread's copy of the folder stays
                # as it is, and the next choice carries on from there
                was = run.get("_failover") or {}
                ran_out = {"from": backend.key, "label": backend.info.label,
                           "source": backend.info.quota_source or backend.key,
                           "why": str(e)[:240], "partial": "".join(parts)[-6000:],
                           "hops": int(was.get("hops") or 0) + 1,
                           "tried": list(was.get("tried") or []) + [backend.key],
                           "sources": list(was.get("sources") or [])
                           + [backend.info.quota_source or backend.key]}
            else:
                line = self._keep_workspace(ws, run)
                if cid:
                    self.store.add_turn(cid, "assistant", ("".join(parts) or f"[failed: {e}]") + line,
                                        backend.key, choice.reason,
                                        meta={**meta, "failed": True})
                raise
        except (asyncio.CancelledError, GeneratorExit):
            if self.runner.stopping:
                # the engine is going away, not you stopping it: the copy is
                # left as it is and the next engine carries the run on
                raise
            observe_mod.note("friction", signal="stopped", backend=backend.key, run=run["id"],
                             task=label.task, request=run["prompt"][:200])
            # stopped on purpose: keep what arrived, and say it was cut short
            line = self._keep_workspace(ws, run)
            if cid and parts:
                self.store.add_turn(cid, "assistant", "".join(parts) + "\n\n*[stopped]*" + line,
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
            if held is not None:
                held.release()
            if ws is not None:
                self._in_copy.discard(ws.tree)

        if ran_out is not None:
            # the same request, to the next choice not on that subscription —
            # told what was done, in the same copy of the folder
            observe_mod.note("history", what="failover", frm=backend.key, run=run["id"],
                             why=ran_out["why"][:200])
            self._quota_after_limit(backend)
            async for piece in self._dispatch({**run, "_failover": ran_out}):
                yield piece
            return

        if handed is not None:
            # the model without tools passed it on: the same request, with its
            # brief, to the harness it named (or the row's next with tools)
            observe_mod.note("history", what="handoff", frm=backend.key, to=handed.target,
                             run=run["id"], brief=handed.brief[:200])
            async for piece in self._dispatch({**run, "_handoff": {
                    "from": backend.key, "to": handed.target, "brief": handed.brief}}):
                yield piece
            return

        # the run's changes, from its copy back to the folder
        if ws is not None and ws.mode == "worktree":
            async with self._folder_lock(ws.folder):
                try:
                    result = await asyncio.to_thread(workspace_mod.close, ws, run["id"],
                                                     run["prompt"], True)
                except workspace_mod.WorkspaceError as e:
                    result = {"state": "error", "why": str(e)[:200]}
            meta["worktree"].update(result)
            line = workspace_mod.summary(ws, result) if result.get("state") != "error" else \
                f"eki: couldn't bring the changes back from `{ws.path}`: {result['why']}"
            if line:
                chunk = f"\n\n*{line}*"
                parts.append(chunk)
                yield chunk

        # usage is whatever the backend volunteered, normalised only in name:
        # an invented number would be worse than an absent one
        usage = dict(getattr(backend, "last_usage", {}) or {})
        if "prompt_tokens" in usage:                # OpenAI-shaped → our names
            usage.setdefault("input_tokens", usage.pop("prompt_tokens"))
            usage.setdefault("output_tokens", usage.pop("completion_tokens", 0))
        if usage:
            meta["usage"] = usage
        checkpoint = getattr(backend, "last_checkpoint", "")
        if checkpoint:
            meta["checkpoint"] = checkpoint     # what /rewind needs to undo this turn's edits
            backend.last_checkpoint = ""                                    # type: ignore[attr-defined]
        if cid:
            text = "".join(parts)
            if ws is not None:
                text = workspace_mod.home_paths(ws, text)   # links point at your files
            self.store.add_turn(cid, "assistant", text, backend.key,
                                choice.reason, meta=meta)
            session = getattr(backend, "last_session", None)
            if session:
                self.store.set_session(cid, backend.key, session)
            # name the thread now that it has an answer in it; not on the
            # run's clock, and never a reason for the run to fail
            task = asyncio.create_task(self._entitle(cid))
            self._side_tasks.add(task)
            task.add_done_callback(self._side_tasks.discard)
            self._observe_turn(run, cid, backend.key, choice.reason, label.task)
            # and see whether the run taught something worth keeping as a
            # skill — also off the run's clock, and never its failure
            if backend.info.capabilities.text:
                seen = {**run, "_copy": ws.path} if ws is not None and ws.mode == "worktree" else run
                task = asyncio.create_task(self._learn(seen, cid, backend.key))
                self._side_tasks.add(task)
                task.add_done_callback(self._side_tasks.discard)

    # ---- the routing table (eki/table.py, docs/routing.md) ------------------

    def _route_names(self) -> List[str]:
        words: List[str] = []
        for ws in self._provider_words().values():
            words += ws
        return words

    def _provider_words(self) -> Dict[str, List[str]]:
        """The words that name each provider, for reading "never use Codex"."""
        out: Dict[str, List[str]] = {}
        for b in self.backends:
            ws = [b.key, b.key.replace("_", " "), (b.info.label or "").lower()]
            kind = b.info.kind
            local = bool(self.options.get(b.key, {}).get("local_model")) or kind in ("mlx", "llamacpp")
            if kind == "claude_code":
                ws += ["claude", "claude code", "opus", "fable", "sonnet", "haiku"]
            elif kind == "codex" and not local:
                ws += ["codex", "openai", "chatgpt", "gpt"]
            elif kind == "gemini_cli":
                ws += ["gemini", "gemini cli", "google"]
            elif local:
                ws += ["local", "local model", "qwen"]
            elif kind in ("anthropic_api", "openai_compat"):
                ws += ["api", "api key", "api keys"]
            if b.info.capabilities.images_out and not b.info.capabilities.text:
                ws += ["image model", "flux", "comfyui"]
            out[b.key] = [w for w in dict.fromkeys(ws) if w]
        return out

    def _subscriptions(self) -> List[Backend]:
        """Subscriptions, the one with the most room first (eki/capacity.py);
        until room is known, the one being spent slowest against its window."""
        subs = [b for b in self.backends if b.info.kind in adapters.AGENT_CLIS
                and not self.options.get(b.key, {}).get("local_model") and not self.policy.is_disabled(b.key)]
        data = capacity_mod.load()
        paces = self.quota.pace() if self.quota else {}
        rooms = {}
        for b in subs:
            reading = (self.quota.latest.get(b.info.quota_source or "") if self.quota else None)
            rooms[b.key] = capacity_mod.room(data, b.info.quota_source or "", reading.windows)["per_hour"] \
                if reading is not None else None
        # a program eki reads no quota for (Gemini CLI) has no room to
        # compare; it waits behind the ones whose room is known
        tracked = [b for b in subs if b.info.quota_source]
        if tracked and all(rooms.get(b.key) is not None for b in tracked):
            return sorted(subs, key=lambda b: (rooms.get(b.key) is None, -(rooms.get(b.key) or 0)))

        def pace(b: Backend) -> float:
            p = paces.get(b.info.quota_source or "")
            return p.factor if p is not None else 1.0
        return sorted(subs, key=lambda b: (pace(b), self.policy.rank(b.key)))

    def _base_table(self) -> Dict[str, List[str]]:
        subs = [b.key for b in self._subscriptions()]
        # the local model with tools (Codex's hands on it), and the local
        # model itself — the one the harness drives, else the biggest text one
        harness = next((b.key for b in self.backends if b.info.kind == "codex"
                        and self.options.get(b.key, {}).get("local_model")), None)
        driven = self.options.get(harness, {}).get("local_model") if harness else None
        raw = driven if driven and self.get(driven) is not None else \
            next((b.key for b in sorted(self.backends, key=lambda b: -(watch_mod.params_of(
                str(self.options.get(b.key, {}).get("model") or "")) or 0))
                  if b.info.kind in ("mlx", "llamacpp") and b.info.capabilities.text
                  and b.key not in self.router.reserved), None)
        images = [b.key for b in self.backends if b.info.capabilities.images_out and not b.info.capabilities.text]
        web = [b.key for b in self.backends if b.info.capabilities.web]
        metered = [b.key for b in self.backends if b.info.kind in ("anthropic_api", "openai_compat")
                   and priors.class_of(b.info.kind, self.options.get(b.key, {}), b.info.capabilities) == "frontier_api"]
        return table_mod.defaults(subs, harness, images, web, metered, raw=raw)

    # ---- staying with a model, and handing over (eki/handoff.py) -------------

    def _bare(self, key: str) -> bool:
        """A model with no tools of its own: it answers, or hands over."""
        b = self.get(key)
        return b is not None and b.info.capabilities.text and not b.info.capabilities.tools \
            and b.info.kind not in adapters.AGENT_CLIS

    def _handoff_targets(self, allowed: Optional[List[str]] = None) -> List[Tuple[str, str]]:
        """Who a model without tools may hand a thread to: the subscriptions'
        harnesses — or, inside a goal's budget, those it allows and the local
        model with Codex's hands."""
        out = []
        for b in self._subscriptions():
            if allowed is None or b.key in allowed:
                out.append((b.key, f"{b.info.label} — files, commands, the web, the screen, and "
                                   "building real software"))
        if allowed is not None:
            for key in self._local_with_tools().values():
                b = self.get(key)
                if b is not None and key in allowed:
                    out.append((key, f"{b.info.label} — files and commands in a folder, on this "
                                     "machine (no web, no screen)"))
        return out

    def _plan(self, label: Any, run: Dict[str, Any], before: List[Dict[str, Any]], cid: str,
              requested: str) -> Tuple[str, str, List[str], str, bool]:
        """(row, why, choices in order, the model the thread stays with, a retry).

        A thread stays with the model that is answering — it has the context.
        It moves when that model hands it over, when you pick another, when
        its last answer failed, or when it can't take this request (no
        quota, not running, no tools — the next choice in the row takes it).
        A picture is always the image models' row."""
        examples = table_mod.load().get("examples") or {}
        failed = False
        last = table_mod.last_answer(before)
        if last is not None:
            m = table_mod.meta_of(last)
            failed = bool(m.get("failed") or m.get("stopped"))
        row, why = table_mod.row_for(label.task, label.difficulty, failed, run["prompt"], examples)
        cells = self.routing_table(cid)
        targets = list((cells.get(row) or {}).get("targets") or [])
        fo = run.get("_failover")
        if fo:
            # out of usage mid-run: the row's next choice on another subscription
            gone = set(fo.get("tried") or [])
            spent = set(fo.get("sources") or [])
            ahead = []
            for t in targets:
                k = table_mod.parse_target(t)[0]
                b = self.get(k)
                if k in gone or b is None or (b.info.quota_source or k) in spent:
                    continue
                ahead.append(t)
            return row, f"{fo.get('label') or fo.get('from')} hit its usage limit", ahead, "", False
        ho = run.get("_handoff")
        if ho:
            subs = [k for k, _ in self._handoff_targets(_allowed_of(run))]
            first = [(f"{ho['to']}@default" if watch_mod.ladder(ho["to"]) else ho["to"])] \
                if ho.get("to") in subs else []
            ahead = first + [t for t in targets if table_mod.parse_target(t)[0] in subs]
            ahead += [f"{s}@default" for s in subs]
            seen: List[str] = []
            for t in ahead:
                if t not in seen and table_mod.parse_target(t)[0] != ho.get("from"):
                    seen.append(t)
            return row, f"handed over by {ho.get('from')}", seen, "", False
        if (last is None or requested or run["images"] or label.task == "image" or failed
                or (cells.get(row) or {}).get("source") == "this thread"):
            if failed and last is not None:
                targets = [t for t in targets if table_mod.parse_target(t)[0] != last.get("backend")]
            return row, why, targets, "", failed
        key = last.get("backend") or ""
        b = self.get(key)
        if b is None or not b.info.capabilities.text:
            return row, why, targets, "", False
        was = table_mod.target_of(last, watch_mod.ladder(key))
        return row, f"{why}; the thread stays with {was}", table_mod.to_front(targets, was), key, False

    #: how much of the thread a program joining it is given
    EARLIER_TURNS = 12
    EARLIER_CHARS = 8000

    def _earlier_for(self, backend: Backend, cid: str, run: Dict[str, Any]) -> str:
        """The conversation so far, for a program that keeps its own session
        and hasn't been part of this thread — empty when it has (it
        remembers) or when there's nothing before."""
        if backend.info.kind not in adapters.AGENT_CLIS or self.store.session(cid, backend.key):
            return ""
        turns = [t for t in self.store.turns(cid) if t["id"] < int(run.get("user_turn") or 0)
                 and t["role"] in ("user", "assistant") and (t.get("content") or "").strip()]
        if not turns:
            return ""
        names = {b.key: b.info.label for b in self.backends}
        lines = []
        for t in turns[-self.EARLIER_TURNS:]:
            who = "Person" if t["role"] == "user" else names.get(t.get("backend") or "", t.get("backend") or "Model")
            body = t["content"].strip()
            if len(body) > 1500:
                body = body[:700] + " […] " + body[-700:]
            lines.append(f"{who}: {body}")
        text = "\n\n".join(lines)
        if len(text) > self.EARLIER_CHARS:
            text = "[…]\n" + text[-self.EARLIER_CHARS:]
        return ("[eki: you're joining a conversation that other models have been part of. "
                f"What was said so far:]\n\n{text}\n\n[eki: the new message follows.]\n\n")

    async def _with_note(self, stream: AsyncIterator[Any], ho: Dict[str, Any]) -> AsyncIterator[Any]:
        """The harness's answer, opened by a line saying who handed it over."""
        frm = next((b.info.label for b in self.backends if b.key == ho.get("from")), ho.get("from"))
        yield f"*{frm} handed this over: {ho.get('brief') or 'it needs tools'}*\n\n"
        async for chunk in stream:
            yield chunk

    def _fails_over(self, e: Exception, run: Dict[str, Any], requested: str) -> bool:
        """A usage limit, on a model eki chose, with hops left."""
        if requested or run.get("images") or not self.settings.get("failover", True):
            return False
        if int((run.get("_failover") or {}).get("hops") or 0) >= failover_mod.MAX_HOPS:
            return False
        return failover_mod.is_limit(str(e))

    def _quota_after_limit(self, backend: Backend) -> None:
        """The window is spent: the router is told before it picks again."""
        try:
            src = backend.info.quota_source or backend.key
            self.quota.mark_exhausted(src)
        except Exception:                           # noqa: BLE001
            pass

    async def _carried_on(self, stream: AsyncIterator[Any], fo: Dict[str, Any],
                          backend: Backend) -> AsyncIterator[Any]:
        """What the model before had written, a line saying it ran out, then
        this one carrying on — one answer in the thread."""
        if fo.get("partial"):
            yield fo["partial"]
        why = (fo.get("why") or "usage limit").split(": ", 1)[-1]
        yield (f"\n\n*— {fo.get('label') or fo.get('from')} hit its usage limit ({why}); "
               f"{backend.info.label} carries on —*\n\n")
        async for chunk in stream:
            yield chunk

    def _local_with_tools(self) -> Dict[str, str]:
        """{local model: the Codex harness driving it}."""
        return {str(self.options.get(b.key, {}).get("local_model")): b.key for b in self.backends
                if b.info.kind == "codex" and self.options.get(b.key, {}).get("local_model")}

    @staticmethod
    def _wants_harness(label: Any, folder: str, images: bool, requested: str) -> bool:
        """Needs tools (files, commands, the screen, the web) → a harness.
        Doesn't → a model directly. Picked by name → whatever was picked."""
        return (not requested and label.task != "image" and not images
                and (label.hands or bool(folder) or label.task in ("repo", "screen", "research")))

    def _typical_seconds(self) -> Dict[str, float]:
        """How long a request usually takes on each backend (the median of
        its recent finished runs) — shown in the table, and the order of two
        local choices."""
        out = {}
        for b in self.backends:
            got = self.runs.durations(b.key, 30)
            if len(got) >= 3:
                got.sort()
                out[b.key] = round(got[len(got) // 2], 1)
        return out

    def routing_table(self, thread: str = "") -> Dict[str, Dict[str, Any]]:
        return table_mod.effective(self._base_table(), table_mod.load(), thread)

    def _target_labels(self) -> "table_mod.Labels":
        names = {b.key: b.info.label or b.key for b in self.backends}
        ladders = {b.key: watch_mod.ladder(b.key) for b in self.backends}
        out = table_mod.Labels(names, ladders)
        for b in self.backends:
            if watch_mod.ladder(b.key):
                for role in ("default", "top", "fast"):
                    t = f"{b.key}@{role}"
                    out[t] = table_mod.label_of(t, names, ladders)
            else:
                out[b.key] = table_mod.label_of(b.key, names, ladders)
        return out

    def _learn_override(self, need: Need, requested: str, cid: str) -> None:
        """You picked a model by name where the table would have picked
        another: noted, and after the same pick for the same row in a few
        threads, the row changes — said, and undoable."""
        try:
            auto = self.router.choose(Need(**{**need.__dict__, "backend": None}))
            if auto.backend is None or auto.backend.key == requested or need.row == "picture":
                return
            rules = table_mod.load()
            entry = {"row": need.row, "from": auto.backend.key, "to": requested,
                     "conversation": cid, "at": int(time.time())}
            rules["overrides"] = (rules.get("overrides") or [])[-299:] + [entry]
            observe_mod.note("friction", signal="override", row=need.row, backend=auto.backend.key,
                             to=requested, conversation=cid)
            if table_mod.learnable(rules["overrides"], rules, need.row, requested):
                cell = self.routing_table().get(need.row) or {}
                first = f"{requested}@default" if watch_mod.ladder(requested) else requested
                rest = [t for t in cell.get("targets") or [] if table_mod.parse_target(t)[0] != requested]
                title = table_mod.TITLES.get(need.row, need.row)
                said = f"you picked {requested} for {title.lower()} in {table_mod.LEARN_AFTER} threads"
                table_mod.set_row(rules, need.row, [first] + rest, "learned", said)
                observe_mod.note("history", what="routing learned", row=need.row, to=requested, why=said)
                if self.settings.get("notify_learned", True):
                    asyncio.create_task(self._notify(f"Learned: {title} goes to {requested} first",
                                                     f"{said}. Undo: eki routing undo {need.row}"))
            table_mod.save(rules)
        except Exception:                           # noqa: BLE001
            pass

    async def _routing_talk(self, run: Dict[str, Any], cid: str, mode: str
                            ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """A message for eki about routing, answered by eki in the thread."""
        yield {"backend": "eki", "reason": "a message about routing, answered by eki"}
        if mode == "show":
            text = self.routing_text(cid)
        elif mode == "why":
            text = self._why_last(cid, run)
        else:
            text = await self._routing_edit(run, cid)
        self.store.add_turn(cid, "assistant", text, "eki", "routing", meta={"run": run["id"], "routing": mode})
        yield text

    def _why_last(self, cid: str, run: Dict[str, Any]) -> str:
        turns = [t for t in self.store.turns(cid) if t["id"] < int(run.get("user_turn") or 0)]
        last = next((t for t in reversed(turns) if t["role"] == "assistant" and t.get("backend")
                     and t["backend"] != "eki"), None)
        if last is None:
            return "Nothing in this thread was routed yet."
        try:
            meta = json.loads(last.get("meta") or "{}")
        except (TypeError, ValueError):
            meta = {}
        lab = meta.get("label") or {}
        out = [f"It went to **{last['backend']}** — {last.get('reason') or 'no reason recorded'}."]
        if lab.get("row"):
            out.append(f"The prompt check read it as {lab.get('task')} · {lab.get('difficulty')} "
                       f"({lab.get('row_why') or ''}), so it was in the row "
                       f"**{table_mod.TITLES.get(lab['row'], lab['row'])}**.")
            cell = self.routing_table(cid).get(lab["row"]) or {}
            labels = self._target_labels()
            out.append("That row now: " + " → ".join(labels.get(t, t) for t in cell.get("targets") or [])
                       + (f" ({cell['source']}: {cell['said']})" if cell.get("source") != "default" else " (default)"))
        out.append("To change it, say so — \"use Claude for code\", \"never use Codex\" — or see all of it with /routing.")
        return "\n\n".join(out)

    async def _routing_edit(self, run: Dict[str, Any], cid: str) -> str:
        message = run["prompt"]
        turns = [t for t in self.store.turns(cid) if t["id"] < int(run.get("user_turn") or 0)]
        previous = next((t["content"] for t in reversed(turns) if t["role"] == "user"), "")
        edit = prefs_mod.plain(message, self._provider_words())
        if edit is None:
            reader = self._watch_reader()
            if reader is None:
                return ("I couldn't read that as a routing rule (no model to read it with). "
                        "Plain forms always work: \"never use Codex\", \"only use Codex if Claude runs out\", "
                        "\"for this thread use Claude\".")
            labels = self._target_labels()
            cells = self.routing_table(cid)
            rows = [{"id": r, "title": t, "examples": ex,
                     "labels": [labels.get(x, x) for x in (cells.get(r) or {}).get("targets") or []]}
                    for r, t, ex in table_mod.ROWS]
            try:
                edit = await self._read(reader, prefs_mod.edit_prompt(
                    message, rows, [{"target": k, "label": v} for k, v in labels.items()], previous)) or {}
            except Exception as e:                  # noqa: BLE001
                return f"I couldn't read that as a routing rule: {e}"[:300]
        return self._apply_edit(edit, message, cid, previous)

    def _apply_edit(self, edit: Dict[str, Any], message: str, cid: str, previous: str) -> str:
        rules = table_mod.load()
        action = str(edit.get("action") or "none")
        labels = self._target_labels()
        target = str(edit.get("target") or "")
        key = table_mod.parse_target(target)[0]
        known = {b.key for b in self.backends}
        row = str(edit.get("row") or "")
        rows = [r for r, _, _ in table_mod.ROWS if r != "picture"] if row == "all" else [row]
        if action in ("first", "never", "backup", "thread") and key not in known:
            return "I understood that as a routing rule, but not which model you meant. Try naming it: Claude, Codex, local."
        name = next((b.info.label for b in self.backends if b.key == key), key)
        if action == "first":
            if not all(r in table_mod.TITLES for r in rows):
                return "I couldn't tell which kind of work that's about. Try: code, writing, quick questions, research."
            cells = self.routing_table()
            drop = {table_mod.parse_target(str(x))[0] for x in edit.get("without") or []} - {key}
            for r in rows:
                ts = table_mod.to_front((cells.get(r) or {}).get("targets") or [], target)
                table_mod.set_row(rules, r, [t for t in ts if table_mod.parse_target(t)[0] not in drop],
                                  "you", message)
        elif action in ("never", "backup"):
            table_mod.add_rule(rules, action, key, "you", message)
        elif action == "thread":
            until = None
            if edit.get("until") == "today":
                lt = time.localtime()
                until = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 59, 0, 0, -1))
            rules.setdefault("threads", {})[cid] = {"targets": [target], "until": until, "said": message}
        elif action == "example":
            if row not in table_mod.TITLES or not previous:
                return "I couldn't tell which request, or which kind of work, you meant."
            table_mod.add_example(rules, row, previous)
        elif action == "forget":
            what = str(edit.get("forget") or "")
            gone = table_mod.forget(rules, what if what != "thread" else cid)
            table_mod.save(rules)
            return ("Taken back: " + ", ".join(gone) + ".") if gone else "There was no rule like that to take back."
        else:
            return "That didn't read as a routing rule, so nothing changed. /routing shows the table."
        table_mod.save(rules)
        observe_mod.note("history", what="routing rule", said=message[:200], action=action)
        reply = str(edit.get("reply") or {
            "never": f"{name} won't be used automatically any more — still there when you pick it by name.",
            "backup": f"{name} is now a backup: used only when nothing ahead of it can take the request.",
            "thread": f"In this thread, {name} goes first" + (" until the end of today." if edit.get("until") else "."),
            "example": f"Noted — requests like that one are {table_mod.TITLES.get(row, row).lower()}.",
            "first": f"{name} goes first for {', '.join(table_mod.TITLES.get(r, r).lower() for r in rows)}.",
        }.get(action, "Done."))
        cells = self.routing_table(cid)
        shown = rows if action in ("first", "example") else [r for r, _, _ in table_mod.ROWS if r != "picture"][:3]
        lines = [f"- {table_mod.TITLES.get(r, r)}: " + " → ".join(labels.get(t, t) for t in (cells.get(r) or {}).get("targets") or [])
                 for r in shown if r in cells]
        return reply + "\n\n" + "\n".join(lines) + "\n\n(/routing shows all of it; \"forget that rule\" takes it back.)"

    # ---- checking it -------------------------------------------------------

    def routing_view(self, thread: str = "") -> Dict[str, Any]:
        labels = self._target_labels()
        cells = self.routing_table(thread)
        secs = self._typical_seconds()
        rows = [{"id": r, "title": t, "examples": ex + ((table_mod.load().get("examples") or {}).get(r) or []),
                 "targets": [{"target": x, "label": labels.get(x, x),
                              "seconds": secs.get(table_mod.parse_target(x)[0])}
                             for x in (cells.get(r) or {}).get("targets") or []],
                 "source": (cells.get(r) or {}).get("source"), "said": (cells.get(r) or {}).get("said")}
                for r, t, ex in table_mod.ROWS]
        data = capacity_mod.load()
        subs = []
        for b in self._subscriptions():
            reading = self.quota.latest.get(b.info.quota_source or "") if self.quota else None
            subs.append({"key": b.key, "label": b.info.label,
                         **(capacity_mod.room(data, b.info.quota_source or "", reading.windows) if reading else {})})
        rules = table_mod.load()
        return {"order": ["what can do it (running, quota, able)", "your rules", "the vendor's ladder",
                          "room left on each subscription"],
                "rows": rows, "subscriptions": subs,
                "rules": {"never": rules.get("never") or [], "backup": rules.get("backup") or [],
                          "threads": rules.get("threads") or {}},
                "ladders": {b.key: watch_mod.ladder(b.key) for b in self.backends if watch_mod.ladder(b.key)}}

    def routing_text(self, thread: str = "") -> str:
        v = self.routing_view(thread)
        out = ["**Routing** — decided in this order: " + " → ".join(v["order"]) + ".", ""]
        out.append("| Kind of work | 1st | 2nd | 3rd | set by |")
        out.append("|---|---|---|---|---|")
        for r in v["rows"]:
            ts = [t["label"] + (f" ~{t['seconds']:g}s" if t.get("seconds") is not None else "")
                  for t in r["targets"]] + ["—", "—", "—"]
            by = r["source"] if r["source"] == "default" else f"{r['source']}: “{(r['said'] or '')[:60]}”"
            out.append(f"| {r['title']} | {ts[0]} | {ts[1]} | {ts[2]} | {by} |")
        if v["subscriptions"]:
            out += ["", "Room on each subscription (learned from what requests cost here):"]
            for sub in v["subscriptions"]:
                if sub.get("per_hour") is not None:
                    out.append(f"- {sub['label']}: ~{sub['left']:g} requests left, {sub['per_hour']:g}/hour until reset")
                else:
                    seen = max((w.get("samples", 0) for w in sub.get("windows") or []), default=0)
                    out.append(f"- {sub['label']}: still learning what a request costs ({seen} measured)")
        rules = v["rules"]
        extra = [f"never: {x['target']} (“{x['said']}”)" for x in rules["never"]] + \
                [f"only as a backup: {x['target']} (“{x['said']}”)" for x in rules["backup"]]
        if extra:
            out += ["", "Your rules: " + "; ".join(extra)]
        return "\n".join(out)

    async def _route(self, prompt: str, thread: str = "", folder: str = "",
                     before_turn: int = 0, allowed: Optional[List[str]] = None,
                     screen: bool = False) -> Tuple[Any, str, Need, Any]:
        """(label, why this row, the need, the choice) — what routing would do, run nothing.
        `screen`: it uses the screen, whatever its words say (a goal that may) — so it
        needs a harness, and a model that can see."""
        try:
            label = await self.classifier.label(prompt, has_folder=bool(folder))
        except Exception:                           # noqa: BLE001
            label = classify.rules(prompt, bool(folder))
        before = [t for t in self.store.turns(thread) if not before_turn or t["id"] < before_turn] \
            if thread else []
        row, row_why, targets, stays, escalate = self._plan(
            label, {"prompt": prompt, "images": False, "cwd": folder,
                    "payload": json.dumps({"allowed": allowed}) if allowed is not None else ""},
            before, thread, "")
        wants_harness = (self._wants_harness(label, folder, False, "") and not (stays and self._bare(stays))) \
            or screen
        need = Need(repo=bool(folder), escalate=escalate, tools=bool(folder) or wants_harness, vision=screen,
                    images_out=label.task == "image", web=label.task == "research" and not stays,
                    task=label.task, difficulty=label.difficulty, row=row,
                    row_title=table_mod.TITLES.get(row, row), targets=targets, allowed=allowed)
        return label, row_why, need, self.router.choose(need)

    async def routing_explain(self, prompt: str, thread: str = "", folder: str = "",
                              before_turn: int = 0) -> Dict[str, Any]:
        """Where a request would go, and why — nothing runs. `before_turn`:
        read the thread only up to that turn (a request replayed in place)."""
        label, row_why, need, choice = await self._route(prompt, thread, folder, before_turn)
        row = need.row
        cells = self.routing_table(thread)
        labels = self._target_labels()
        return {"prompt": prompt, "label": label.to_json(), "row": row, "row_title": need.row_title,
                "row_why": row_why, "row_targets": [labels.get(t, t) for t in need.targets],
                "row_source": (cells.get(row) or {}).get("source"),
                "choice": choice.label, "reason": choice.reason, "rejected": choice.rejected}

    async def routing_replay(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Your recent requests: where each went, where it would go now."""
        out = []
        for r in self.runs.recent(limit * 3):
            if r.get("kind") != "ask" or not r.get("prompt") or not r.get("conversation_id") or not r.get("backend"):
                continue
            full = self.runs.get(r["id"]) or {}
            if full.get("requested") or r["backend"] == "eki" or r["prompt"].lstrip().startswith("/"):
                continue                            # picked by name, eki's own, or a program's command
            now = await self.routing_explain(r["prompt"], thread=r["conversation_id"], folder=r.get("cwd") or "",
                                             before_turn=int(full.get("user_turn") or 0))
            then = r["backend"]
            out.append({"run": r["id"], "prompt": r["prompt"][:120], "then": then,
                        "now": now["choice"], "row": now["row_title"],
                        "label": f"{now['label']['task']} · {now['label']['difficulty']}",
                        "differs": then != now["choice"].split(" ")[0]})
            if len(out) >= limit:
                break
        return out

    def capacity_tick(self) -> None:
        """Fold the latest quota readings into what a request costs."""
        if not self.quota:
            return
        data = capacity_mod.load()
        for source, reading in (self.quota.latest or {}).items():
            keys = [b.key for b in self.backends if b.info.quota_source == source]
            if not keys:
                continue
            capacity_mod.observe(data, source, reading.windows, self.runs.finished_count(keys))
        capacity_mod.save(data)

    # ---- which models are out there (eki/watch.py) --------------------------

    def _watch_reader(self) -> Optional[Backend]:
        """Who reads the pages and the charts: one that can see a picture —
        Claude Code first — or the one setting `watch_reader` names."""
        wanted = str(self.settings.get("watch_reader") or "")
        order = [b for b in self.backends if b.key == wanted] if wanted else []
        order += sorted((b for b in self.backends if b.info.capabilities.text
                         and not self.options.get(b.key, {}).get("local_model")),
                        key=lambda b: (b.info.kind != "claude_code", b.info.kind != "codex",
                                       not b.info.capabilities.vision))
        for b in order:
            if b.info.kind not in adapters.AGENT_CLIS and self._is_up(b.key) is not True:
                continue
            return adapters.build(b.info, self.options.get(b.key, {}))
        return None

    async def _read(self, reader: Backend, prompt: str) -> Optional[Dict[str, Any]]:
        kw: Dict[str, Any] = {"max_tokens": 3000, "temperature": 0.1}
        if reader.info.kind in adapters.AGENT_CLIS:
            watch_mod.HOME.mkdir(parents=True, exist_ok=True)
            kw["cwd"] = str(watch_mod.HOME)
        parts: List[str] = []

        async def collect() -> None:
            async for chunk in reader.stream([Message("user", prompt)], **kw):
                if isinstance(chunk, str):
                    parts.append(chunk)
        try:
            await asyncio.wait_for(collect(), timeout=300)
        finally:
            try:
                await reader.close()
            except Exception:                       # noqa: BLE001
                pass
        return watch_mod.parse_json("".join(parts))

    def _local_current(self) -> List[str]:
        """The local text models you run, biggest first, by their model id."""
        out = []
        for b in self.backends:
            if b.info.kind in ("mlx", "llamacpp") and b.info.capabilities.text:
                model = str(self.options.get(b.key, {}).get("model") or "")
                if model:
                    out.append(model)
        return sorted(out, key=lambda m: -(watch_mod.params_of(m) or 0))

    async def _program_models(self, b: Backend) -> Dict[str, Dict[str, str]]:
        """What a program itself says it runs: for Claude Code, each alias
        and the model it resolves to — from its handshake, no model asked."""
        if b.info.kind != "claude_code":
            return {}
        import uuid
        try:
            session = live.LiveSession(b.live_argv(str(watch_mod.HOME), None, str(uuid.uuid4())),  # type: ignore[attr-defined]
                                       str(watch_mod.HOME), None, "")
            watch_mod.HOME.mkdir(parents=True, exist_ok=True)
            await session.start()
        except Exception:                           # noqa: BLE001
            return {}
        try:
            out = {}
            for m in session.models:
                value = str(m.get("value") or "")
                if not value or value == "default":
                    continue
                # the family alias ("fable", "opus") when the program names
                # one, else its own value ("claude-fable-5-1[1m]")
                name = str(m.get("displayName") or "").lower()
                key = name if re.fullmatch(r"[a-z]+", name) else re.sub(r"\[.*\]$", "", value)
                out[key] = {"resolved": str(m.get("resolvedModel") or ""),
                            "label": str(m.get("description") or m.get("displayName") or "")}
            return out
        finally:
            await session.close()

    async def _program_version(self, c: Any, b: Backend) -> Dict[str, Any]:
        binary = getattr(b, "bin", "") or ""
        installed = ""
        if binary:
            try:
                proc = await asyncio.create_subprocess_exec(binary, "--version",
                                                            stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                installed = watch_mod.version_of(out.decode("utf-8", "replace"))
            except Exception:                       # noqa: BLE001
                pass
        latest = ""
        pkg = watch_mod.NPM.get(b.info.kind)
        if pkg:
            try:
                r = await c.get(f"https://registry.npmjs.org/{pkg}/latest")
                latest = str(r.json().get("version") or "") if r.status_code == 200 else ""
            except Exception:                       # noqa: BLE001
                pass
        return {"installed": installed, "latest": latest,
                "behind": watch_mod.behind(installed, latest),
                "update": watch_mod.update_command(b.info.kind, binary)}

    async def watch_refresh(self, force: bool = False) -> Dict[str, Any]:
        """Once a day: the vendors' own guidance → a ladder per program, and
        Ollama's popular models → suggestions for this Mac. Never raises."""
        before = watch_mod.load()
        if not force and not watch_mod.due(before):
            return before
        async with self._watching:
            try:
                return await self._watch(before)
            except Exception as e:                  # noqa: BLE001
                observe_mod.fault(e, source="watch")
                return before

    async def _watch(self, before: Dict[str, Any]) -> Dict[str, Any]:
        import httpx
        try:
            await self.discover_models()            # what each program can run, today
        except Exception:                           # noqa: BLE001
            pass
        state: Dict[str, Any] = {"at": int(time.time()), "vendors": dict(before.get("vendors") or {}),
                                 "candidates": dict(before.get("candidates") or {}), "errors": []}
        async with httpx.AsyncClient(timeout=30, headers=watch_mod.UA, follow_redirects=True) as c:
            # ---- the vendors
            for b in self.backends:
                src = watch_mod.VENDORS.get(b.info.kind)
                if src is None or self.options.get(b.key, {}).get("local_model"):
                    continue
                reader = self._watch_reader()
                if reader is None:
                    state["errors"].append("nothing to read the pages with")
                    break
                # what the program on this Mac runs — for Claude Code, which
                # model each alias really is (an older CLI's "opus" is an older Opus)
                said = await self._program_models(b)
                for alias, info in said.items():
                    if self.registry.get(b.key, alias) is None:
                        self.registry.seen(b.key, alias, label=alias.title())
                runnable = [{"id": r.model, "label": (said.get(r.model) or {}).get("label") or r.label,
                             "resolved": (said.get(r.model) or {}).get("resolved", "")}
                            for r in self.registry.for_provider(b.key) if r.model]
                program = await self._program_version(c, b)
                program["runs"] = {r["id"]: r["resolved"] for r in runnable if r.get("resolved")}
                try:
                    r = await c.get(src["url"])
                    r.raise_for_status()
                    answer = await self._read(reader, watch_mod.vendor_prompt(
                        src["vendor"], watch_mod.text_of(r.text), runnable, b.info.kind))
                except Exception as e:              # noqa: BLE001
                    state["errors"].append(f"{src['vendor']}: {e}"[:200])
                    observe_mod.note("failed", source="watch", what=f"couldn't read {src['url']}",
                                     error=str(e)[:200])
                    continue
                if not answer:
                    state["errors"].append(f"{src['vendor']}: the reader's answer wasn't JSON")
                    continue
                state["vendors"][b.key] = {
                    "vendor": src["vendor"], "url": src["url"], "at": int(time.time()),
                    "ladder": watch_mod.clean_ladder(answer, runnable),
                    "guidance": str(answer.get("guidance") or "")[:500],
                    "vendor_models": answer.get("vendor_models") or [],
                    "not_offered": [str(x) for x in answer.get("not_offered") or []][:10],
                    "program": {**program, "stale": watch_mod.stale(answer.get("vendor_models") or [],
                                                                    program.get("runs") or {})}}
            # ---- local models
            current = self._local_current()
            if current:
                await self._watch_local(c, state, before, current[0])
        state["suggestions"] = [dict(name=k, **v) for k, v in state["candidates"].items()
                                if v.get("verdict") in ("suggest", "test")]
        watch_mod.save(state)
        said = watch_mod.news(before, state)
        for line in said:
            observe_mod.note("history", what="watch", news=line)
        if said and self.settings.get("notify_learned", True):
            await self._notify("models: " + said[0][:80], "; ".join(said[1:3]) or said[0])
        return state

    async def _watch_local(self, c: Any, state: Dict[str, Any], before: Dict[str, Any],
                           current: str) -> None:
        try:
            r = await c.get(f"{watch_mod.OLLAMA}/search")
            r.raise_for_status()
            listed = watch_mod.parse_search(r.text)
        except Exception as e:                      # noqa: BLE001
            state["errors"].append(f"ollama: {e}"[:200])
            return
        if not listed:
            state["errors"].append("ollama: the list had no models — has the page changed?")
            return
        ceiling = self.models.memory().ceiling_gb
        fam, ver = watch_mod.family(current)
        rows: Optional[List[Dict[str, Any]]] = None
        for item in [m for m in listed if not m["cloud_only"]][:watch_mod.POPULAR]:
            name = item["name"]
            if watch_mod.norm(name) == watch_mod.norm(fam + ver):
                continue                            # what you run (a variant of it is still news)
            seen = (before.get("candidates") or {}).get(name) or {}
            if seen and seen.get("updated") == item["updated"]:
                continue                            # read already, unchanged since
            entry: Dict[str, Any] = {"updated": item["updated"], "pulls": item["pulls"],
                                     "about": item["about"], "at": int(time.time())}
            state["candidates"][name] = entry
            try:
                page = watch_mod.parse_library((await c.get(f"{watch_mod.OLLAMA}/library/{name}")).text, name)
            except Exception as e:                  # noqa: BLE001
                entry.update(verdict="skip", why=f"couldn't read its page: {e}"[:160])
                continue
            tag = watch_mod.pick_tag(page, ceiling, watch_mod.params_of(current))
            if tag is None:
                entry.update(verdict="skip", why=f"no build fits in {ceiling:g} GB")
                continue
            entry["tag"] = tag
            chart, comparison = await self._read_chart(c, name, tag["tag"], page)
            entry["chart"] = chart
            entry["comparison"] = comparison
            v, why = watch_mod.verdict({"name": name}, current, comparison)
            entry.update(verdict=v, why=why)
            if v in ("suggest", "test"):
                if rows is None:
                    from . import suggest as suggest_mod
                    try:
                        rows = await suggest_mod.catalogue(c)
                    except Exception:               # noqa: BLE001
                        rows = []
                entry["build"] = watch_mod.match_build(name, tag["tag"], rows) or ""
                if not entry["build"]:
                    entry["why"] += " (no MLX build of it on Hugging Face yet)"

    async def _read_chart(self, c: Any, name: str, tag: str, page: Dict[str, Any]
                          ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        pics = watch_mod.charts(page["images"])
        if not pics:
            return {"is_chart": False}, None
        folder = watch_mod.HOME / "charts" / name
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, pic in enumerate(pics):
            try:
                r = await c.get(pic["url"])
                if r.status_code == 200 and r.content:
                    kind = (r.headers.get("content-type") or "image/png").split("/")[-1].split(";")[0]
                    path = folder / f"{i}.{'jpg' if kind == 'jpeg' else kind}"
                    path.write_bytes(r.content)
                    paths.append(str(path))
            except Exception:                       # noqa: BLE001
                continue
        reader = self._watch_reader()
        if not paths or reader is None:
            return {"is_chart": False}, None
        try:
            size = f"{watch_mod.params_of(tag):g}B" if watch_mod.params_of(tag) else ""
            chart = await self._read(reader, watch_mod.chart_prompt(name, paths, page.get("readme", ""),
                                                                     size)) or {}
        except Exception:                           # noqa: BLE001
            return {"is_chart": False, "error": "unreadable"}, None
        if not chart.get("is_chart"):
            return chart, None
        labels_all = {k for b in chart.get("benchmarks") or [] for k in (b.get("scores") or {})}
        subject = str(chart.get("subject") or "")
        if subject not in labels_all:
            subject = ""                            # a description, not a label: find it ourselves
        if not subject:
            size = watch_mod.params_of(tag)
            labels = {k for b in chart.get("benchmarks") or [] for k in (b.get("scores") or {})}
            f, v = watch_mod.family(name)
            subject = next((l for l in labels if watch_mod._label_is(l, f, v, size)), "")
        current = self._local_current()[0]
        return chart, watch_mod.compare(chart, subject, current)

    async def watch_take(self, name: str) -> Dict[str, str]:
        """Download and set up a suggested local model (the usual Add Model run)."""
        s = next((x for x in watch_mod.load().get("suggestions") or [] if x["name"] == name), None)
        if s is None:
            raise KeyError(name)
        if not s.get("build"):
            raise ValueError(f"no MLX build of {name} to download yet")
        return await self.deploy(s["build"], label=f"{name} (local)")

    # ---- eki watching itself (eki/observe.py) -------------------------------

    def _observe_turn(self, run: Dict[str, Any], cid: str, backend: str, reason: str,
                      task: str) -> None:
        """What a finished turn says about how eki is doing — cheap, no model."""
        try:
            turns = self.store.turns(cid)
            upto = int(run.get("user_turn") or 0)
            before = [t for t in turns if t["id"] < upto] if upto else turns[:-2]
            said = [s for s in learn_mod.signals(run.get("prompt") or "", before) if s != "asked"]
            if said:
                last = next((t for t in reversed(before) if t["role"] == "assistant"), None)
                observe_mod.note("friction", signal=",".join(said), backend=backend, task=task,
                                 before=(last or {}).get("backend") or "", run=run["id"],
                                 conversation=cid, request=(run.get("prompt") or "")[:200])
        except Exception:                           # noqa: BLE001
            pass

    def _on_run_error(self, run: Dict[str, Any], exc: BaseException) -> None:
        """A run ended in an error: a provider saying no is journalled as
        such; an error in eki's own code is a fault, and may get a fix."""
        fresh = self.runs.get(run["id"]) or run
        if not observe_mod.is_fault(exc):
            observe_mod.note("failed", backend=fresh.get("backend") or "", error=str(exc)[:300],
                             run=run["id"], request=(run.get("prompt") or "")[:200])
            return
        entry = observe_mod.fault(exc, source="run", run=run["id"],
                                  conversation=run.get("conversation_id") or "",
                                  backend=fresh.get("backend") or "", request=run.get("prompt") or "")
        if entry:
            self.fault_seen(entry)

    def fault_seen(self, entry: Dict[str, Any]) -> None:
        """Called on the engine's loop for every fault, from a run or the log."""
        task = asyncio.create_task(self.consider_fix(entry))
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    async def consider_fix(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Propose a fix for a fault if it's due (observe.due), one at a time.
        The result is a self/ branch and a verdict — applied only at the
        `apply` autonomy setting."""
        if str(self.settings.get("self_fix", "propose")) != "propose":
            return {"skipped": "self_fix is off"}
        from . import builds as builds_mod
        if builds_mod.cant_change_code(builds_mod.source()):
            return {"skipped": "installed by a package manager — faults are kept in the journal"}
        why = observe_mod.due(entry, daily=int(self.settings.get("self_fix_daily", observe_mod.DAILY)))
        if why:
            return {"skipped": why}
        if any(i.source == "fault" and i.state in ("queued", "working") for i in selfloop.items()):
            return {"skipped": "another fix is being written"}
        return await self._propose_fix(entry)

    async def _propose_fix(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """A fault becomes a piece of self-work (eki/selfengine.py): the loop
        takes it when eki works on itself; otherwise it starts at once. Either
        way the result is a self/ branch and a verdict, applied only if the
        autonomy setting says so."""
        from . import builds as builds_mod
        sig = entry["signature"]
        root = self._self_root()
        running = builds_mod.running()
        # fix the code that is actually running: a build's commit, or your checkout's HEAD
        base = running.get("commit") if not running.get("dev") and running.get("commit") else "HEAD"
        seen = [e for e in observe_mod.entries(time.time() - observe_mod.QUIET_DAYS * 86400, "fault")
                if e.get("signature") == sig]
        request = observe_mod.brief(entry, seen, observe_mod.recent_commits(root, entry.get("file") or ""))
        # no base check: code with a fault in it may well fail its own tests,
        # and that is the point — the candidate check judges the fix
        it = selfloop.add("fault", f"Fix {sig}", request, key=sig, base=base, check_base=False)
        g = self._self_goal()
        if g is not None and g.state == "active":
            observe_mod.mark(sig, state="queued", item=it.id, at=int(time.time()), error=entry.get("error"))
            self._self_wake()
            return {"state": "queued", "item": it.id}
        observe_mod.mark(sig, state="working", item=it.id, at=int(time.time()), error=entry.get("error"))
        try:
            started = await self._self_start(it)
        except Exception as e:                      # noqa: BLE001
            observe_mod.mark(sig, state="error", why=str(e)[:300])
            selfloop.update(it.id, state="gave up", note=str(e)[:300])
            return {"state": "error", "why": str(e)[:300]}
        return {"state": "working", "item": it.id, "run": started["run"]}

    # ---- a folder per thread (eki/workspace.py) ------------------------------

    def _folder_lock(self, folder: str) -> asyncio.Lock:
        return self._folder_locks.setdefault(os.path.realpath(folder), asyncio.Lock())

    async def _open_workspace(self, run: Dict[str, Any], cid: str
                              ) -> Tuple[workspace_mod.Workspace, Optional[asyncio.Lock]]:
        """The run's place to work, and the lock it holds (lock mode only).

        The thread's copy is synced under the folder's lock, so it never reads
        the folder while another run is bringing changes back into it. A
        second run in the same thread at once gets a copy of its own."""
        folder = run["cwd"]
        lock = self._folder_lock(folder)
        if not self.settings.get("worktrees", True):
            await lock.acquire()
            return workspace_mod.Workspace(folder=folder, mode="lock", path=folder), lock
        key = cid or run["id"]
        try:
            carrying_on = bool((json.loads(run.get("payload") or "{}") or {}).get("resume_of"))
        except (TypeError, ValueError):
            carrying_on = False
        # carrying on after a limit: the copy as the model before left it
        carrying_on = carrying_on or bool(run.get("_failover"))
        async with lock:
            try:
                # carrying on after a restart: the copy as the run left it
                ws = await asyncio.to_thread(workspace_mod.open, folder, key, carrying_on)
                if ws.mode == "worktree" and ws.tree in self._in_copy:
                    ws = await asyncio.to_thread(workspace_mod.open, folder, f"{key}-{run['id']}")
            except workspace_mod.WorkspaceError as e:
                ws = workspace_mod.Workspace(folder=folder, mode="lock", path=folder,
                                             note=f"no copy: {e}"[:200])
        if ws.mode == "lock":
            await lock.acquire()
            return ws, lock
        self._in_copy.add(ws.tree)
        return ws, None

    def _keep_workspace(self, ws: Optional[workspace_mod.Workspace], run: Dict[str, Any]) -> str:
        """A run that failed or was stopped: what it changed stays on a
        branch, not in your folder. Sync on purpose — this runs while the
        run is being torn down."""
        if ws is None or ws.mode != "worktree":
            return ""
        try:
            result = workspace_mod.close(ws, run["id"], run["prompt"], ok=False)
        except workspace_mod.WorkspaceError:
            return ""
        line = workspace_mod.summary(ws, result)
        return f"\n\n*{line}*" if line else ""

    # ---- learning from runs (eki/learn.py) --------------------------------

    def _reviewer(self, did: str) -> Optional[Backend]:
        """Who reviews a run: the backend that did the work (it knows the
        domain), or the one setting `skills_learn_backend` names. A fresh
        one-shot — never the thread's own session."""
        key = str(self.settings.get("skills_learn_backend") or "") or did
        b = self.get(key)
        if b is None or not b.info.capabilities.text:
            return None
        if b.info.kind not in adapters.AGENT_CLIS and self._is_up(key) is not True:
            return None
        return adapters.build(b.info, self.options.get(key, {}))

    def _kind_of(self, key: str) -> str:
        b = self.get(key)
        return b.info.kind if b is not None else ""

    async def _learn(self, run: Dict[str, Any], cid: str, did: str,
                     manual: bool = False) -> Dict[str, Any]:
        """Review a finished run for a lesson and, if there is one, commit it
        to the skill store. Returns what happened; never raises."""
        try:
            return await self._learn_inner(run, cid, did, manual)
        except Exception as e:                      # noqa: BLE001
            learn_mod.record({"run": run.get("id"), "conversation": cid, "backend": did,
                              "result": "error", "note": str(e)[:200]})
            return {"result": "error", "note": str(e)[:200]}

    async def _learn_inner(self, run: Dict[str, Any], cid: str, did: str,
                           manual: bool) -> Dict[str, Any]:
        # an agent may have edited a skill through its link during the run:
        # that is its own commit, before anything eki learns lands on top
        rid = run.get("id") or ""
        await asyncio.to_thread(skills_mod.settle_stray, did, rid)
        mode = str(self.settings.get("skills_learn", "apply"))
        if mode not in learn_mod.MODES or mode == "off":
            return {"result": "off"}
        # what the agent kept for itself during the run, despite being told
        # remembering is eki's: skill folders it wrote are taken in, and
        # notes in Claude Code's memory are looked at below
        since = float(run.get("created_at") or time.time()) - 2
        kind = self._kind_of(did)
        view = {"claude_code": "claude", "codex": "codex", "gemini_cli": "gemini"}.get(kind, "")
        adopted = await asyncio.to_thread(learn_mod.adopt_new_folders, since, view, did, rid) \
            if view else []
        # Claude Code's memory, only in the folder this run worked in
        notes = await asyncio.to_thread(
            learn_mod.saved_notes, since,
            [run.get("cwd") or str(self.SCRATCH), run.get("_copy") or ""]) \
            if kind == "claude_code" else []
        turns = self.store.turns(cid)
        upto = int(run.get("user_turn") or 0)
        before = [t for t in turns if t["id"] < upto] if upto else turns[:-2]
        why = learn_mod.signals(run.get("prompt") or "", before)
        if manual and "asked" not in why:
            why = ["asked", *why]
        if notes:
            why.append("saved")
        if not why:
            return {"result": "nothing to learn", "adopted": adopted} if adopted else \
                {"result": "nothing to learn"}
        entry: Dict[str, Any] = {"run": rid, "conversation": cid, "signals": why,
                                 "manual": manual}
        if adopted:
            entry["adopted"] = adopted
        daily = int(self.settings.get("skills_learn_daily", learn_mod.DAILY) or 0)
        if not ({"asked", "saved"} & set(why)) and learn_mod.budget_left(daily) <= 0:
            return {"result": "over today's budget"}
        reviewer = self._reviewer(did)
        if reviewer is None:
            learn_mod.record({**entry, "backend": did, "result": "skipped",
                              "note": "no backend up to review it"})
            return {"result": "skipped", "note": "no backend up to review it"}
        entry["backend"] = reviewer.info.key
        prompt = await asyncio.to_thread(learn_mod.build_prompt, turns, why, notes)
        kw: Dict[str, Any] = {"max_tokens": 1500, "temperature": 0.3}
        if reviewer.info.kind in adapters.AGENT_CLIS:
            learn_mod.WORKDIR.mkdir(parents=True, exist_ok=True)
            kw["cwd"] = str(learn_mod.WORKDIR)
        parts: List[str] = []

        async def collect() -> None:
            async for chunk in reviewer.stream([Message("user", prompt)], **kw):
                if isinstance(chunk, str):
                    parts.append(chunk)

        try:
            await asyncio.wait_for(collect(), timeout=learn_mod.TIMEOUT)
        except (asyncio.TimeoutError, BackendError) as e:
            learn_mod.record({**entry, "result": "error", "note": str(e)[:200] or "timed out"})
            return {"result": "error", "note": str(e)[:200] or "timed out"}
        finally:
            try:
                await reviewer.close()
            except Exception:                       # noqa: BLE001
                pass
        answer = learn_mod.parse("".join(parts))
        if answer is None:
            learn_mod.record({**entry, "result": "unreadable", "note": "".join(parts)[:200]})
            return {"result": "unreadable"}
        covered = learn_mod.absorbs(answer, notes)
        change, reason = await asyncio.to_thread(learn_mod.check, answer)
        if change is None:
            # "none" can still mean "a skill already says this": then the
            # note's copy goes. A refused change takes nothing with it.
            absorbed = []
            if str(answer.get("action") or "").lower() == "none" and covered:
                absorbed = await asyncio.to_thread(learn_mod.absorb, covered)
            learn_mod.record({**entry, "result": "none", "note": reason,
                              **({"absorbed": absorbed} if absorbed else {})})
            return {"result": "none", "note": reason, "absorbed": absorbed}
        done = await asyncio.to_thread(learn_mod.apply, change, mode, rid, cid)
        absorbed = []
        # only once the lesson is live in the store does the note's copy go;
        # a proposed (off) skill leaves Claude's note where it is
        if covered and done.get("applied") and done.get("enabled"):
            absorbed = await asyncio.to_thread(learn_mod.absorb, covered)
        done["absorbed"] = absorbed
        learn_mod.record({**entry, "result": change["action"] if done.get("applied") else "proposed",
                          "skill": change["name"], "note": change["why"],
                          **({"absorbed": absorbed} if absorbed else {})})
        if done.get("applied") and self.settings.get("notify_learned", True):
            verb = "improved" if change["action"] == "edit" else "learned"
            off = "" if done.get("enabled") else " (off until you turn it on)"
            await self._notify(f"{verb} a skill: {change['name']}{off}", change["why"])
        return {"result": change["action"], **done, "why": change["why"]}

    async def learn_now(self, cid: str) -> Dict[str, Any]:
        """Review the latest finished run in a thread because you asked."""
        run = self.runs.last_done(cid)
        if run is None:
            raise KeyError(cid)
        return await self._learn(run, cid, run.get("backend") or "", manual=True)

    async def _notify(self, title: str, body: str) -> None:
        clean = lambda t: t.replace(chr(34), chr(39)).replace("\\", "/")[:200]   # noqa: E731
        try:
            await asyncio.create_subprocess_exec(
                "osascript", "-e",
                f'display notification "{clean(body)}" with title "eki" subtitle "{clean(title)}"',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            pass

    # ---- Claude Code and Codex, kept open --------------------------------

    def _lives(self, backend: Backend) -> bool:
        """Whether a backend runs as an open session under eki's interface."""
        kind = backend.info.kind
        if kind == "claude_code":
            return bool(self.settings.get("live_claude", True))
        if kind == "codex":
            return bool(self.settings.get("live_codex", True))
        return False

    def _needs_skill_loader(self, backend: Backend) -> bool:
        """Claude Code and Codex load skills themselves (from the links eki
        keeps in their folders); a local or API model gets them from here."""
        caps = backend.info.capabilities
        return (backend.info.kind not in adapters.AGENT_CLIS and caps.text
                and bool(self.settings.get("skills_local", True)))

    async def _skilled(self, backend: Backend, history: List[Message], kw: Dict[str, Any],
                       meta: Dict[str, Any]) -> AsyncIterator[str]:
        """A turn for a model with no skill loader: eki is the loader.

        `/name rest` (or `$name`) hands the skill over outright. Otherwise the
        model sees the names and descriptions, and answering `[[skill:name]]`
        asks for one: that start is held back, and the turn is asked again
        with the skill's instructions. The body is only ever sent when used."""
        try:
            catalog = await asyncio.to_thread(skills_mod.catalog_prompt, "local")
        except OSError:
            catalog = ""
        if not catalog or not history:
            async for chunk in backend.stream(history, **kw):
                yield chunk
            return
        last = history[-1]
        chosen, rest = skills_mod.invoked(last.content) if last.role == "user" else (None, "")
        if chosen is not None:
            meta["skill"] = chosen["name"]
            yield {"kind": "activity", "text": f"Using the {chosen['name']} skill"}  # type: ignore[misc]
            asked = history[:-1] + [Message("system", skills_mod.loaded_prompt(chosen)),
                                    Message("user", rest or f"Go ahead with {chosen['name']}.")]
            async for chunk in backend.stream(asked, **kw):
                yield chunk
            return

        offered = [Message("system", catalog)] + history
        held = ""
        deciding = True
        thinking = ""                           # a reasoning model's <think> goes by untouched
        source = backend.stream(offered, **kw)
        async for chunk in source:
            if not deciding or not isinstance(chunk, str):
                yield chunk
                continue
            if thinking:
                thinking += chunk
                if "</think>" not in thinking:
                    yield chunk
                    continue
                before, _, after = chunk.rpartition("</think>") if "</think>" in chunk else ("", "", "")
                if "</think>" in chunk:
                    yield before + "</think>"
                    chunk = after
                else:                           # the tag was split across chunks
                    yield chunk
                    chunk = thinking.partition("</think>")[2]
                    # what came after the tag was already passed on with it
                    held, thinking = "", ""
                    deciding = False
                    continue
                thinking = ""
                held = ""
                if not chunk:
                    continue
            held += chunk
            t = held.lstrip()
            if t.startswith("<think>"):
                if "</think>" not in held:
                    thinking = held
                    yield held
                    held = ""
                    continue
                head, _, after = held.partition("</think>")
                yield head + "</think>"
                held = after
                if not held:
                    continue
            elif "<think>".startswith(t) and t:
                continue
            if skills_mod.could_be_pick(held) and not skills_mod.PICK_RE.match(held):
                continue
            deciding = False
            pick = skills_mod.picked(held)
            if pick is None:
                yield held
                continue
            # the model asked for a skill: stop that answer and ask again with it
            try:
                await source.aclose()  # type: ignore[attr-defined]
            except Exception:                   # noqa: BLE001
                pass
            meta["skill"] = pick["name"]
            yield {"kind": "activity", "text": f"Using the {pick['name']} skill"}  # type: ignore[misc]
            asked = history[:-1] + [Message("system", skills_mod.loaded_prompt(pick)), history[-1]]
            async for again in backend.stream(asked, **kw):
                yield again
            return
        if deciding and held:
            yield held

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
        if session is not None and session.alive and getattr(session, "cwd", None) and \
                os.path.realpath(str(session.cwd)) != os.path.realpath(self._workdir(cwd)):
            # the thread moved to another folder (eki working on itself, in its
            # own worktree): a program works where it was started, so start it there
            self.live.pop(cid, None)
            await session.close()
            session = None
        if session is not None and session.alive and getattr(session, "screen", None) not in (None,
                                                                                    self._screen_for(cid)):
            # a goal's screen setting changed: its tools are fixed when a session starts
            self.live.pop(cid, None)
            await session.close()
            session = None
        if session is not None and session.alive:
            session.backend_key = backend.key                              # type: ignore[attr-defined]
            return session
        warm = self.warm.pop(cwd or "", None)
        if warm is not None and getattr(warm, "screen", True) != self._screen_for(cid):
            self.warm[cwd or ""] = warm                 # warmed with the screen; this thread may not have it
            warm = None
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
        session = self._new_live(argv, cwd or None, cid)
        try:
            await session.start()
        except RuntimeError as e:
            if resume:
                # the old session is gone (deleted, or another machine's): start over
                argv = backend.live_argv(cwd or None, None, str(uuid.uuid4()))  # type: ignore[attr-defined]
                session = self._new_live(argv, cwd or None, cid)
                await session.start()
            else:
                raise BackendError(str(e))
        if cid:
            self.live[cid] = session
        session.backend_key = backend.key                                  # type: ignore[attr-defined]
        return session

    def _new_live(self, argv: List[str], cwd: Optional[str], cid: str = "") -> live.LiveSession:
        """A Claude Code session under eki: its system prompt addition, and
        eki's own tools served in-process (Settings → Claude tools)."""
        bridge = None
        if self.settings.get("claude_tools", True):
            depth = int(os.environ.get("EKI_DEPTH", "0") or 0)
            # the screen: eki's own tools (mac/tools/hid.swift). Claude Code's
            # built-in server needs an approval dialog only its own front
            # ends show, so it is opt-in beside these (claude_builtin_computer_use)
            bridge = mcpbridge.Bridge(self, cid, depth=depth + 1, screen=self._screen_for(cid))
        claude_bin = next((getattr(b, "bin", "") for b in self.backends
                           if b.info.kind == "claude_code"), "") or ""
        prompt = "\n\n".join(x for x in (str(self.settings.get("claude_system_prompt", "")).strip(),
                                          learn_mod.agent_note(self.settings)) if x)
        session = live.LiveSession(argv, cwd, None, prompt,
                                   bridge=bridge,
                                   extra_servers=mcpregistry.builtin_for_claude(claude_bin))
        session.screen = self._screen_for(cid)                             # type: ignore[attr-defined]
        return session

    def _screen_for(self, cid: str) -> bool:
        """May the thread's agent use the screen tools? Yours, if Settings say so;
        a goal's only if it may use the screen — and then its turns run only
        while you're away. A goal's thread without it runs while you may be
        using the Mac."""
        if not self.settings.get("claude_screen", True):
            return False
        goal = next((g for g in goals_mod.all_goals() if g.conversation == cid), None) if cid else None
        return goal is None or goal.screen

    def claude_binary(self) -> str:
        """Where Claude Code really is — the path macOS wants in its
        Accessibility and Screen Recording lists for the screen tools."""
        for b in self.backends:
            if b.info.kind == "claude_code" and getattr(b, "bin", None):
                return os.path.realpath(b.bin)                                 # type: ignore[arg-type]
        return ""

    async def claude_session(self, cid: str = "", cwd: str = "") -> live.LiveSession:
        """The Claude Code session for a thread — or, with no thread, the
        warm one for a folder — for the things the terminal's panels ask
        the program: MCP servers, models, usage, permission rules…"""
        session = self.live.get(cid) if cid else None
        if isinstance(session, codex_live.CodexSession):
            raise BackendError("that thread is Codex, not Claude Code")
        if session is None or not session.alive:
            session = self.warm.get(cwd or "")
        if session is None or not session.alive:
            backend = next((b for b in self.backends if b.info.kind == "claude_code"
                            and getattr(b, "bin", None)), None)
            if backend is None:
                raise BackendError("Claude Code is not set up")
            if not self.settings.get("live_claude", True):
                raise BackendError("Claude Code isn't kept open (Settings → live)")
            import uuid
            argv = backend.live_argv(cwd or None, None, str(uuid.uuid4()))   # type: ignore[attr-defined]
            session = self._new_live(argv, self._workdir(cwd), cid)
            try:
                await session.start()
            except RuntimeError as e:
                raise BackendError(str(e))
            if cid:
                self.live[cid] = session
            else:
                self.warm[cwd or ""] = session
        return session

    async def claude_control(self, op: str, cid: str = "", cwd: str = "",
                             **kw: Any) -> Dict[str, Any]:
        """What the terminal's panels ask the program, by name. Each op is
        one control request on the thread's session (or the folder's warm
        one); the reply is returned as the program gave it, under a key
        the app knows, so a build that says more is never cut short."""
        session = await self.claude_session(cid, cwd)
        try:
            if op == "mcp":
                servers = await session.mcp_status()
                return {"servers": servers, "registry": mcpregistry.load()}
            if op == "mcp_toggle":
                await session.mcp_toggle(str(kw["name"]), bool(kw.get("enabled", True)))
                return {"servers": await session.mcp_status()}
            if op == "mcp_reconnect":
                try:
                    await session.mcp_reconnect(str(kw["name"]))
                except RuntimeError as e:
                    if "disabled" not in str(e).lower():
                        raise
                    # Reconnect on a disabled server means "bring it back"
                    await session.mcp_toggle(str(kw["name"]), True)
                    await session.mcp_reconnect(str(kw["name"]))
                return {"servers": await session.mcp_status()}
            if op == "mcp_authenticate":
                reply = await session.mcp_authenticate(str(kw["name"]), str(kw.get("redirect_uri") or ""))
                return {"reply": reply, "servers": await session.mcp_status()}
            if op == "mcp_oauth_callback":
                reply = await session.mcp_oauth_callback(str(kw["name"]), str(kw["callback_url"]))
                return {"reply": reply, "servers": await session.mcp_status()}
            if op == "mcp_clear_auth":
                await session.mcp_clear_auth(str(kw["name"]))
                return {"servers": await session.mcp_status()}
            if op == "mcp_import":
                status = await session.mcp_status()
                return {"registry": mcpregistry.import_from_claude(status, list(kw.get("names") or []))}
            if op == "mcp_apply":
                # the registry's servers, added to this session without a restart
                await session.mcp_set_servers(mcpregistry.for_claude()["mcpServers"])
                return {"servers": await session.mcp_status()}
            if op == "models":
                return {"models": await session.list_models(), "model": session.model,
                        "account": session.account}
            if op == "set_model":
                await session.set_model(str(kw["model"]))
                return {"model": session.model}
            if op == "account":
                return {"account": session.account, "version": session.version,
                        "program": self.claude_binary(),
                        "capabilities": session.capabilities, "tools": session.tools,
                        "agents": session.agents, "output_style": session.output_style,
                        "output_styles": session.output_styles}
            if op == "permission_mode":
                await session.set_permission_mode(str(kw["mode"]))
                return {"permission_mode": session.permission_mode}
            if op == "thinking":
                await session.set_thinking(kw.get("max_tokens"), kw.get("display"))
                return {"ok": True}
            if op == "usage":
                return {"usage": await session.usage()}
            if op == "context":
                return {"context": await session.context_usage(str(kw.get("detail") or "summary"))}
            if op == "rules":
                return {"rules": await session.permission_rules()}
            if op == "rewind":
                target = str(kw.get("checkpoint") or "")
                if not target and kw.get("turn"):
                    turn = next((t for t in self.store.turns(cid) if str(t.get("id")) == str(kw["turn"])), None)
                    meta = (turn or {}).get("meta") or {}
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except ValueError:
                            meta = {}
                    target = str(meta.get("checkpoint") or "")
                if not target:
                    raise BackendError("that turn has no checkpoint to rewind to")
                return {"rewind": await session.rewind_files(target, bool(kw.get("dry_run")))}
            if op == "rename":
                await session.rename(str(kw["title"]))
                return {"ok": True}
            if op == "background":
                return {"tasks": await session.background_tasks()}
            if op == "stop_task":
                await session.stop_task(str(kw["task_id"]))
                return {"ok": True}
            if op == "reload_skills":
                return {"skills": await session.reload_skills()}
            if op == "skills":
                # the program lists skills as commands; reload_skills answers
                # with just the skills, current as of now
                reply = await session.reload_skills()
                skills = (reply or {}).get("skills") if isinstance(reply, dict) else None
                if not isinstance(skills, list):
                    skills = [c for c in session.commands if ":" in str(c.get("name", ""))
                              or str(c.get("name", "")) in session.skills]
                return {"skills": [s for s in skills if isinstance(s, dict) and s.get("name")]}
            if op == "hooks":
                return {"hooks": await session.control({"subtype": "get_hooks_listing"})}
            if op == "agents":
                return {"agents": session.agents}
            if op == "memory":
                return {"memory": await session.control({"subtype": "get_memory_dialog"})}
            if op == "effort":
                # the session's flag layer (what --effort sets), not a settings file
                await session.control({"subtype": "apply_flag_settings",
                                       "settings": {"effortLevel": str(kw["effort"])}})
                return {"effort": str(kw["effort"])}
            if op == "output_style":
                await session.update_settings({"outputStyle": str(kw["style"])}, "localSettings")
                session.output_style = str(kw["style"])
                return {"output_style": session.output_style}
            if op == "settings":
                return {"settings": await session.settings()}
            if op == "update_settings":
                await session.update_settings(dict(kw.get("settings") or {}),
                                              str(kw.get("source") or "userSettings"))
                return {"ok": True}
            if op == "interrupt":
                await session.interrupt()
                return {"ok": True}
            if op == "status":
                return {"alive": session.alive, "busy": session.busy, "model": session.model,
                        "session": session.session_id, "permission_mode": session.permission_mode,
                        "version": session.version, "context_window": session.context_window,
                        "servers": session.mcp_servers, "commands": session.commands,
                        "background": session.background, "rate_limits": session.rate_limits}
        except KeyError as e:
            raise BackendError(f"{op} needs {e}")
        except asyncio.TimeoutError:
            raise BackendError(f"Claude Code didn't answer {op}")
        except RuntimeError as e:
            # the running build doesn't know the request, or refused it
            raise BackendError(str(e)[:300])
        raise BackendError(f"unknown op {op}")

    async def agent_control(self, op: str, cid: str = "", cwd: str = "", backend_key: str = "",
                            **kw: Any) -> Dict[str, Any]:
        """The panels, for whichever program the thread (or the picked
        backend) is: Claude Code's or Codex's, same op names."""
        key = (backend_key or "").partition(":")[0]
        kind = ""
        if key:
            chosen = self.get(key)
            kind = chosen.info.kind if chosen is not None else ""
        if not kind and cid:
            session = self.live.get(cid)
            if isinstance(session, codex_live.CodexSession):
                kind = "codex"
            elif session is not None:
                kind = "claude_code"
            else:
                last = next((t for t in reversed(self.store.turns(cid))
                             if t.get("role") == "assistant" and t.get("backend")), None)
                if last:
                    chosen = self.get(str(last["backend"]).partition(":")[0])
                    kind = chosen.info.kind if chosen is not None else ""
        if kind == "codex":
            return await self.codex_control(op, cid, cwd, key, **kw)
        if kind == "claude_code":
            return await self.claude_control(op, cid, cwd, **kw)
        raise BackendError("pick Claude Code or Codex for its panels")

    async def codex_session(self, cid: str = "", cwd: str = "", key: str = "") -> codex_live.CodexSession:
        """The thread's Codex session, or a warm one for the folder."""
        session = self.live.get(cid) if cid else None
        if session is not None and not isinstance(session, codex_live.CodexSession):
            raise BackendError("that thread is Claude Code, not Codex")
        if session is None or not session.alive:
            session = self.warm.get("codex|" + (cwd or ""))
        if session is None or not session.alive:
            backend = self.get(key) if key else None
            if backend is None or backend.info.kind != "codex":
                backend = next((b for b in self.backends if b.info.kind == "codex"
                                and getattr(b, "bin", None)), None)
            if backend is None:
                raise BackendError("Codex is not set up")
            if not self.settings.get("live_codex", True):
                raise BackendError("Codex isn't kept open (Settings → live)")
            session = await self._codex_session(cid, backend, self._workdir(cwd), "", None)
            if not cid:
                self.live.pop("", None)
                self.warm["codex|" + (cwd or "")] = session
        return session

    async def codex_control(self, op: str, cid: str = "", cwd: str = "", key: str = "",
                            **kw: Any) -> Dict[str, Any]:
        """Codex's side of the panels, over its app-server (see
        docs/claude-code.md for the op → request table)."""
        session = await self.codex_session(cid, cwd, key)
        try:
            if op == "mcp":
                return {"servers": await session.mcp_status(), "registry": mcpregistry.load()}
            if op == "mcp_authenticate":
                reply = await session.control("mcpServer/oauth/login",
                                              {"name": str(kw["name"]), "threadId": session.session_id},
                                              timeout=300)
                return {"reply": reply, "servers": await session.mcp_status()}
            if op in ("mcp_reconnect", "mcp_apply"):
                # Codex re-reads its config: the registry's managed block included
                mcpregistry.render_codex()
                await session.control("config/mcpServer/reload", {}, timeout=60)
                return {"servers": await session.mcp_status()}
            if op == "mcp_toggle":
                raise BackendError("Codex has no per-session toggle; remove the server from eki's registry or its config.toml")
            if op == "models":
                return {"models": await session.list_models(), "model": session.model,
                        "account": await self._codex_account(session)}
            if op == "set_model":
                await session.set_model(str(kw["model"]))
                return {"model": session.model}
            if op == "account":
                return {"account": await self._codex_account(session), "version": "",
                        "capabilities": [], "tools": [], "agents": [], "output_style": "",
                        "output_styles": []}
            if op == "permission_mode":
                mode = str(kw["mode"])
                table = {
                    "default": ({"type": "workspaceWrite"}, "on-request"),
                    "acceptEdits": ({"type": "workspaceWrite"}, "on-request"),
                    "plan": ({"type": "readOnly"}, "on-request"),
                    "bypassPermissions": ({"type": "dangerFullAccess"}, "never"),
                }
                if mode not in table:
                    raise BackendError(f"no Codex equivalent of {mode}")
                session.sandbox_policy, session.approval_policy = table[mode]
                session.permission_mode = mode                                  # type: ignore[attr-defined]
                return {"permission_mode": mode}
            if op == "thinking":
                # Codex has reasoning effort, not a thinking budget: "off" means low
                if kw.get("max_tokens") == 0:
                    session.effort = "low"
                return {"ok": True}
            if op == "effort":
                session.effort = str(kw.get("effort") or "")
                return {"effort": session.effort}
            if op == "usage":
                return {"usage": await session.usage_panel()}
            if op == "context":
                return {"context": await session.context_panel()}
            if op == "rules":
                profiles = await session.control("permissionProfile/list", {})
                rules = [{"toolName": "profile", "ruleContent": p.get("id", ""),
                          "behavior": "allow" if p.get("allowed") else "deny", "source": "codex"}
                         for p in (profiles or {}).get("data") or [] if isinstance(p, dict)]
                if session.sandbox_policy:
                    rules.insert(0, {"toolName": "sandbox", "ruleContent": session.sandbox_policy.get("type", ""),
                                     "behavior": "allow", "source": "this thread"})
                return {"rules": {"state": {"rules": rules}}}
            if op == "rewind":
                n = int(kw.get("turns") or 1)
                if kw.get("dry_run"):
                    return {"rewind": {"canRewind": True, "numTurns": n, "dry_run": True}}
                reply = await session.control("thread/rollback", {"threadId": session.session_id, "numTurns": n},
                                              timeout=60)
                return {"rewind": reply or {"rolledBack": n}}
            if op == "rename":
                await session.control("thread/name/set", {"threadId": session.session_id, "name": str(kw["title"])})
                return {"ok": True}
            if op in ("skills", "reload_skills"):
                return {"skills": await session.skills()}
            if op == "hooks":
                reply = await session.control("hooks/list", {})
                hooks = []
                for group in (reply or {}).get("data") or []:
                    for h in (group or {}).get("hooks") or []:
                        if isinstance(h, dict):
                            hooks.append({"event": h.get("event") or h.get("eventName") or "",
                                          "matcher": h.get("matcher") or "", "source": h.get("source") or "codex",
                                          "displayText": h.get("command") or h.get("displayText") or ""})
                return {"hooks": {"hooks": hooks, "events": []}}
            if op == "settings":
                return {"settings": await session.control("config/read", {}) or {}}
            if op == "plugins":
                return {"plugins": await session.control("plugin/list", {}, timeout=60)}
            if op == "memory":
                return {"memory": {"files": []}}
            if op in ("agents", "background"):
                return {"agents": [], "tasks": {}}
            if op == "interrupt":
                await session.control("turn/interrupt", {"threadId": session.session_id, "turnId": session.turn_id})
                return {"ok": True}
            if op == "status":
                model = session.model
                if not model:
                    try:
                        cfg = await session.control("config/read", {})
                        model = str(((cfg or {}).get("config") or {}).get("model") or "")
                    except (RuntimeError, asyncio.TimeoutError):
                        pass
                return {"alive": session.alive, "busy": session.busy, "model": model,
                        "session": session.session_id,
                        "permission_mode": getattr(session, "permission_mode", "")
                        or ("bypassPermissions" if session.permissions == "auto" else "default"),
                        "version": "", "context_window": _codex_window(session),
                        "servers": [], "commands": session.commands, "background": {},
                        "rate_limits": session.rate_limits}
        except KeyError as e:
            raise BackendError(f"{op} needs {e}")
        except asyncio.TimeoutError:
            raise BackendError(f"Codex didn't answer {op}")
        except RuntimeError as e:
            raise BackendError(str(e)[:300])
        raise BackendError(f"Codex has no {op}")

    async def _codex_account(self, session: codex_live.CodexSession) -> Dict[str, Any]:
        try:
            reply = await session.control("account/read", {})
        except (RuntimeError, asyncio.TimeoutError):
            return {}
        acct = (reply or {}).get("account") or {}
        return {"email": acct.get("email", ""), "subscriptionType": acct.get("planType", ""),
                "apiProvider": acct.get("type", "")}

    async def _codex_session(self, cid: str, backend: Backend, cwd: str, model: str,
                             resume: Optional[str]) -> codex_live.CodexSession:
        argv = backend.live_argv()                                          # type: ignore[attr-defined]
        screen = self._screen_for(cid)
        if not screen:
            # Codex reaches eki's tools through `eki mcp`: that one, without the screen
            argv = argv[:-1] + mcpregistry.codex_screen_off() + argv[-1:]
        wanted = model or str(self.options.get(backend.key, {}).get("model") or "")
        permissions = str(self.settings.get("permissions", "auto"))
        local = bool(self.options.get(backend.key, {}).get("local_model"))
        guidance = "\n\n".join(x for x in (codex_live.LOCAL_INSTRUCTIONS if local else "",
                                            learn_mod.agent_note(self.settings)) if x)
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
        session.screen = screen                                            # type: ignore[attr-defined]
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
            async for chunk in self._live_turn(run, cid, backend, "", send=False, until_quiet=8.0, nudge=True):
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
                         model: str, send: bool = True, until_quiet: float = 0.0,
                         nudge: bool = False) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """One turn through the open session: text as it streams, tool
        activity as lines, questions and permission prompts as events the
        app turns into cards and answers through `answer()`."""
        session = await self._live_session(cid, backend, run["cwd"] or "", model)
        saved_session = ""
        if cid and session.session_id:
            # known from the handshake: a turn cut short can still be resumed
            self.store.set_session(cid, backend.key, session.session_id)
            saved_session = session.session_id
        if model and (not session.model or model not in session.model):
            try:
                await session.set_model(model)
            except RuntimeError:
                pass
        rid = run["id"]
        usage: Dict[str, Any] = {}
        context: Dict[str, int] = {}
        checkpoint = ""
        # a local model announces and stops; so does a program carrying on by
        # itself with nobody to say "go on" — both get told to
        local = bool(self.options.get(backend.key, {}).get("local_model")) or nudge
        nudges = 0
        try:
            prompt = run["prompt"]
            attached = list((json.loads(run.get("payload") or "{}") or {}).get("attachments") or [])
            while True:
                if send:
                    await session.send(prompt, images=attached or None)
                    attached = []                       # once; a nudge carries no pictures
                send = True                         # a nudge, if one follows, is sent
                tail: List[str] = []                # words since the last tool call
                async for ev in session.turn(until_quiet=until_quiet):
                    if cid and session.session_id and session.session_id != saved_session:
                        # as soon as it's known, not when the turn ends: a
                        # turn cut off by a restart is carried on by this id
                        saved_session = session.session_id
                        self.store.set_session(cid, backend.key, saved_session)
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
                    elif kind in ("ask", "permission", "elicitation", "dialog"):
                        self.pending[rid] = {**ev, "run": rid}
                        yield {"kind": kind, **{k: v for k, v in ev.items() if k != "kind"}}
                    elif kind in ("thinking", "mcp"):
                        yield {"kind": kind, **{k: v for k, v in ev.items() if k != "kind"}}
                    elif kind == "needs_permission":
                        from . import launcher as launcher_mod
                        # under the eki app, it's eki macOS must let in — not the tool below it
                        program = str(launcher_mod.APP) if launcher_mod.running_under() else \
                            (mcpbridge._helper() or "") if "eki-hid" in ev.get("text", "") \
                            else self.claude_binary()
                        yield {"kind": kind, "what": ev["what"], "text": ev.get("text", ""),
                               "program": program}
                    elif kind == "checkpoint":
                        checkpoint = ev["uuid"]
                    elif kind == "cancel":
                        self.pending.pop(rid, None)
                        yield {"kind": "cancel", "request_id": ev["request_id"]}
                    elif kind == "rate_limit":
                        self._note_rate_limits(ev["info"])
                        why = failover_mod.rejected(ev["info"])
                        if why and self.settings.get("failover", True):
                            # don't let it wait for the reset: another model can carry on now
                            if session.alive:
                                await session.interrupt()
                            raise BackendError(f"{backend.info.label}: {why}")
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
                    until_quiet = 0.0                   # a real turn follows the nudge
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
        backend.last_checkpoint = checkpoint                                # type: ignore[attr-defined]

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
        """The slash commands for the *picked* backend, and only then: Claude
        Code's own (a session is opened ahead for the folder and kept for the
        first run) plus eki's panels; Codex's plus its panels; a model with no
        loader gets eki's skills. Auto picks nothing yet — commands shared by
        every provider are a later, careful step (ROADMAP, Stage 1)."""
        if not backend_key:
            return []
        chosen = self.get(backend_key.partition(":")[0])
        if chosen is None:
            return []
        if chosen.info.kind == "codex":
            session = self.live.get(cid) if cid else None
            base = (list(session.commands) if isinstance(session, codex_live.CodexSession)
                    else list(codex_live.COMMANDS))
            base = [c for c in base if c.get("name") != "model"]        # eki's picker owns the model
            return with_panels(base + _skill_commands("codex"), CODEX_PANELS)
        if chosen.info.kind != "claude_code":
            # a model with no loader: eki's skills, and the panel for them
            return _skill_commands("local") + [SKILLS_PANEL]
        session = self.live.get(cid) if cid else None
        if isinstance(session, codex_live.CodexSession):
            session = None
        if session is None or not session.alive:
            session = self.warm.get(cwd or "")
        if session is None or not session.alive:
            backend = chosen if getattr(chosen, "bin", None) else None
            if backend is None or not self.settings.get("live_claude", True):
                return []
            import uuid
            argv = backend.live_argv(cwd or None, None, str(uuid.uuid4()))   # type: ignore[attr-defined]
            session = self._new_live(argv, self._workdir(cwd), cid)
            try:
                await session.start()
            except RuntimeError:
                return []
            self.warm[cwd or ""] = session
        return with_panels(session.commands, CLAUDE_PANELS)

    async def close_idle_claude(self) -> int:
        """Close Claude Code sessions that aren't mid-turn, so they reopen
        with the current settings; a thread's session resumes by its id."""
        closed = 0
        for table in (self.live, self.warm):
            for key, session in list(table.items()):
                if isinstance(session, live.LiveSession) and not session.busy:
                    await session.close()
                    table.pop(key, None)
                    closed += 1
        return closed

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

    def _picture_before(self, run: Dict[str, Any]) -> Dict[str, Any]:
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
                "source": made.get("source") or "",
                "paper": made.get("paper") if isinstance(made.get("paper"), dict) else {}}

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

    # ---- goals: things eki keeps doing (eki/goals.py, eki/shift.py) ---------------------------

    def _shift_on(self) -> bool:
        return str(self.settings.get("background", "local")) != "off"

    def _goal_allowed(self, g: "goals_mod.Goal") -> List[str]:
        """What a goal may use: the models on this machine, and — if it may
        use subscriptions — those with spare room (under pace for the week,
        never the last 30% of a window)."""
        out = [b.key for b in self.backends if b.info.cost.tier == 0 and b.key not in self.router.reserved]
        # the screen wants a model that can see and act: a goal that uses it may use subscriptions
        if g.spare or g.screen:
            reserve = float(self.settings.get("background_reserve", shift_mod.RESERVE))
            for b in self._subscriptions():
                if shift_mod.spare_room(self.quota.latest.get(b.info.quota_source or ""), reserve=reserve).ok:
                    out.append(b.key)
        return out

    def _blocked_why(self, g: "goals_mod.Goal", need: Need) -> str:
        """Why no model within the goal's budget can take its turn."""
        if not (g.spare or g.screen):
            everyone = self.router.choose(Need(**{**need.__dict__, "allowed": None}))
            if everyone.backend is not None and everyone.backend.info.cost.tier != 0:
                return (f"needs {everyone.backend.key} — let this goal use your subscriptions "
                        "(it only takes their spare room)")
            return "no model on this machine can take it"
        return "its subscriptions have no spare room right now (under pace, last 30% kept for you)"

    def _goal_run(self) -> Dict[str, Any]:
        return (self.runs.get(self._shift_run) or {}) if self._shift_run else {}

    def _shift_step_out(self, why: str) -> bool:
        """Cancel the goal's turn in progress; it's taken up again later."""
        rid = self._shift_run
        if not rid or rid not in self.runner.running:
            return False
        self.runner.cancel(rid)
        self._shift_state = {"state": "stepped out", "why": why, "at": int(time.time())}
        return True

    async def _shift_unload(self, why: str) -> List[str]:
        """Give back the memory of models the shift loaded, unless a run uses them."""
        gone = []
        for key in list(self._shift_loaded):
            if not self.models.busy(key):
                await self.models.stop(key)
                gone.append(key)
            self._shift_loaded.discard(key)
        if gone:
            observe_mod.note("history", what="shift unloaded", models=gone, why=why)
        return gone

    def _asks_running(self) -> bool:
        """One of your requests is running — not a goal's turn, nor anything
        an agent started through eki's tools, nor a program carrying on."""
        return any(_yours(self.runs.get(rid) or {}) for rid in self.runner.running)

    def _goal_finished(self) -> None:
        """The turn that just ended: where its goal stands now."""
        run = self._goal_run()
        gid = self._shift_goal
        self._shift_run, self._shift_goal, self._shift_local = "", "", False
        self._shift_screen = self._shift_on_time = False
        if not run or not gid:
            return
        try:
            g = goals_mod.get(gid)
        except goals_mod.GoalError:
            return                                  # removed while it ran
        state = run.get("state")
        seconds = round((run.get("ended_at") or time.time()) - (run.get("started_at") or time.time()), 1)
        entry = {"at": int(time.time()), "goal": gid, "run": run["id"], "backend": run.get("backend"),
                 "seconds": seconds, "state": state}
        if state == "cancelled":
            entry["state"] = "stepped out"          # not the goal's fault: tried again when there's room
            if self._shift_state.get("state") == "stepped out":
                entry["why"] = self._shift_state.get("why")
            goals_mod.update(g.id, turns=max(0, g.turns - 1),
                             next_at=max(g.next_at, int(time.time()) + goals_mod.STEP_OUT_PAUSE))
        elif g.kind == "self":
            # the piece of self-work said where it stands itself (eki/selfengine.py)
            self._self_goal_finished(g, run, entry)
        else:
            ok = state == "done"
            answer = run.get("output") or ""
            if g.conversation:
                last = [t for t in self.store.turns(g.conversation) if t["role"] == "assistant"]
                answer = (last[-1].get("content") if last else "") or answer
            before = g
            g = goals_mod.after_turn(g, answer, ok)
            entry["outcome"] = goals_mod.outcome(answer) if ok else "failed"
            if not ok:
                entry["error"] = (run.get("error") or "")[:200]
            self._tell(before, g, entry["outcome"], answer)
        goals_mod.note(entry)

    def _tell(self, before: "goals_mod.Goal", g: "goals_mod.Goal", said: str, answer: str) -> None:
        """A notification when a goal needs you, is done, or this time's run is:
        not for every turn of a goal that's carrying on."""
        if not self.settings.get("notify_goals", True):
            return
        lines = [x.strip() for x in (answer or "").splitlines()
                 if x.strip() and not goals_mod.MARK.match(x)]
        if said == "waiting":
            body = lines[-1] if lines else "it asked you something"
            title = "Needs you: "
        elif g.state == "stuck" and before.state != "stuck":
            body, title = g.note, "Stuck: "
        elif said == "done" or (g.repeats and said != "failed"):
            body, title = (lines[-1] if lines else "done"), ("" if g.repeats else "Done: ")
        else:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._notify(title + g.text[:60], body))
        except RuntimeError:
            return
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    async def shift_tick(self) -> Dict[str, Any]:
        """Give the next goal that's due a turn, if the machine has room; step out if not."""
        now = int(time.time())
        # who's at the keyboard, eki's own clicks and keys left out
        you = await asyncio.to_thread(self._presence.read)
        if self._shift_run and self._shift_run in self.runner.running:
            if self._shift_screen and self._presence.since(self._shift_started):
                self._shift_step_out("you're back — it was using the screen")
                self._awake.hold()                  # the screen may sleep again
            elif self._shift_on_time:
                pass                                # its time: it carries on (only the screen gives way)
            elif any(g.on_time and goals_mod.due(g, now) and not g.note for g in goals_mod.all_goals()):
                self._shift_step_out("a goal's time came")   # taken up again after it
            elif self._shift_local and self._asks_running():
                self._shift_step_out("your request comes first")
            else:
                held = shift_mod.must_stop(bool(self.settings.get("background_on_battery", False)))
                # a spike (a picture model loading beside the writer) passes; a
                # warning that lasts, or anything critical, doesn't
                self._pressure = self._pressure + 1 if not held.ok else 0
                lasting = "critical" in held.why or "battery" in held.why or self._pressure >= 3
                if not held.ok and lasting and (self._shift_local or "battery" in held.why):
                    self._shift_step_out(held.why)
                    await self._shift_unload(held.why)
                    self._pressure = 0
            return self._shift_state
        if self._shift_run:
            self._goal_finished()
        if not self._shift_on():
            self._awake.let_go()
            self._shift_state = {"state": "off", "why": "background work is off", "at": now}
            return self._shift_state
        goals = goals_mod.all_goals()
        for g in goals:                             # a reply lets a waiting goal carry on
            if g.state == "waiting" and g.conversation and goals_mod.replied(g, self.store.turns(g.conversation)):
                goals_mod.update(g.id, state="active", next_at=0, note="")
            elif goals_mod.stale(g, now):           # an on-time goal the Mac slept through: next time
                goals_mod.note({"at": now, "goal": g.id, "state": "skipped",
                                "why": "its time passed hours ago (the Mac was asleep or off)"})
                goals_mod.update(g.id, next_at=goals_mod._next(g, now))
        # on-time goals first; they don't wait for your request to finish
        due = sorted((g for g in goals_mod.all_goals() if goals_mod.due(g, now)),
                     key=lambda g: (not g.on_time, g.next_at, g.created_at))
        if self._asks_running():
            due = [g for g in due if g.on_time]
            if not due:
                self._shift_state = {"state": "waiting", "why": "your request is running", "at": now}
                return self._shift_state
        if not due:
            self._awake.let_go()
            self._shift_state = {"state": "idle", "at": now,
                                 "why": "nothing due" if goals else "no goals yet"}
            return self._shift_state
        picked = None
        for g in due:
            if g.kind == "self":
                # eki working on itself: its turn is the next piece of self-work
                # (eki/selfloop.py), not the goal's sentence
                plan = await self._self_plan(g, now)
                if isinstance(plan, str):
                    # look again later — or at once, when something it waits on changes (_self_wake)
                    goals_mod.update(g.id, note=plan, next_at=now + selfengine_mod.IDLE_RECHECK)
                    continue
                picked = (plan[0], plan[1], plan[2], "", True, plan[3])
                break
            if g.screen:
                wait = await self._screen_wait(you)
                if wait:
                    if g.note != wait:
                        goals_mod.update(g.id, note=wait)
                    continue
            text = goals_mod.prompt(g, now)
            allowed = self._goal_allowed(g)
            # a fresh one starts this time's run in a new thread — unless you've just answered it
            new_thread = not g.conversation or (g.fresh and g.repeats and not goals_mod.replied(
                g, self.store.turns(g.conversation)))
            # routed on the goal's own words, inside its budget — eki's framing isn't the work
            _, _, need, choice = await self._route(g.text, "" if new_thread else g.conversation, g.folder,
                                                   allowed=allowed, screen=g.screen)
            if choice.backend is not None:
                picked = (g, choice.backend.key, allowed, text, new_thread)
                break
            why = self._blocked_why(g, need)
            if g.note != why:
                goals_mod.update(g.id, note=why)
        if picked is None:
            self._awake.let_go()
            self._shift_state = {"state": "idle", "why": f"{due[0].text[:60]}: {goals_mod.get(due[0].id).note}",
                                 "at": now}
            return self._shift_state
        g, key, allowed, text, new_thread = picked[:5]
        self_item = picked[5] if len(picked) > 5 else None
        local = self._local_for(key)
        on_machine = self.get(key).info.cost.tier == 0
        pids = [os.getpid()] + [models_mod.listener_pid(m.port) or 0
                                for m in self.models.models.values() if m.running and m.port]
        gate = shift_mod.power(bool(self.settings.get("background_on_battery", False))) if g.on_time \
            else await asyncio.to_thread(
            shift_mod.check, model_gb=local.gb if local else 0.0,
            model_loaded=bool(local and local.running),
            when=str(self.settings.get("background_when", "resources")), exclude_pids=pids,
            cpu_limit=float(self.settings.get("background_cpu", shift_mod.CPU_BUSY)),
            gpu_limit=float(self.settings.get("background_gpu", shift_mod.GPU_BUSY)),
            on_battery_ok=bool(self.settings.get("background_on_battery", False)),
            measure_gpu=on_machine, idle=you)
        if not gate.ok:
            self._awake.let_go()
            if "memory" in gate.why:
                await self._shift_unload(gate.why)
            self._shift_state = {"state": "waiting", "why": gate.why, "at": now, "next": g.id}
            return self._shift_state
        self._awake.hold(display=g.screen)          # the screen stays on for a goal that uses it
        if local is not None and not local.running:
            self._shift_loaded.add(local.key)
        if self_item is not None:
            started = await self._self_start(self_item, goal=g, allowed=allowed)
            rid = started["run"]
            goals_mod.update(g.id, conversation=started["conversation"], turns=g.turns + 1,
                             last_turn_at=now, note="")
            self._shift_run, self._shift_goal, self._shift_local = rid, g.id, on_machine
            self._shift_screen, self._shift_on_time, self._shift_started = False, False, time.time()
            self._shift_state = {"state": "working", "why": f"eki, on itself: {self_item.title[:50]} — on {key}",
                                 "at": now, "run": rid, "goal": g.id}
            task = self.runner.tasks.get(rid)
            if task is not None:
                task.add_done_callback(lambda _t: self.shift_wake.set())
            return self._shift_state
        if not new_thread:
            cid = g.conversation
        elif g.fresh and g.repeats:
            title = f"{g.text[:48]} · {time.strftime('%b %-d, %H:%M')}"
            cid = self.store.new_conversation(title)
            self.store.set_conversation(cid, title=title)
        else:
            cid = self.store.new_conversation()
        turn = self.store.add_turn(cid, "user", text, meta={"goal": g.id})
        # not pinned to a model: routed as usual, inside the goal's budget — so a
        # model without tools can still hand it to a harness the goal may use
        payload = {"goal": g.id, "allowed": allowed, "route": g.text}
        if g.screen:
            payload["screen"] = True
        rid = self.runs.create(text, conversation=cid, cwd=g.folder, user_turn=turn,
                               payload=json.dumps(payload))
        goals_mod.update(g.id, conversation=cid, turns=g.turns + 1, last_turn=turn,
                         last_turn_at=now, note="")
        self._shift_run, self._shift_goal, self._shift_local = rid, g.id, on_machine
        self._shift_screen, self._shift_on_time, self._shift_started = g.screen, g.on_time, time.time()
        self._shift_state = {"state": "working", "why": f"{g.text[:60]} — on {key}", "at": now,
                             "run": rid, "goal": g.id}
        await self.runner.submit(rid)
        task = self.runner.tasks.get(rid)
        if task is not None:                        # the next goal starts the moment this ends
            task.add_done_callback(lambda _t: self.shift_wake.set())
        return self._shift_state

    async def _screen_wait(self, you: Optional[float]) -> str:
        """Why a goal that uses the screen can't have a turn now, or ""."""
        if you is None:
            return SCREEN_WAIT + " (and can't tell here whether you are)"
        if you < shift_mod.AWAY_SECONDS:
            return SCREEN_WAIT
        if await asyncio.to_thread(shift_mod.screen_locked):
            return SCREEN_WAIT + " with the Mac unlocked"
        return ""

    # ---- goals, for the board and the command line ---------------------------------------------

    def goals_view(self) -> Dict[str, Any]:
        rows = []
        running = self._shift_goal if self._shift_run in self.runner.running else ""
        for g in goals_mod.all_goals():
            row = g.to_json()
            row["working"] = g.id == running
            row["last"] = ""
            row["last_backend"] = ""
            if g.conversation:
                said = [t for t in self.store.turns(g.conversation) if t["role"] == "assistant"]
                if said:
                    row["last"] = (said[-1].get("content") or "")[-1500:]
                    row["last_backend"] = said[-1].get("backend") or ""
            row["status"] = ("working" if row["working"] else g.state if g.state != "active"
                             else "away" if g.screen and g.note.startswith(SCREEN_WAIT)
                             else self._self_status(g) if g.kind == "self" and g.note
                             else "blocked" if g.note else "scheduled" if g.repeats and g.next_at > time.time()
                             else "queued")
            rows.append(row)
        return {"on": self._shift_on(), "when": str(self.settings.get("background_when", "resources")),
                "shift": self._shift_state, "goals": rows}

    def goals_create(self, text: str, when: Optional[Dict[str, Any]] = None, folder: str = "",
                     spare: bool = False, screen: bool = False, on_time: bool = False,
                     fresh: bool = False) -> Dict[str, Any]:
        g = goals_mod.create(text, when, folder, spare, screen=screen, on_time=on_time, fresh=fresh)
        observe_mod.note("history", what="goal added", goal=g.id, text=text[:200])
        self.shift_wake.set()
        return g.to_json()

    def goals_update(self, gid: str, **fields: Any) -> Dict[str, Any]:
        g = goals_mod.get(gid)
        if fields.get("state") == "paused" and self._shift_goal == g.id:
            self._shift_step_out("you paused its goal")
        if "screen" in fields:
            fields["screen"] = bool(fields["screen"])
            if fields["screen"] != g.screen:
                fields.setdefault("note", "")       # what it waited on may not hold any more
                if not fields["screen"] and self._shift_goal == g.id and self._shift_screen:
                    self._shift_step_out("it may not use the screen any more")
        if fields.get("state") == "active":
            fields.setdefault("note", "")
            fields.setdefault("failures", 0)
            if g.state in ("done", "stuck") and not g.repeats:
                fields.setdefault("next_at", 0)
        g = goals_mod.update(g.id, **fields)
        self.shift_wake.set()
        return g.to_json()

    def goals_run_now(self, gid: str) -> Dict[str, Any]:
        """Its next turn at the first room, whatever its schedule says."""
        g = goals_mod.get(gid)
        return self.goals_update(g.id, state="active", next_at=0)

    def goals_remove(self, gid: str) -> Dict[str, Any]:
        g = goals_mod.get(gid)
        if self._shift_goal == g.id:
            self._shift_step_out("you removed its goal")
        goals_mod.remove(g.id)
        observe_mod.note("history", what="goal removed", goal=g.id)
        return {"removed": g.id, "conversation": g.conversation}

    def goals_report(self, hours: float = 24.0) -> Dict[str, Any]:
        """What the shift did: turns, outcomes, time, and on which models."""
        entries = goals_mod.history(time.time() - hours * 3600)
        turns = [e for e in entries if e.get("state") == "done"]
        by_backend: Dict[str, Dict[str, float]] = {}
        for e in turns:
            b = by_backend.setdefault(e.get("backend") or "?", {"turns": 0, "seconds": 0.0})
            b["turns"] += 1
            b["seconds"] += float(e.get("seconds") or 0)
        local = {b.key for b in self.backends if b.info.cost.tier == 0}
        return {"hours": hours, "turns": len(turns),
                "failed": sum(e.get("state") == "failed" for e in entries),
                "stepped_out": sum(e.get("state") == "stepped out" for e in entries),
                "finished": sum(e.get("outcome") == "done" for e in entries),
                "working_seconds": round(sum(float(e.get("seconds") or 0) for e in entries), 1),
                "by_backend": by_backend,
                "on_subscription": sum(1 for e in turns if (e.get("backend") or "") not in local)}

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


#: the terminal's panels eki draws itself (ClaudeCode.swift); listed with
#: the program's commands even when the program calls them terminal-only.
#: /model is not among them: eki's own picker beside the composer owns the
#: model, for every backend
CLAUDE_PANELS = [
    ("mcp", "MCP servers: status, authenticate, add"),
    ("permissions", "Permission rules of this session"),
    ("usage", "Plan limits and this session's cost"),
    ("context", "What fills the context window"),
    ("rewind", "Put files back as before an answer"),
    ("agents", "Custom agents available here"),
    ("hooks", "Hooks configured here"),
    ("status", "Version, session, account, what the build can do"),
    ("tasks", "Background tasks, and stop one"),
    ("config", "Claude Code's settings as this session sees them"),
    ("memory", "Memory files loaded for this folder"),
    ("skills", "Skills available here, and use one"),
]
CODEX_PANELS = [
    ("mcp", "MCP servers: status, sign in"),
    ("permissions", "Sandbox and approval of this thread"),
    ("usage", "Plan limits and usage"),
    ("context", "What the last request carried"),
    ("rewind", "Roll the thread back a turn"),
    ("hooks", "Hooks configured here"),
    ("status", "Model, thread, account"),
    ("config", "Codex's config as it reads it"),
    ("skills", "Skills available here, and use one"),
    ("plugins", "Installed plugins and marketplaces"),
]
PANEL_COMMANDS = CLAUDE_PANELS


def with_panels(commands: List[Dict[str, Any]],
                panels: Optional[List[Tuple[str, str]]] = None) -> List[Dict[str, Any]]:
    have = {c.get("name") for c in commands}
    out = [c for c in commands if c.get("name") != "model"]     # eki's picker owns the model
    for name, desc in (panels if panels is not None else PANEL_COMMANDS):
        if name not in have:
            out.append({"name": name, "description": desc, "argumentHint": "", "builtin": True})
    return out


def _codex_window(session: Any) -> int:
    try:
        return int((session.usage or {}).get("modelContextWindow") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _codex_window(session: Any) -> int:
    try:
        return int((session.usage or {}).get("modelContextWindow") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


SKILLS_PANEL = {"name": "skills", "description": "eki's skills, and use one",
                "argumentHint": "", "builtin": True}


def _skill_commands(backend: str) -> List[Dict[str, Any]]:
    """eki's skills enabled for a backend, as slash commands."""
    try:
        return [{"name": s["folder"], "description": s["description"], "argumentHint": "",
                 "skill": True} for s in skills_mod.enabled_for(backend)]
    except OSError:
        return []
