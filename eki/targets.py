"""Targets: one way to name where work goes, everywhere (no I/O here).

A target is `provider`, `provider:tier` or `provider:model`. The tiers are
TIERS; a part after the colon that isn't a tier is a model id, so
`claude:fast` and `claude:claude-haiku-4-5` both work. A bare `provider`
means the provider's own default.

A tier is looked up in the provider's entry in providers.json:
`"models": {"fast": ..., "default": ..., "top": ...}`; `default` falls back
to the entry's old `model`, which is never rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

TIERS = ("fast", "default", "top")


@dataclass
class Target:
    provider: str
    tier: Optional[str] = None
    model: Optional[str] = None


def parse(s: str) -> Target:
    """`provider`, `provider:tier` or `provider:model`; ValueError when a part is empty."""
    text = str(s or "").strip()
    provider, colon, rest = text.partition(":")
    provider, rest = provider.strip(), rest.strip()
    if not provider:
        raise ValueError(f"target {text!r} names no provider")
    if not colon:
        return Target(provider)
    if not rest:
        raise ValueError(f"target {text!r} has nothing after the colon")
    if rest in TIERS:
        return Target(provider, tier=rest)
    return Target(provider, model=rest)


def fmt(t: Target) -> str:
    part = t.tier or t.model
    return f"{t.provider}:{part}" if part else t.provider


def provider_of(s: str) -> str:
    return str(s or "").partition(":")[0].strip()


def resolve(target: Any, cfg: Dict[str, Any],
            listed: Optional[List[str]]) -> Tuple[Optional[str], str]:
    """(the model id to ask for, or None for the provider's default; a note, or "").

    `listed` is the provider's read model list, or None when it was never
    read (then a model name is trusted)."""
    t = target if isinstance(target, Target) else parse(target)
    model = t.model
    if t.tier:
        models = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
        model = str(models.get(t.tier) or "").strip() or None
        if model is None and t.tier == "default":
            model = str(cfg.get("model") or "").strip() or None
        if model is None:
            return None, f"{t.provider} has no {t.tier} model picked; its default"
    if model is None:
        return None, ""
    if listed is not None and model not in listed:
        return None, f"{model} isn't in {t.provider}'s list any more; its default"
    return model, ""


def problems(s: Any, known: set) -> List[str]:
    """What's wrong with target `s`, for settings validation ([] when nothing)."""
    if not isinstance(s, str):
        return [f"target {s!r} is not text"]
    try:
        t = parse(s)
    except ValueError as e:
        return [str(e)]
    out = []
    if t.provider not in known:
        out.append(f"unknown provider {t.provider!r} in target {s!r}")
    if t.model is not None and any(c.isspace() for c in t.model):
        out.append(f"model {t.model!r} in target {s!r} has a space in it")
    return out
