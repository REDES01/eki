"""The kinds of provider eki knows how to add, and how to find them.

Each template is what the Add Provider sheet offers: what the user has to
supply (nothing, a key, or a URL), sensible defaults, and — for things that
may already be on this Mac — how to detect them. Detection never installs
or starts anything; it only looks.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from .adapters.claude_code import _find_binary

# tier 0 local and free · 50 a subscription you already pay for · 100 metered
TEMPLATES: List[Dict[str, Any]] = [
    {"id": "claude_code", "title": "Claude Code", "kind": "claude_code",
     "blurb": "Your installed Claude Code, signed in with your own account.",
     "needs": "binary", "binary": "claude", "tier": 50, "note": "subscription",
     "quota_source": "claude",
     "capabilities": {"context_tokens": 200000, "tools": True, "repo": True, "vision": True},
     "options": {"binary": "claude", "timeout_seconds": 900}},
    {"id": "codex", "title": "Codex", "kind": "codex",
     "blurb": "Your installed Codex CLI, signed in with your ChatGPT account.",
     "needs": "binary", "binary": "codex", "tier": 50, "note": "subscription",
     "quota_source": "codex",
     "capabilities": {"context_tokens": 200000, "tools": True, "repo": True},
     "options": {"binary": "codex", "sandbox": "read-only", "timeout_seconds": 900}},
    {"id": "anthropic", "title": "Anthropic API", "kind": "anthropic_api",
     "blurb": "Claude through the API, billed per token to your API key.",
     "needs": "key", "tier": 100, "note": "metered",
     "capabilities": {"context_tokens": 200000, "vision": True},
     "options": {"base_url": "https://api.anthropic.com"}},
    {"id": "openai", "title": "OpenAI API", "kind": "openai_compat",
     "blurb": "OpenAI models through the API, billed per token.",
     "needs": "key", "tier": 100, "note": "metered",
     "capabilities": {"context_tokens": 128000, "vision": True},
     "options": {"base_url": "https://api.openai.com/v1"}},
    {"id": "xai", "title": "xAI (Grok)", "kind": "openai_compat",
     "blurb": "Grok through xAI's OpenAI-compatible API.",
     "needs": "key", "tier": 100, "note": "metered",
     "capabilities": {"context_tokens": 128000},
     "options": {"base_url": "https://api.x.ai/v1"}},
    {"id": "openrouter", "title": "OpenRouter", "kind": "openai_compat",
     "blurb": "Hundreds of models behind one key.",
     "needs": "key", "tier": 100, "note": "metered",
     "capabilities": {"context_tokens": 128000},
     "options": {"base_url": "https://openrouter.ai/api/v1"}},
    {"id": "mlx", "title": "MLX server", "kind": "mlx",
     "blurb": "A local mlx_lm server — Apple Silicon, free, private.",
     "needs": "url", "port": 8080, "tier": 0, "note": "local, free",
     "capabilities": {"context_tokens": 32000},
     "options": {"base_url": "http://127.0.0.1:8080"}},
    {"id": "ollama", "title": "Ollama", "kind": "openai_compat",
     "blurb": "Models you run with Ollama.",
     "needs": "url", "port": 11434, "tier": 0, "note": "local, free",
     "capabilities": {"context_tokens": 32000},
     "options": {"base_url": "http://127.0.0.1:11434/v1"}},
    {"id": "lmstudio", "title": "LM Studio", "kind": "openai_compat",
     "blurb": "Models served by LM Studio or llmster.",
     "needs": "url", "port": 1234, "tier": 0, "note": "local, free",
     "capabilities": {"context_tokens": 32000},
     "options": {"base_url": "http://127.0.0.1:1234/v1"}},
    {"id": "comfyui", "title": "ComfyUI", "kind": "comfyui",
     "blurb": "Local image generation.",
     "needs": "url", "port": 8188, "tier": 0, "note": "local, free",
     "capabilities": {"context_tokens": 512, "text": False, "images_out": True},
     "options": {"base_url": "http://127.0.0.1:8188", "output_dir": "~/flux/output"}},
    {"id": "custom", "title": "Any OpenAI-compatible URL", "kind": "openai_compat",
     "blurb": "vLLM, llama.cpp's server, a gateway — anything that speaks /v1.",
     "needs": "url", "tier": 0, "note": "",
     "capabilities": {"context_tokens": 32000},
     "options": {"base_url": "http://127.0.0.1:8000/v1"}},
]


def template(template_id: str) -> Optional[Dict[str, Any]]:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)


async def _answers(url: str, path: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get(url.rstrip("/") + path)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


async def discover(configured: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Templates that are present on this Mac and not yet added."""
    have_kinds = {p["kind"] for p in configured}
    have_urls = {str((p.get("options") or {}).get("base_url", "")).rstrip("/")
                 for p in configured}

    async def check(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if t["needs"] == "binary":
            path = _find_binary(t["binary"])
            if path and t["kind"] not in have_kinds:
                return {**t, "found": path}
            return None
        if t.get("port"):
            base = t["options"]["base_url"].rstrip("/")
            if base in have_urls or base.removesuffix("/v1") in have_urls:
                return None
            probe = "/system_stats" if t["kind"] == "comfyui" else (
                "/models" if base.endswith("/v1") else "/v1/models")
            if await _answers(base, probe):
                return {**t, "found": base}
        return None

    found = await asyncio.gather(*(check(t) for t in TEMPLATES))
    return [f for f in found if f]
