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

from . import context
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

async def search(query: str = "", limit: int = 30) -> List[Dict[str, Any]]:
    params = {"author": "mlx-community", "sort": "downloads", "limit": str(limit * 2)}
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
        out.append({"repo": m["id"], "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0), "task": task})
    return out[:limit]


_text_config = context._text_config
kv_gb = context.kv_gb                               # the estimate lives with the window logic


async def details(repo: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        info = (await c.get(f"{HF}/api/models/{repo}", params={"blobs": "true"})).json()
        try:
            config = (await c.get(f"{HF}/{repo}/resolve/main/config.json")).json()
        except (httpx.HTTPError, ValueError):
            config = {}
        try:
            gen = (await c.get(f"{HF}/{repo}/resolve/main/generation_config.json")).json()
        except (httpx.HTTPError, ValueError):
            gen = {}
    files = info.get("siblings") or []
    weights = sum(f.get("size", 0) for f in files if f["rfilename"].endswith(".safetensors"))
    total = sum(f.get("size", 0) for f in files)
    text = _text_config(config)
    return {
        "repo": repo,
        "weights_gb": round(weights / 1024**3, 2),
        "download_gb": round(total / 1024**3, 2),
        "context": int(text.get("max_position_embeddings") or 0),
        "vision": bool(config.get("vision_config")),
        "license": (info.get("cardData") or {}).get("license", ""),
        "gated": bool(info.get("gated")),
        "config": config,
        "sampling": sampling(gen),
    }


def fit(d: Dict[str, Any], free_gb: float) -> Dict[str, Any]:
    """Will it run beside what's already loaded, and with how much context?

    The window is sized the way a model already here gets it (see
    eki/context.py): from its config, against the memory left beside its
    weights, under the speed cap. Need = weights + that cache + overhead.
    A repo without a config gets the smallest window and an honest guess.
    """
    config = d.get("config") or {}
    room = free_gb - d["weights_gb"] - OVERHEAD_GB
    window = context.size(config, room)
    tokens = window.tokens if window else min(context.STEPS[0], d.get("context") or context.STEPS[0])
    need = round(d["weights_gb"] + kv_gb(config, tokens) + OVERHEAD_GB, 1)
    return {"need_gb": need, "free_gb": free_gb, "context": tokens,
            "window": window.as_dict() if window else None,
            "fits": need <= free_gb,
            "verdict": ("fits" if need <= free_gb * 0.85 else
                        "tight" if need <= free_gb else "too big")}


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
                  max_tokens: int = 8192, thinking: Optional[bool] = None) -> Dict[str, str]:
    """`thinking=False` turns a reasoning model's thinking off — eki's
    default, since a local model is picked for quick answers."""
    s = settings()
    folder = HOME / "models" / key
    folder.mkdir(parents=True, exist_ok=True)
    flags = [f"--max-tokens {max_tokens}"]
    if thinking is not None:
        flags.append("--chat-template-args " + shlex.quote(
            json.dumps({"enable_thinking": thinking})))
    if "temperature" in samp:
        flags.append(f"--temp {samp['temperature']}")
    if "top_p" in samp:
        flags.append(f"--top-p {samp['top_p']}")
    if "top_k" in samp:
        flags.append(f"--top-k {samp['top_k']}")
    start = folder / "start.sh"
    start.write_text(f"""#!/bin/bash
# Written by eki. Serves {repo} on 127.0.0.1:{port}.
export HF_HOME={shlex.quote(os.path.expanduser(s['hf_home']))}
export HF_HUB_DISABLE_XET=1
export HF_HUB_OFFLINE=1
exec {shlex.quote(os.path.expanduser(s['mlx_python']))} -m mlx_lm server \\
  --model {shlex.quote(repo)} --host 127.0.0.1 --port {port} \\
  {' '.join(flags)} >> {shlex.quote(str(folder / 'server.log'))} 2>&1
""")
    stop = folder / "stop.sh"
    stop.write_text(f"""#!/bin/bash
# Written by eki. Stops whatever listens on {port}.
for p in $(lsof -nP -iTCP:{port} -sTCP:LISTEN -t 2>/dev/null); do kill "$p"; done
""")
    start.chmod(0o755)
    stop.chmod(0o755)
    return {"start": str(start), "stop": str(stop)}


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
    """Fetch the weights with the MLX environment's huggingface_hub,
    reporting progress by watching the cache grow."""
    py = os.path.expanduser(settings()["mlx_python"])
    if not Path(py).exists():
        raise RuntimeError(f"no MLX Python at {py} — set one in Settings → Models")
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


async def smoke(port: int, samp: Dict[str, Any]) -> Dict[str, Any]:
    """One short answer: proves it loads and says how fast it is."""
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
