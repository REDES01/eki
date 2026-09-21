# SPDX-License-Identifier: Apache-2.0
"""How much context a local model gets.

A number typed into a catalog is wrong the day the model changes, and it
was wrong here: Qwen3.8-27B was listed at 32k while it takes 262k, so
Codex — whose own prompt is ~15k — thought it was at the wall after a
handful of commands and compacted mid-task. The model's own config says
what it supports; memory says what fits beside the weights; speed says
where to stop. eki works the window out from those, every time it loads
its providers, and shows the reasoning in the Models pane.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

#: windows are picked from these, never in between: Codex and Claude Code
#: compact at a margin below the number they're given, and a round figure
#: is one a person can reason about
STEPS = (8192, 16384, 32768, 49152, 65536, 98304, 131072)

#: past this, every turn of a 4-bit 20–30B model on a Mac re-reads a
#: novel: a bigger window would fit, and would not be worth waiting for
SPEED_CAP = 131072

#: the share of the room beside the weights the cache may grow into — the
#: rest stays for other models, the app, and whatever the user has open
KV_SHARE = 0.75

#: below this a harness spends most of its window on itself: Codex's
#: prompt and tool definitions are ~15k before the first message
HARNESS_MIN = 49152


@dataclass
class Window:
    tokens: int                 # what eki gives the model
    native: int                 # what the model itself supports
    kv_gb: float                # cache at `tokens`, fp16
    per_token_kb: float
    limited_by: str             # native | memory | speed | pinned
    fits: bool = True           # the cache at full fits in the room it was sized against

    def describe(self) -> str:
        parts = [f"{_k(self.tokens)} context"]
        if self.limited_by == "pinned":
            parts.append("set by you")
        if self.limited_by == "native" or self.tokens == self.native:
            parts.append("the model's maximum")
        else:
            parts.append(f"native {_k(self.native)}")
        parts.append(f"~{self.kv_gb:g} GB of cache at full")
        if self.limited_by == "memory":
            parts.append("limited by this Mac's memory")
        elif self.limited_by == "pinned" and not self.fits:
            parts.append("more than fits beside it now")
        return " · ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "summary": self.describe()}


def _k(n: int) -> str:
    return f"{n // 1024}k"


def _text_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("text_config") or config.get("llm_config") or config


def kv_gb(config: Dict[str, Any], tokens: int) -> float:
    """fp16 KV cache for `tokens` of context.

    Hybrid models (Qwen3.5/3.8, Gemma 3n…) list `layer_types`; only the
    full-attention layers keep a cache that grows with the context, the
    linear/sliding ones hold a fixed state, so those are counted alone."""
    c = _text_config(config)
    layers = c.get("num_hidden_layers") or 0
    types = c.get("layer_types")
    if isinstance(types, list) and types:
        layers = sum(1 for t in types if t == "full_attention")
    heads = c.get("num_attention_heads") or 0
    kv_heads = c.get("num_key_value_heads") or heads
    head_dim = c.get("head_dim") or ((c.get("hidden_size") or 0) // heads if heads else 0)
    return round(2 * layers * kv_heads * head_dim * 2 * tokens / 1024**3, 2)


def read_config(repo: str) -> Optional[Dict[str, Any]]:
    """The model's config.json from the Hugging Face cache, if downloaded."""
    from . import deploy                            # deploy sizes with this module
    snap = deploy.snapshot_dir(repo)
    if snap is None:
        return None
    try:
        return json.loads((snap / "config.json").read_text())
    except (OSError, ValueError):
        return None


def native(config: Dict[str, Any]) -> int:
    return int(_text_config(config).get("max_position_embeddings") or 0)


def size(config: Dict[str, Any], room_gb: float, cap: int = SPEED_CAP) -> Optional[Window]:
    """The largest step that the model supports, that fits in `room_gb`
    of cache, and that stays under the speed cap. None when the config
    says nothing — a stored number is better than a guess."""
    limit = native(config)
    if not limit:
        return None
    per_token = kv_gb(config, 1 << 20) / (1 << 20)          # GB per token, rounding kept small
    budget = max(0.0, room_gb) * KV_SHARE
    chosen, why = STEPS[0], ""
    for step in STEPS:
        if step > limit:
            why = "native"
            break
        if step > cap:
            why = "speed"
            break
        if step * per_token > budget:
            why = "memory"
            break
        chosen = step
    if not why:                                     # ran out of steps
        why = "speed" if limit > chosen else "native"
    return Window(tokens=chosen, native=limit, kv_gb=round(chosen * per_token, 1),
                  per_token_kb=round(per_token * 1024 * 1024, 1), limited_by=why)


#: what a person may pin a window to: the auto steps, and past the speed
#: cap for those who'd rather wait than compact
CHOICES = STEPS + (196608, 262144)


def pinned(config: Dict[str, Any], tokens: int, room_gb: float) -> Optional[Window]:
    """A window the user chose: honoured up to the model's maximum, and
    said plainly when the cache at full is more than what's free now."""
    limit = native(config)
    if not limit:
        return None
    tokens = max(STEPS[0], min(int(tokens), limit))
    per_token = kv_gb(config, 1 << 20) / (1 << 20)
    cache = tokens * per_token
    return Window(tokens=tokens, native=limit, kv_gb=round(cache, 1),
                  per_token_kb=round(per_token * 1024 * 1024, 1), limited_by="pinned",
                  fits=cache <= max(0.0, room_gb) * KV_SHARE)


def harness_ready(tokens: int) -> bool:
    """Can a coding harness work inside this window?"""
    return tokens >= HARNESS_MIN
