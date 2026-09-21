# SPDX-License-Identifier: Apache-2.0
"""What did the person paste?

One field takes anything — a Hugging Face link or repo id, a GGUF file
on disk, a folder of MLX weights, the address of a server already
running — and eki says what it is, what it would cost to run here, and
what happens next: a download and a start script, or a provider that
points at what's already there. The person never has to know the words
for any of it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from . import context, deploy, gguf, profile, public_scores

_HF = re.compile(r"^(?:https?://)?(?:huggingface\.co|hf\.co)/(?P<repo>[^/\s]+/[^/\s?#]+)", re.I)
_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_URL = re.compile(r"^https?://", re.I)


async def identify(text: str, free_gb: float, ceiling_gb: float) -> Dict[str, Any]:
    text = text.strip().strip("'\"")
    if not text:
        return {"kind": "empty"}
    m = _HF.match(text)
    if m:
        return await _hub(m.group("repo").rstrip("/"), free_gb, ceiling_gb)
    if _URL.match(text):
        return await _server(text)
    path = Path(os.path.expanduser(text))
    if path.exists():
        return _local(path, ceiling_gb)
    if _REPO.match(text):
        return await _hub(text, free_gb, ceiling_gb)
    return {"kind": "unknown", "text": text,
            "message": "Paste a Hugging Face link or org/name, a .gguf file, a folder of MLX weights, "
                       "or a server's address."}


async def _hub(repo: str, free_gb: float, ceiling_gb: float) -> Dict[str, Any]:
    # a link deep into a repo ("…/tree/main", "…/blob/main/x.gguf") still names the repo
    repo = "/".join(repo.split("/")[:2])
    try:
        d = await deploy.details(repo)
    except Exception as e:                          # noqa: BLE001
        return {"kind": "unknown", "text": repo, "message": f"Hugging Face couldn't find {repo}: {e}"}
    if not d.get("weights_gb"):
        return {"kind": "unsupported", "text": repo,
                "message": f"{repo} has no weights eki can serve (safetensors for MLX, or GGUF)."}
    room = deploy.fit(d, free_gb, ceiling_gb)
    prof = profile.from_hub(repo, d)
    d.pop("config", None)
    d.pop("chosen", None)
    return {"kind": "hub", "repo": repo, "format": d["format"], "next": "deploy",
            "fit": {**d, **room, "profile": prof.as_dict()}}


def local_details(path: Path) -> Tuple[Dict[str, Any], profile.Profile]:
    """A file or folder already on disk, described the way deploy.details
    describes a repo — so setup runs the same way, minus the download."""
    if path.is_file() and path.suffix == ".gguf":
        h = gguf.read_header(path)                  # ValueError when it isn't one
        config = gguf.config_from_header(h)
        weights = round(path.stat().st_size / 1024**3, 2)
        template = str(h.get("tokenizer.chat_template") or "")
        quant = gguf.quant_of(path.name)
        prof = profile.from_files(path.name, config, None)
        prof.format, prof.quant = "gguf", quant
        prof.quant_bits = int(round(gguf.bits_of(quant))) if quant else 0
        prof.weights_gb = weights
        prof.tools = ("tools" in template or "tool_call" in template) if template else None
        prof.thinking_switch = "enable_thinking" in template
        prof.base = public_scores.base_name(str(h.get("general.basename") or path.stem))
        if not prof.params_b and h.get("general.size_label"):
            prof.params_b = profile.params_from_name(str(h["general.size_label"]) + "B")
        d = {"repo": str(path), "format": "gguf", "weights_gb": weights, "download_gb": 0.0,
             "context": config["max_position_embeddings"], "config": config, "sampling": {},
             "quant": quant, "license": "", "gated": False, "vision": False,
             "base_id": str(h.get("general.basename") or ""),
             "gguf": {"tools": prof.tools, "thinking_switch": prof.thinking_switch}}
        return d, prof
    if path.is_dir() and (path / "config.json").exists():
        weights = round(sum(f.stat().st_size for f in path.glob("*.safetensors")) / 1024**3, 2)
        if not weights:
            raise ValueError("a folder with a config.json but no safetensors")
        try:
            config = json.loads((path / "config.json").read_text())
        except (OSError, ValueError):
            config = {}
        prof = profile.from_files(path.name, config, path)
        prof.weights_gb = weights
        d = {"repo": str(path), "format": "mlx", "weights_gb": weights, "download_gb": 0.0,
             "context": context.native(config), "config": config, "sampling": prof.sampling,
             "license": "", "gated": False, "vision": prof.vision_weights, "base_id": ""}
        return d, prof
    raise ValueError("eki serves a .gguf file or a folder of MLX weights (config.json + safetensors)")


def _local(path: Path, ceiling_gb: float) -> Dict[str, Any]:
    try:
        d, prof = local_details(path)
    except (OSError, ValueError) as e:
        return {"kind": "unsupported", "text": str(path), "message": str(e)}
    room = deploy.fit(d, ceiling_gb, ceiling_gb)
    d.pop("config")
    return {"kind": "gguf_file" if d["format"] == "gguf" else "mlx_dir", "path": str(path),
            "format": d["format"], "next": "deploy", "fit": {**d, **room, "profile": prof.as_dict()}}


async def _server(url: str) -> Dict[str, Any]:
    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=5) as c:
        for probe, template in (("/system_stats", "comfyui"), ("/v1/models", "custom"), ("/models", "custom")):
            try:
                r = await c.get(base + probe)
            except httpx.HTTPError:
                continue
            if r.status_code == 200:
                models = []
                if template == "custom":
                    try:
                        models = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
                    except ValueError:
                        pass
                    base_url = base if base.endswith("/v1") or probe == "/models" else base + "/v1"
                else:
                    base_url = base
                return {"kind": "server", "url": base_url, "template": template, "models": models[:20],
                        "next": "provider",
                        "message": ("A ComfyUI" if template == "comfyui" else "An OpenAI-compatible server")
                                   + f" is answering at {base_url}."}
    return {"kind": "unknown", "text": url, "message": f"Nothing eki recognises is answering at {base}."}
