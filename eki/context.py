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

from . import deploy

#: windows are picked from these, never in between: Codex and Claude Code
#: compact at a margin below the number they're given, and a round figure
#: is one a person can reason about
STEPS = (8192, 16384, 32768, 49152, 65536, 98304, 131072)

#: past this, every turn of a 4-bit 20–30B model on a Mac re-reads a
#: novel: a bigger window would fit, and would not be worth waiting for
SPEED_CAP = 131072

#: the share of free memory the cache may grow into — the rest stays for
#: other models, the app, and whatever the user has open
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
    limited_by: str             # native | memory | speed

    def describe(self) -> str:
        parts = [f"{_k(self.tokens)} context"]
        if self.limited_by == "native":
            parts.append("the model's maximum")
        else:
            parts.append(f"native {_k(self.native)}")
        parts.append(f"~{self.kv_gb:g} GB of cache at full")
        if self.limited_by == "memory":
            parts.append("limited by memory")
        return " · ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "summary": self.describe()}


def _k(n: int) -> str:
    return f"{n // 1024}k"


def read_config(repo: str) -> Optional[Dict[str, Any]]:
    """The model's config.json from the Hugging Face cache, if downloaded."""
    snap = deploy.snapshot_dir(repo)
    if snap is None:
        return None
    try:
        return json.loads((snap / "config.json").read_text())
    except (OSError, ValueError):
        return None


def native(config: Dict[str, Any]) -> int:
    text = config.get("text_config") or config.get("llm_config") or config
    return int(text.get("max_position_embeddings") or 0)


def size(config: Dict[str, Any], room_gb: float, cap: int = SPEED_CAP) -> Optional[Window]:
    """The largest step that the model supports, that fits in `room_gb`
    of cache, and that stays under the speed cap. None when the config
    says nothing — a stored number is better than a guess."""
    limit = native(config)
    if not limit:
        return None
    per_token = deploy.kv_gb(config, 1 << 20) / (1 << 20)   # GB per token, rounding kept small
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


def harness_ready(tokens: int) -> bool:
    """Can a coding harness work inside this window?"""
    return tokens >= HARNESS_MIN
