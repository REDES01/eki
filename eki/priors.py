# SPDX-License-Identifier: Apache-2.0
"""Starting beliefs about what each class of model is good at.

eki has no history on its first day, so it starts from public evidence: on
open leaderboards and public evaluations, frontier hosted models lead on
agentic coding, hard reasoning and research, large open-weight models are
close behind on everyday chat, writing and translation and well behind on
the hardest reasoning, and small open models are fine for short, easy work
and unreliable past that. That is the shape encoded below — coarse on
purpose, because the exact numbers move every month and a wrong decimal
would look like precision eki doesn't have.

These are priors, not measurements. They decide only *whether a class is
good enough* for a request; cost still decides which of the good-enough
providers is picked, and the user's policy still overrides both. A later
version replaces them, class by class, with what actually happened on this
machine — overrides, retries and redos — with these numbers as a prior worth
about ten of the user's own runs.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

#: how good a class is at each task, 0–1. Read as "would a careful user
#: accept this class's answer without redoing it".
QUALITY: Dict[str, Dict[str, float]] = {
    # a frontier model driven as an agent: can also change files
    "frontier_agent": {"chat": 0.95, "writing": 0.93, "translate": 0.92, "code": 0.95,
                       "repo": 0.95, "math": 0.92, "research": 0.9, "image": 0.0},
    # the same vendors' faster, cheaper tier as an agent: sonnet, haiku, the
    # smaller Codex models — very good, a step behind on the hardest work
    "frontier_agent_fast": {"chat": 0.9, "writing": 0.88, "translate": 0.9, "code": 0.9,
                            "repo": 0.9, "math": 0.82, "research": 0.85, "image": 0.0},
    # the same class of model over an API, no tools of its own
    "frontier_api": {"chat": 0.95, "writing": 0.93, "translate": 0.92, "code": 0.92,
                     "repo": 0.0, "math": 0.9, "research": 0.7, "image": 0.0},
    # a 20B-and-up open-weight model on this Mac
    "large_open": {"chat": 0.85, "writing": 0.82, "translate": 0.85, "code": 0.75,
                   "repo": 0.0, "math": 0.72, "research": 0.5, "image": 0.0},
    # roughly 7B–20B
    "mid_open": {"chat": 0.75, "writing": 0.7, "translate": 0.72, "code": 0.6,
                 "repo": 0.0, "math": 0.55, "research": 0.4, "image": 0.0},
    # under 7B: short, easy things
    "small_open": {"chat": 0.6, "writing": 0.55, "translate": 0.55, "code": 0.45,
                   "repo": 0.0, "math": 0.4, "research": 0.3, "image": 0.0},
    # a large or mid open model given Codex's hands (eki/gateway.py): the
    # model's own numbers, plus repo work at the easy end — a 4-bit 27B
    # follows a harness through small changes, not a refactor; measuring
    # it says where the line really is
    "large_agent": {"chat": 0.85, "writing": 0.82, "translate": 0.85, "code": 0.75,
                    "repo": 0.62, "math": 0.72, "research": 0.55, "image": 0.0},
    "mid_agent": {"chat": 0.75, "writing": 0.7, "translate": 0.72, "code": 0.6,
                  "repo": 0.5, "math": 0.55, "research": 0.4, "image": 0.0},
    "image": {"image": 0.9},
}
#: the class a local model's Codex companion takes
AGENT_OF = {"large_open": "large_agent", "mid_open": "mid_agent"}

#: what a request of each difficulty needs before eki will spend a cheap
#: provider on it rather than a better one
NEED = {"easy": 0.5, "medium": 0.72, "hard": 0.88}

_PARAMS = re.compile(r"[-_/](\d+(?:\.\d+)?)\s*([bB])\b")
#: model names (any vendor) that mean "the fast tier"
_FAST = re.compile(r"haiku|sonnet|mini|nano|flash|lite|turbo|luna|small", re.I)
_LOCAL = re.compile(r"127\.0\.0\.1|localhost|::1|0\.0\.0\.0")


def params_b(name: str) -> Optional[float]:
    """Parameter count from a model name — '…-27B-4bit' → 27."""
    hits = _PARAMS.findall(name or "")
    return max(float(h[0]) for h in hits) if hits else None


def class_of(kind: str, options: Dict[str, Any], capabilities: Any = None) -> str:
    if kind == "comfyui" or (capabilities is not None and getattr(capabilities, "images_out", False)
                             and not getattr(capabilities, "text", True)):
        return "image"
    if kind in ("claude_code", "codex"):
        return "frontier_agent"
    base = str(options.get("base_url", ""))
    if kind == "anthropic_api" or (kind == "openai_compat" and base and not _LOCAL.search(base)):
        return "frontier_api"
    size = params_b(str(options.get("model", "")))
    if size is None:
        return "mid_open"               # unknown local model: assume the middle
    if size >= 20:
        return "large_open"
    return "mid_open" if size >= 7 else "small_open"


def class_of_model(kind: str, model: str, options: Dict[str, Any],
                   capabilities: Any = None) -> str:
    """The class of one particular model behind a provider."""
    base = class_of(kind, {**options, "model": model or options.get("model", "")}, capabilities)
    if base == "frontier_agent" and model and _FAST.search(model):
        return "frontier_agent_fast"
    return base


def quality(kind: str, options: Dict[str, Any], task: str, capabilities: Any = None) -> float:
    return QUALITY.get(class_of(kind, options, capabilities), {}).get(task, 0.0)
