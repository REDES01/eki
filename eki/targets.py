"""Targets: one way to name where work goes, everywhere (no I/O here).

A target is written as text, `provider`, `provider:tier` or
`provider:model`, or as an object `{"provider", "model"?, "effort"?}` when
it carries an effort. The tiers are TIERS; a part after the colon (or an
object's `model`) that isn't a tier is a model id, so `claude:fast` and
`claude:claude-haiku-4-5` both work, and everything after the first colon is
the model (`local:qwen3:32b`). A bare `provider` means its own default.

A tier is looked up in the provider's entry in providers.json:
`"models": {"fast": ..., "default": ..., "top": ...}`; `default` falls back
to the entry's old `model`, which is never rewritten. Tiers are still read;
nothing writes them any more.

choose() checks a target against the provider's read model list
(modellist.listed()): a model or an effort the program no longer offers
falls back to its default, and the note says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

TIERS = ("fast", "default", "top")
#: the keys an object target may have
KEYS = ("provider", "model", "effort")


@dataclass
class Target:
    provider: str
    tier: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None


class Choice(NamedTuple):
    model: Optional[str]         # None: the provider's default
    effort: Optional[str]        # None: the model's default
    note: str                    # why it isn't what was written, or ""


def _text(x: Any, what: str, whole: Any) -> Optional[str]:
    if x is None:
        return None
    if not isinstance(x, str):
        raise ValueError(f"{what} {x!r} in target {whole!r} is not text")
    return x.strip() or None


def parse(x: Any) -> Target:
    """A Target from text or an object; ValueError when it names no provider
    or a part is empty or not text."""
    if isinstance(x, Target):
        return x
    if isinstance(x, dict):
        provider = _text(x.get("provider"), "provider", x)
        if not provider:
            raise ValueError(f"target {x!r} names no provider")
        model = _text(x.get("model"), "model", x)
        effort = _text(x.get("effort"), "effort", x)
        if model in TIERS:
            return Target(provider, tier=model, effort=effort)
        return Target(provider, model=model, effort=effort)
    text = str(x or "").strip()
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


def fmt(t: Target) -> Union[str, Dict[str, str]]:
    """The shortest written form: text without an effort, an object with one."""
    part = t.tier or t.model
    if t.effort:
        out = {"provider": t.provider}
        if part:
            out["model"] = part
        out["effort"] = t.effort
        return out
    return f"{t.provider}:{part}" if part else t.provider


def key(x: Any) -> str:
    """A stable one-line name (`claude:opus@high`) for dict keys and
    role_calls.target; never parsed back."""
    try:
        t = parse(x)
    except ValueError:
        return str(x)
    part = t.tier or t.model
    out = f"{t.provider}:{part}" if part else t.provider
    return f"{out}@{t.effort}" if t.effort else out


def provider_of(x: Any) -> str:
    if isinstance(x, Target):
        return x.provider
    if isinstance(x, dict):
        p = x.get("provider")
        return p.strip() if isinstance(p, str) else ""
    return str(x or "").partition(":")[0].strip()


# ---- against the read list --------------------------------------------------------------

def _rows(listed: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
    if listed is None:
        return None
    return [r if isinstance(r, dict) else {"model": str(r)} for r in listed]


def _row(rows: Optional[List[Dict[str, Any]]], model: Optional[str]) -> Optional[Dict[str, Any]]:
    for r in rows or []:
        if (r.get("model") == model) if model else r.get("is_default"):
            return r
    return None


def label(provider: str, model: Optional[str], effort: Optional[str],
          listed: Optional[List[Any]]) -> str:
    """`claude · Opus 5.5 · high`, from the program's own label."""
    row = _row(_rows(listed), model)
    name = (row.get("label") or row.get("model")) if row else None
    parts = [provider, name or model or "its default"]
    if effort:
        parts.append(effort)
    return " · ".join(parts)


def _model(t: Target, cfg: Dict[str, Any], rows: Optional[List[Dict[str, Any]]]) -> Tuple[Optional[str], str]:
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
    if rows is not None and model not in [r.get("model") for r in rows]:
        return None, f"{model} isn't in {t.provider}'s list any more; its default"
    return model, ""


def _effort(t: Target, model: Optional[str], rows: Optional[List[Dict[str, Any]]]) -> Tuple[Optional[str], str]:
    if not t.effort:
        return None, ""
    row = _row(rows, model)
    if row is None or "efforts" not in row:      # never read, ids only, or no default known: trust it
        return t.effort, ""
    name = model or row.get("model") or "its default"
    efforts = row["efforts"]
    if efforts is None:
        return None, f"{name} takes no effort"
    if t.effort not in efforts:
        return None, f"{t.provider} {name} doesn't offer effort {t.effort} any more; its default"
    return t.effort, ""


def choose(target: Any, cfg: Dict[str, Any], listed: Optional[List[Any]]) -> Choice:
    """The model and effort to ask for, checked against `listed`
    (modellist.listed()'s dicts, a plain list of ids, or None when the list
    was never read: then what is written is trusted)."""
    t = parse(target)
    rows = _rows(listed)
    model, note = _model(t, cfg, rows)
    effort, why = _effort(t, model, rows)
    return Choice(model, effort, "; ".join(n for n in (note, why) if n))


def resolve(target: Any, cfg: Dict[str, Any],
            listed: Optional[List[Any]]) -> Tuple[Optional[str], str]:
    """(the model id to ask for, or None for the provider's default; a note, or "")."""
    c = choose(target, cfg, listed)
    return c.model, c.note


def problems(x: Any, known: set) -> List[str]:
    """What's wrong with target `x`, for settings validation ([] when nothing)."""
    if isinstance(x, dict):
        extra = sorted(str(k) for k in x if k not in KEYS)
        if extra:
            return [f"target {x!r} has unknown keys: {', '.join(extra)}"]
    elif not isinstance(x, str):
        return [f"target {x!r} is not text or an object"]
    try:
        t = parse(x)
    except ValueError as e:
        return [str(e)]
    out = []
    if t.provider not in known:
        out.append(f"unknown provider {t.provider!r} in target {x!r}")
    if t.model is not None and any(c.isspace() for c in t.model):
        out.append(f"model {t.model!r} in target {x!r} has a space in it")
    if t.effort is not None and any(c.isspace() for c in t.effort):
        out.append(f"effort {t.effort!r} in target {x!r} has a space in it")
    return out
