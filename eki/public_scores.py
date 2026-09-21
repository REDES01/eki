# SPDX-License-Identifier: Apache-2.0
"""What the public boards say about a model, as eki's starting belief.

eki can't rank Fable against Opus by testing them itself — every task it
could check, both pass. The public benchmarks can, so a snapshot of Epoch
AI's benchmark hub (CC BY 4.0) ships with eki, reduced to one number per
model per (kind of work, difficulty), relative to the best model on each
benchmark. A model the boards have seen starts from that instead of from
its class; a model they haven't — a 2B quantised build, say — keeps the
class prior until eki measures it.

Two things are matched, not assumed: which board entry an alias like
"fable" means (the newest version), and which reasoning-effort variant
(the one the CLI actually runs at, else the plain entry, else the mean).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

SNAPSHOT = Path(__file__).with_name("evals") / "public_scores.json"
#: what each CLI runs at unless told otherwise
DEFAULT_EFFORT = {"claude_code": "high", "codex": "medium"}
_EFFORT = re.compile(r"_(none|minimal|low|medium|high|xhigh|max|unknown)$")


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    try:
        return json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {"models": {}, "retrieved": "", "source": ""}


def attribution() -> str:
    d = load()
    return f"{d.get('source', '')} · retrieved {d.get('retrieved', '')}".strip(" ·")


def _base(model_id: str) -> str:
    return _EFFORT.sub("", model_id)


def _norm(name: str) -> str:
    """"mlx-community/Qwen3.5-2B-MLX-4bit" → "qwen3.5-2b"; "GPT-5.6-Terra" → "gpt-5.6-terra"."""
    n = name.split("/")[-1].lower()
    n = re.sub(r"-(mlx|gguf|awq|gptq|instruct|it|chat)\b", "", n)
    n = re.sub(r"-\d+bit$", "", n)
    n = re.sub(r"-(bf16|fp16|q\d[_a-z0-9]*)$", "", n)
    return n.strip("-")


def resolve(kind: str, model: str, default_model: str = "") -> Optional[str]:
    """The board's base id for what this provider would run.

    `model` is eki's id ("fable", "gpt-5.6-terra", "mlx-community/…");
    `default_model` is what the provider runs when none is named.
    """
    models = load().get("models", {})
    if not models:
        return None
    name = _norm(model or default_model)
    if not name:
        return None
    if kind == "claude_code" and not name.startswith("claude-"):
        # an alias: the newest version of that family
        family = f"claude-{name}-"
        versions = {_base(m): e.get("date", "") for m, e in models.items()
                    if _base(m).startswith(family) or _base(m) == family.rstrip("-")}
        if not versions:
            return None
        return max(versions, key=lambda v: versions[v])
    bases = {_base(m) for m in models}
    if name in bases:
        return name
    # a family without a version ("qwen3.5-2b") or a differently-cased id
    matches = [b for b in bases if b.lower() == name or b.lower().startswith(name + "-")]
    if not matches:
        return None
    dates = {b: max((e.get("date", "") for m, e in models.items() if _base(m) == b), default="")
             for b in matches}
    return max(matches, key=lambda b: dates[b])


def scores(base: str, effort: str = "") -> Dict[str, float]:
    """Per (task/difficulty) scores for a board entry, folding effort variants:
    the requested effort where it has a number, the plain entry next, else the
    mean of whatever variants reported that benchmark."""
    models = load().get("models", {})
    variants = {m: e for m, e in models.items() if _base(m) == base}
    if not variants:
        return {}
    slots = {s for e in variants.values() for s in e.get("scores", {})}
    out: Dict[str, float] = {}
    for slot in slots:
        wanted = variants.get(f"{base}_{effort}", {}).get("scores", {}).get(slot) if effort else None
        plain = variants.get(base, {}).get("scores", {}).get(slot)
        if wanted is not None:
            out[slot] = wanted
        elif plain is not None:
            out[slot] = plain
        else:
            have = [e["scores"][slot] for e in variants.values() if slot in e.get("scores", {})]
            out[slot] = round(sum(have) / len(have), 3)
    return out


def lookup(kind: str, model: str, default_model: str = "",
           effort: str = "") -> Optional[Dict[str, Any]]:
    """{"base": id, "name": display, "scores": {task/difficulty: 0..1}} or None."""
    base = resolve(kind, model, default_model)
    if base is None:
        return None
    effort = effort or DEFAULT_EFFORT.get(kind, "")
    got = scores(base, effort)
    if not got:
        return None
    entry = next((e for m, e in load()["models"].items() if _base(m) == base), {})
    out = {"base": base, "name": entry.get("name") or base, "scores": got,
           "date": entry.get("date", "")}
    if entry.get("price"):
        out["price"] = entry["price"]
    return out


def price(kind: str, model: str, default_model: str = "") -> Optional[Dict[str, float]]:
    """{"in": $/M, "out": $/M} from the public price list, or None."""
    base = resolve(kind, model, default_model)
    if base is None:
        return None
    entry = next((e for m, e in load()["models"].items() if _base(m) == base and e.get("price")),
                 None)
    return entry["price"] if entry else None


def nearest(slot_scores: Dict[str, float], task: str, difficulty: str) -> Optional[float]:
    """The score at this difficulty, or the closest one the boards have.

    A harder benchmark says at least as much about easier work; an easier
    one is a weaker witness for harder work, so it's discounted a little.
    """
    order = ["easy", "medium", "hard"]
    if f"{task}/{difficulty}" in slot_scores:
        return slot_scores[f"{task}/{difficulty}"]
    i = order.index(difficulty) if difficulty in order else 1
    for d in order[i + 1:]:                         # harder evidence first
        if f"{task}/{d}" in slot_scores:
            return slot_scores[f"{task}/{d}"]
    for d in reversed(order[:i]):                   # then easier, discounted
        if f"{task}/{d}" in slot_scores:
            return round(slot_scores[f"{task}/{d}"] * 0.9, 3)
    if task == "translate":                         # no board for it: chat stands in
        return nearest(slot_scores, "chat", difficulty)
    return None
