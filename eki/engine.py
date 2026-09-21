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
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from . import config as config_mod
from . import policy as policy_mod
from .adapters import base as adapters
from .adapters.base import Backend, BackendError, Health, Message
from . import deploy as deploy_mod
from . import classify
from . import secrets
from . import settings as settings_mod
from . import titles
from .models import LocalModel, ModelManager
from .providers import Provider, ProviderStore, seed_from_config
from .quota import QuotaBoard, QuotaProvider
from .quota.claude import ClaudeStatusLine
from .quota.codex import CodexAppServer
from .router import Need, Router
from .runs import Runner, RunStore
from .store import Store

HEALTH_TTL = 20.0


class Engine:
    def __init__(self, cfg, owner: bool = False):
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
                    idle_minutes=float(r.get("idle_minutes", 30) or 0)))
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
        self.router = Router(self.backends, self.quota, policy=self.policy,
                             is_up=self._is_up,
                             reserved={self.settings["router_model"]}
                             if self.settings["router_model"] else set())
        self._health = {}

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
        model = self.models.for_backend(key)
        if model is None:
            return None
        if model.running:
            return True
        # stopped, but eki can bring it up: still a candidate, started on
        # demand — otherwise a free local model loses every request to a paid
        # one just because it was idle-unloaded
        return None if self.models.can_start(model.key) else False

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

        cid = run["conversation_id"]
        label = await self._label(run)
        need = Need(repo=bool(run["cwd"]), tools=bool(run["cwd"]),
                    images_out=bool(run["images"]) or label.task == "image",
                    backend=run["requested"] or None,
                    task=label.task, difficulty=label.difficulty)
        choice = self.router.choose(need)
        if choice.backend is None:
            why = choice.reason
            if choice.rejected:
                why += " — " + "; ".join(choice.rejected)
            if cid:
                self.store.add_turn(cid, "assistant", f"[{why}]", "", choice.reason,
                                    meta={"run": run["id"], "failed": True})
            raise BackendError(why)

        reason = choice.reason
        model = self.models.for_backend(choice.backend.key)
        meta_label = label.to_json()
        if model is not None and not model.running:
            reason += f"; starting {model.label}"
        yield {"backend": choice.backend.key, "reason": reason}
        if model is not None:
            if not model.running:
                message = await self.models.start(model.key)
                if not model.running:
                    raise BackendError(message)
                if "make room" in message:
                    yield {"backend": choice.backend.key, "reason": reason + "; " +
                           message[message.index("(") + 1:-1]}
            self.models.hold(model.key)

        # A fresh instance per run. Adapters keep per-call state — the
        # session id to resume, the token usage — on themselves, and two runs
        # on one shared instance would hand each other their sessions.
        backend = adapters.build(choice.backend.info,
                                 self.options.get(choice.backend.key, {}))

        # Everything up to and including this run's question — and nothing a
        # parallel run in the same conversation added after it.
        history = [Message(t["role"], t["content"]) for t in self.store.turns(cid)
                   if t["id"] <= run["user_turn"]] if cid else []
        if not history:
            history = [Message("user", run["prompt"])]

        kw: Dict[str, Any] = {}
        if run["cwd"]:
            kw["cwd"] = run["cwd"]
        resumed = self.store.session(cid, backend.key) if cid else None
        if resumed:
            kw["resume"] = resumed

        meta: Dict[str, Any] = {"run": run["id"], "label": meta_label}
        if run["cwd"]:
            meta["cwd"] = run["cwd"]
        parts: List[str] = []
        try:
            async for chunk in backend.stream(history, **kw):
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

    def _titler(self) -> Optional[Backend]:
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
            if backend is None:
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

    async def _label(self, run: Dict[str, Any]) -> classify.Label:
        """What kind of request this is. Never allowed to fail a run."""
        try:
            label = await self.classifier.label(run["prompt"], has_folder=bool(run["cwd"]))
        except Exception:                           # noqa: BLE001
            label = classify.rules(run["prompt"], bool(run["cwd"]))
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
            runtime={"port": port, "gb": room["need_gb"], "idle_minutes": 30,
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
        for b in self.backends:
            try:
                await b.close()
            except Exception:                       # noqa: BLE001
                pass


def open_engine(path: str = "") -> Engine:
    return Engine(config_mod.load(path or str(config_mod.default_path())))
