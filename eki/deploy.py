# SPDX-License-Identifier: Apache-2.0
"""Find an MLX model on Hugging Face, see whether it fits, and set it up.

Deploying is a run like any other: it survives the window closing, it shows
up in Activity, and its log is the progress. Setting a model up means:
download the weights, write a start and stop script, add a provider whose
runtime eki manages, start it once, and measure it with a one-line smoke
test — so the first real question doesn't find out the model is broken.

Nothing here runs a model inside the engine. The server is `mlx_lm server`
from an MLX Python environment of the user's choosing (settings.json), a
separate process that outlives the engine.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import socket
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from . import context, gguf
from . import settings as settings_mod

HF = "https://huggingface.co"
HOME = Path("~/.eki").expanduser()
KEEP = ["*.json", "*.safetensors", "*.jinja", "*.txt", "*.model", "*.tiktoken", "*.py"]
#: loader and runtime overhead on top of the weights, measured loosely
OVERHEAD_GB = 1.2
FIRST_PORT = 8090
NOT_CHAT = {"automatic-speech-recognition", "text-to-speech", "text-to-audio",
            "audio-to-audio", "feature-extraction", "sentence-similarity",
            "text-to-image", "image-to-image", "image-classification", "fill-mask"}           # 8080/8081 are commonly taken by hand-run servers


# ---- settings --------------------------------------------------------------

def settings() -> Dict[str, Any]:
    return settings_mod.load()


def save_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    return settings_mod.save(data)


def _env() -> Dict[str, str]:
    s = settings()
    env = dict(os.environ)
    env["HF_HOME"] = os.path.expanduser(s["hf_home"])
    env["HF_HUB_DISABLE_XET"] = "1"         # Xet stalls on some networks; plain HTTP doesn't
    return env


# ---- catalog ---------------------------------------------------------------

#: GGUF publishers whose builds are the originals quantised, not edited
GGUF_AUTHORS = ("unsloth", "bartowski", "lmstudio-community", "ggml-org", "Qwen", "google",
                "mistralai", "microsoft", "openai", "zai-org", "MaziyarPanahi", "TheBloke")
_EDITED = re.compile(r"uncensored|abliterated|heretic|-mtp|nvfp4|imatrix-nvfp4", re.I)


async def search(query: str = "", limit: int = 30, min_b: float = 0.0,
                 max_b: float = 0.0, fmt: str = "mlx") -> List[Dict[str, Any]]:
    """Builds by popularity — mlx-community's for MLX, the known GGUF
    publishers' for GGUF — narrowed by a parameter range (billions; 0 =
    no bound) the way Hugging Face's own filter does."""
    from .profile import params_from_name
    bounded = bool(min_b or max_b)
    params: Dict[str, str] = {"sort": "downloads", "limit": str(limit * (6 if bounded else 3))}
    if fmt == "gguf":
        params["filter"] = "gguf"
    else:
        params["author"] = "mlx-community"
    if query:
        params["search"] = query
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{HF}/api/models", params=params)
    r.raise_for_status()
    out = []
    for m in r.json():
        task = m.get("pipeline_tag") or ""
        # eki serves chat models; speech, embedding and image repos don't belong here
        if task in NOT_CHAT or re.search(r"whisper|parakeet|-asr|tts|embedding|bge-|e5-", m["id"], re.I):
            continue
        if fmt == "gguf" and (m["id"].split("/")[0] not in GGUF_AUTHORS or _EDITED.search(m["id"])):
            continue
        size = params_from_name(m["id"])
        if bounded and (not size or (min_b and size < min_b) or (max_b and size > max_b)):
            continue
        out.append({"repo": m["id"], "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0), "task": task, "params_b": size, "format": fmt})
    return out[:limit]


_text_config = context._text_config
kv_gb = context.kv_gb                               # the estimate lives with the window logic


async def _json(c: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    try:
        r = await c.get(url)
        return r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}


def _base_id(info: Dict[str, Any]) -> str:
    base = (info.get("cardData") or {}).get("base_model")
    if isinstance(base, list):
        base = base[0] if base else ""
    return str(base or "")


async def details(repo: str, quant: str = "") -> Dict[str, Any]:
    """What a repo is before it's downloaded: its format, weights, context,
    config (the base model's, for a GGUF), sampling, and for a GGUF the
    quantisations on offer with `quant` (or the usual default) chosen."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        info = (await c.get(f"{HF}/api/models/{repo}", params={"blobs": "true"})).json()
        siblings = info.get("siblings") or []
        is_gguf = any(f.get("rfilename", "").endswith(".gguf") for f in siblings)
        config = await _json(c, f"{HF}/{repo}/resolve/main/config.json")
        gen = await _json(c, f"{HF}/{repo}/resolve/main/generation_config.json")
        base_id = _base_id(info)
        if is_gguf and not config and base_id:
            # the cache's shape lives in the base model's config
            config = await _json(c, f"{HF}/{base_id}/resolve/main/config.json")
            gen = gen or await _json(c, f"{HF}/{base_id}/resolve/main/generation_config.json")
    total = sum(f.get("size", 0) for f in siblings)
    text = _text_config(config)
    task = str(info.get("pipeline_tag") or "")
    tags = [str(t) for t in (info.get("tags") or [])]
    # a diffusion model's weights in GGUF are for ComfyUI, not a language
    # model server: the file is real, the words come out as noise
    picture = task in ("text-to-image", "image-to-image", "text-to-video", "image-to-video") or \
        any(t in ("comfyui-gguf", "image-generation", "diffusion-single-file") for t in tags)
    out: Dict[str, Any] = {
        "repo": repo, "format": "gguf" if is_gguf else "mlx", "base_id": base_id,
        "task": task, "picture": picture,
        "context": int(text.get("max_position_embeddings") or 0),
        "vision": bool(config.get("vision_config")),
        "license": (info.get("cardData") or {}).get("license", ""),
        "gated": bool(info.get("gated")),
        "config": config,
        "sampling": sampling(gen),
    }
    if is_gguf:
        meta = info.get("gguf") or {}
        files = gguf.files_of(siblings)
        chosen = next((f for f in files if f["quant"] == quant.upper()), None) if quant else None
        chosen = chosen or gguf.default_quant(files)
        out.update({
            "files": [{k: v for k, v in f.items() if k != "sha256s"} for f in files],
            "quant": chosen["quant"] if chosen else "",
            "chosen": chosen,
            "weights_gb": chosen["gb"] if chosen else 0.0,
            "download_gb": chosen["gb"] if chosen else 0.0,
            "context": out["context"] or int(meta.get("context_length") or 0),
            "gguf": {"architecture": meta.get("architecture", ""), "params": meta.get("total", 0),
                     "tools": ("tools" in (meta.get("chat_template") or "")
                               or "tool_call" in (meta.get("chat_template") or "")),
                     "thinking_switch": "enable_thinking" in (meta.get("chat_template") or "")},
        })
    else:
        weights = sum(f.get("size", 0) for f in siblings if f["rfilename"].endswith(".safetensors"))
        out.update({"weights_gb": round(weights / 1024**3, 2), "download_gb": round(total / 1024**3, 2)})
    return out


def fit(d: Dict[str, Any], free_gb: float, ceiling_gb: float) -> Dict[str, Any]:
    """Can this Mac run it, with how much context, and is there room now?

    The window is sized against the most a model can ever get here — the
    memory ceiling less its weights — the same way an installed model is
    (see eki/context.py); what happens to be loaded at the moment only
    decides whether it can start right away. A person who wants a smaller
    window can pin one after. Need = weights + that cache + overhead.
    """
    config = d.get("config") or {}
    room = ceiling_gb - d["weights_gb"] - OVERHEAD_GB
    window = context.size(config, room)
    tokens = window.tokens if window else min(context.STEPS[0], d.get("context") or context.STEPS[0])
    need = round(d["weights_gb"] + kv_gb(config, tokens) + OVERHEAD_GB, 1)
    return {"need_gb": need, "free_gb": free_gb, "ceiling_gb": ceiling_gb, "context": tokens,
            "window": window.as_dict() if window else None,
            "fits": need <= ceiling_gb,             # on this Mac at all
            "fits_now": need <= free_gb,            # beside what's loaded this minute
            "verdict": ("fits" if need <= ceiling_gb * 0.85 else
                        "tight" if need <= ceiling_gb else "too big")}


def sampling(gen: Dict[str, Any]) -> Dict[str, Any]:
    """The model author's recommended sampling, when they published one."""
    out = {}
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k")):
        if gen.get(src) is not None:
            out[dst] = gen[src]
    return out


# ---- set up ----------------------------------------------------------------

def slug(repo: str) -> str:
    name = repo.split("/")[-1].lower()
    name = re.sub(r"-(mlx-)?\d+bit$|-mlx$", "", name)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:32] or "model"


def free_port(taken: List[int]) -> int:
    port = FIRST_PORT
    while port in taken or _listening(port):
        port += 1
    return port


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def snapshot_dir(repo: str) -> Optional[Path]:
    root = Path(os.path.expanduser(settings()["hf_home"])) / "hub" / (
        "models--" + repo.replace("/", "--")) / "snapshots"
    snaps = sorted(root.glob("*"), key=lambda p: p.stat().st_mtime) if root.exists() else []
    return snaps[-1] if snaps else None


def has_thinking_switch(repo: str) -> bool:
    """Does the chat template take `enable_thinking`? (Qwen3-family and kin.)"""
    snap = snapshot_dir(repo)
    if snap is None:
        return False
    for name in ("chat_template.jinja", "tokenizer_config.json"):
        try:
            if "enable_thinking" in (snap / name).read_text(errors="replace"):
                return True
        except OSError:
            pass
    return False


def write_scripts(key: str, repo: str, port: int, samp: Dict[str, Any],
                  max_tokens: int = 8192, thinking: Optional[bool] = None,
                  engine_name: str = "mlx", context: int = 0) -> Dict[str, str]:
    """start.sh / stop.sh for a model, through its engine (eki/engines).
    `thinking=False` turns a reasoning model's thinking off — eki's
    default, since a local model is picked for quick answers."""
    from . import engines
    from .engines.base import ServeSpec
    spec = ServeSpec(key=key, model=repo, port=port, context=context, max_tokens=max_tokens,
                     sampling=samp, thinking=thinking)
    return engines.get(engine_name).write_scripts(spec)


def _cache_bytes(repo: str) -> int:
    root = Path(os.path.expanduser(settings()["hf_home"])) / "hub" / (
        "models--" + repo.replace("/", "--"))
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


async def download(repo: str, expected_bytes: int) -> AsyncIterator[str]:
    """Fetch MLX weights with the mlx engine's huggingface_hub, reporting
    progress by watching the cache grow."""
    from . import engines
    py = engines.get("mlx").python()                # type: ignore[attr-defined]
    if not py or not Path(py).exists():
        raise RuntimeError("mlx_lm isn't installed yet")
    code = ("import sys; from huggingface_hub import snapshot_download; "
            "snapshot_download(sys.argv[1], allow_patterns=sys.argv[2].split(','))")
    proc = await asyncio.create_subprocess_exec(
        py, "-c", code, repo, ",".join(KEEP), env=_env(),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    last_pct, last_at = -1, 0.0
    try:
        while True:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
                break
            except asyncio.TimeoutError:
                pass
            have = _cache_bytes(repo)
            pct = int(100 * have / expected_bytes) if expected_bytes else 0
            if pct >= last_pct + 5 or time.time() - last_at > 30:
                last_pct, last_at = pct, time.time()
                yield f"  {have / 1024**3:.1f} of {expected_bytes / 1024**3:.1f} GB ({min(pct, 100)}%)\n"
    except asyncio.CancelledError:
        proc.kill()
        raise
    if proc.returncode != 0:
        err = (await proc.stderr.read()).decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("download failed: " + (err[-1] if err else f"exit {proc.returncode}"))


async def download_gguf(repo: str, chosen: Dict[str, Any]) -> AsyncIterator[str]:
    """Fetch a quantisation's file(s) straight from the Hub, each checked
    against the sha256 the Hub lists for it."""
    from .engines.base import fetch
    for i, name in enumerate(chosen["files"], 1):
        dest = gguf.local_path(repo, name)
        if dest.exists():
            yield f"  {name} is already here.\n"
            continue
        if len(chosen["files"]) > 1:
            yield f"  part {i} of {len(chosen['files'])}: {name}\n"
        async for line in fetch(f"{HF}/{repo}/resolve/main/{name}", dest,
                                (chosen.get("sha256s") or {}).get(name, ""), 0):
            yield line


async def ready(port: int, timeout: float = 240.0) -> None:
    """Wait until the server answers for real: llama-server opens its port
    while the weights are still loading and says 503 until they're in."""
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=5) as c:
        while time.time() < deadline:
            try:
                r = await c.get(f"http://127.0.0.1:{port}/v1/models")
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("the server didn't finish loading in time")


async def smoke(port: int, samp: Dict[str, Any]) -> Dict[str, Any]:
    """One short answer: proves it loads and says how fast it is."""
    await ready(port)
    body = {"messages": [{"role": "user", "content": "Reply with one short sentence about the sea."}],
            "max_tokens": 64, "stream": False, **{k: v for k, v in samp.items() if k != "top_k"}}
    t0 = time.time()
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body)
    r.raise_for_status()
    data = r.json()
    elapsed = time.time() - t0
    out_tokens = (data.get("usage") or {}).get("completion_tokens") or 0
    message = data["choices"][0]["message"]
    text = message.get("content") or message.get("reasoning") or ""
    return {"reply": text.strip()[:200], "seconds": round(elapsed, 1),
            "tok_s": round(out_tokens / elapsed, 1) if elapsed and out_tokens else None}
