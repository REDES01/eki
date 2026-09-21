# SPDX-License-Identifier: Apache-2.0
"""Which local model is worth downloading on this Mac.

Nothing on the internet answers that: Hugging Face and the model apps
say whether a build *fits*, and no one says whether it's any *good*. eki
has both halves. The public boards (eki/public_scores.py) know how each
base model scores against the best there is; the mlx-community catalogue
says which builds of it exist and links each to its base; this Mac's
memory says which of those builds fit and with how much context
(eki/context.py). Join them and sort.

What comes out is a short list: the best model that runs here, the best
for code, the lightest one that's still good — each with the build eki
would pick (the highest precision that keeps a useful context window),
what it needs, and its board scores so the choice is visible. Boards are
priors; once a build is measured here, that number is the one routing
uses (eki/capability.py).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import context, public_scores
from .deploy import HF, OVERHEAD_GB

CACHE = Path("~/.eki/suggest.json").expanduser()
CONFIGS = Path("~/.eki/suggest-configs.json").expanduser()
CACHE_HOURS = 24
#: catalogue rows fetched (one request); the join to the boards is what
#: narrows it, not this number
CATALOGUE = 1000
#: bases shown, and bases whose builds get their config read
SHOWN = 8
SHORTLIST = 16
#: models the boards haven't scored, shown by trending — a release the
#: boards will take months to reach is here the week it lands
TRENDING = 8

#: the plain mlx_lm quantisations, which every mlx_lm can load; the rest
#: are experiments (OptiQ, mxfp4, nvfp4), multi-token-prediction builds
#: (MTP) that need special serving, or bf16 originals that rarely fit
_PLAIN = re.compile(r"-(?:mlx-)?(\d)bit$", re.I)
#: mlx-community's mixed-precision scheme: loads in mlx_lm, used only for a
#: base that has no plain build
_OPTIQ = re.compile(r"-optiq-(\d)bit$", re.I)
#: vision-only architectures that mlx_lm can't serve, thinking-only models
#: (no switch: slow and wordy for the quick answers a local model is for),
#: and edits of the weights nobody benchmarked
_SKIP = re.compile(r"mtp|nvfp4|mxfp|bf16|fp16|uncensored|abliterated|dwq|-vision|-vl-|thinking", re.I)

#: how a board's slots roll up into what a person is choosing a model for
GROUPS = {"code": ("code/", "repo/"), "chat": ("chat/", "writing/", "research/"), "math": ("math/",)}

#: the cache a normal thread carries; what "needs" means on the card — the
#: window itself can be far larger, and grows only as a thread does
USUAL_CONTEXT = 32768
#: a build that leaves this much of the ceiling free is preferred over a
#: higher-precision one that doesn't: the Mac has other things to run
COMFORT = 0.6


#: "qwen3.8-27b" → family "qwen-27b", version 3.8: the vendor's name, the
#: generation right after it, and everything else (size, variant) as is
_FAMILY = re.compile(r"^(?P<vendor>[a-z]+)-?(?P<version>\d+(?:\.\d+)?)(?P<rest>(?:-.*)?)$")


def family(base: str) -> Tuple[str, float]:
    """The line a base belongs to and its generation, so that Qwen3.8 27B
    is seen as the successor of Qwen3.5 27B rather than a rival."""
    m = _FAMILY.match(base)
    if not m:
        return base, 0.0
    return m.group("vendor") + m.group("rest"), float(m.group("version"))


def supersede(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Within a family keep the newest generation, and let it inherit
    the benchmarks its predecessors have that it lacks. The boards lag
    a release by months; a vendor's newer model of the same size is not
    worse on chat because nobody has scored it on chat yet. Inherited
    slots are listed as estimated so the card can say so."""
    lines: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        key, version = family(e["base"])
        e["version"] = version
        lines.setdefault(key, []).append(e)
    out = []
    for members in lines.values():
        members.sort(key=lambda e: (e["version"], e.get("date", "")), reverse=True)
        newest = members[0]
        inherited: Dict[str, float] = {}
        for older in members[1:]:
            for slot, v in older["slots"].items():
                if slot not in newest["slots"] and slot not in inherited:
                    inherited[slot] = v
        newest["estimated"] = sorted(inherited)
        newest["supersedes"] = [m["name"] for m in members[1:]]
        newest["scores"] = _rollup({**inherited, **newest["slots"]})
        out.append(newest)
    return out


def _rollup(slots: Dict[str, float]) -> Dict[str, float]:
    """Medium and hard slots only: the easy ones (GSM8K and kin) saturate,
    and a 2024 model at 94% there would outrank this year's best."""
    out: Dict[str, float] = {}
    for group, prefixes in GROUPS.items():
        have = [v for s, v in slots.items() if s.startswith(prefixes) and not s.endswith("/easy")]
        if have:
            out[group] = round(sum(have) / len(have), 2)
    if out:
        out["overall"] = round(sum(out.values()) / len(out), 2)
    return out


def weights_gb(row: Dict[str, Any], bits: int) -> float:
    """From the Hub's parameter counts by dtype: packed weights at `bits`
    plus a little for scales, the rest at their own width."""
    params = (row.get("safetensors") or {}).get("parameters") or {}
    total = 0.0
    for dtype, n in params.items():
        if dtype in ("U32", "U8", "I8") and bits:
            total += n * (bits + 0.5) / 8          # scales and biases ride along
        elif dtype in ("F32", "I32"):
            total += n * 4
        else:                                       # BF16, F16, and anything odd
            total += n * 2
    return round(total / 1024**3, 1)


def _bits(row: Dict[str, Any]) -> int:
    m = _PLAIN.search(row["id"]) or _OPTIQ.search(row["id"])
    if m:
        return int(m.group(1))
    return int(((row.get("config") or {}).get("quantization_config") or {}).get("bits") or 0)


async def catalogue(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    r = await client.get(f"{HF}/api/models", params=[
        ("author", "mlx-community"), ("sort", "downloads"), ("direction", "-1"),
        ("limit", str(CATALOGUE)), ("expand[]", "safetensors"), ("expand[]", "downloads"),
        ("expand[]", "baseModels"), ("expand[]", "config"), ("expand[]", "pipeline_tag"),
        ("expand[]", "createdAt"), ("expand[]", "trendingScore")])
    r.raise_for_status()
    return [m for m in r.json() if (m.get("pipeline_tag") or "") in ("text-generation", "image-text-to-text")]


async def _config(client: httpx.AsyncClient, repo: str, cache: Dict[str, Any]) -> Dict[str, Any]:
    if repo in cache:
        return cache[repo]
    try:
        r = await client.get(f"{HF}/{repo}/resolve/main/config.json")
        cfg = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        cfg = {}
    cache[repo] = cfg
    return cfg


def _base_id(row: Dict[str, Any]) -> Optional[str]:
    models = (row.get("baseModels") or {}).get("models") or []
    return models[0].get("id") if models else None


def _choose(builds: List[Dict[str, Any]], ceiling_gb: float) -> Optional[Dict[str, Any]]:
    """The highest precision that still leaves the Mac room and keeps a
    useful window; then the highest that fits with a useful window; then
    whatever fits with the most context."""
    fitting = [b for b in builds if b["fits"]]
    if not fitting:
        return None
    roomy = [b for b in fitting if context.harness_ready(b["context"])]
    comfortable = [b for b in roomy if b["need_gb"] <= ceiling_gb * COMFORT]
    for pool in (comfortable, roomy):
        if pool:
            return max(pool, key=lambda b: (b["bits"], b["context"]))
    return max(fitting, key=lambda b: (b["context"], b["bits"]))


def _trusted(base: str, slots: Dict[str, float]) -> bool:
    """One number from one leaderboard is not evidence: a base is shown
    when a dated board entry (Epoch's) covers it, or when it has scores
    in two of the groups a person chooses by."""
    models = public_scores.load().get("models", {})
    dated = any(e.get("date") for m, e in models.items() if public_scores._base(m) == base)
    groups = {g for g, prefixes in GROUPS.items()
              if any(s.startswith(prefixes) and not s.endswith("/easy") for s in slots)}
    return bool(groups) and (dated or len(groups) >= 2)


async def build(ceiling_gb: float, free_gb: float, installed: List[str]) -> Dict[str, Any]:
    """The suggestions for a Mac with `ceiling_gb` for models."""
    try:
        configs = json.loads(CONFIGS.read_text())
    except (OSError, ValueError):
        configs = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        rows = await catalogue(client)
        # every board base with plain builds in the catalogue
        by_base: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            name = row["id"].split("/")[-1]
            plain = bool(_PLAIN.search(name))
            if _SKIP.search(name) or not (plain or _OPTIQ.search(name)):
                continue
            base_id = _base_id(row)
            if not base_id:
                continue
            base = public_scores.resolve("mlx", base_id)
            # the boards' family fallback may land on a sibling ("qwen3-30b-a3b"
            # → the thinking variant); a build is only credited with its own scores
            if base is not None and base != public_scores.base_name(base_id) and "thinking" in base:
                base = None
            slots = public_scores.scores(base) if base else {}
            rolled = _rollup(slots)
            scored = bool(base and rolled and _trusted(base, slots))
            if not scored:                          # known only by use: the second list
                if (row.get("createdAt") or "") < _year_ago():
                    continue                        # not new and never scored: nothing to say for it
                base, rolled, slots = public_scores.base_name(base_id), {}, {}
            entry = by_base.setdefault(base, {
                "base": base, "scores": rolled, "slots": slots, "builds": [], "scored": scored,
                "name": _name(base) if scored else base_id.split("/")[-1],
                "date": _date(base) if scored else "", "trending": 0.0,
                "params_b": _params(row, base_id)})
            bits = _bits(row)
            if bits >= 3:                           # 2-bit builds lose too much to recommend
                entry["builds"].append({"repo": row["id"], "bits": bits, "downloads": row.get("downloads", 0),
                                        "weights_gb": weights_gb(row, bits), "plain": plain})
                entry["trending"] = max(entry["trending"], float(row.get("trendingScore") or 0))
        for entry in by_base.values():
            if any(b["plain"] for b in entry["builds"]):
                entry["builds"] = [b for b in entry["builds"] if b["plain"]]
        entries = [e for e in by_base.values() if e["builds"]]
        # the boards rank; trending separates what the boards can't
        ranked = sorted(supersede([e for e in entries if e["scored"]]),
                        key=lambda e: (e["scores"]["overall"], e["trending"]), reverse=True)
        # what the boards don't know yet: by trending, then the biggest that fits
        unranked = sorted([e for e in entries if not e["scored"]],
                          key=lambda e: (e["trending"], e["params_b"]), reverse=True)
        # a build whose weights alone don't fit is out before any config is read
        shortlist, trending = [], []
        for pool, out_, limit in ((ranked, shortlist, SHORTLIST), (unranked, trending, TRENDING * 3)):
            for entry in pool:
                entry["builds"] = [b for b in entry["builds"] if b["weights_gb"] + OVERHEAD_GB < ceiling_gb]
                if entry["builds"]:
                    out_.append(entry)
                if len(out_) >= limit:
                    break
        await asyncio.gather(*[_config(client, b["repo"], configs)
                               for e in shortlist + trending for b in e["builds"]])
    try:
        CONFIGS.parent.mkdir(parents=True, exist_ok=True)
        CONFIGS.write_text(json.dumps(configs))
    except OSError:
        pass
    have = {public_scores.resolve("mlx", r) or public_scores.base_name(r) for r in installed}
    out, fresh = [], []
    for entry in shortlist + trending:
        listing = out if entry["scored"] else fresh
        for b in entry["builds"]:
            cfg = configs.get(b["repo"]) or {}
            room = ceiling_gb - b["weights_gb"] - OVERHEAD_GB
            window = context.size(cfg, room)
            tokens = window.tokens if window else context.STEPS[0]
            # what it takes with a normal thread; the window is the most it can grow to
            need = round(b["weights_gb"] + context.kv_gb(cfg, min(tokens, USUAL_CONTEXT)) + OVERHEAD_GB, 1)
            b.update({"context": tokens, "need_gb": need, "fits": need <= ceiling_gb,
                      "fits_now": need <= free_gb, "native": window.native if window else 0,
                      "limited_by": window.limited_by if window else ""})
        pick = _choose(entry["builds"], ceiling_gb)
        if pick is None:
            continue
        listing.append({
            "base": entry["base"], "name": entry["name"], "repo": pick["repo"], "bits": pick["bits"],
            "trending": round(entry["trending"], 1), "params_b": entry["params_b"], "scored": entry["scored"],
            "weights_gb": pick["weights_gb"], "need_gb": pick["need_gb"], "context": pick["context"],
            "native": pick["native"], "fits_now": pick["fits_now"], "downloads": pick["downloads"],
            "scores": entry["scores"], "estimated": entry.get("estimated", []),
            "supersedes": entry.get("supersedes", []),
            "installed": entry["base"] in have,
            "others": [{"repo": b["repo"], "bits": b["bits"], "need_gb": b["need_gb"],
                        "context": b["context"], "fits": b["fits"]}
                       for b in sorted(entry["builds"], key=lambda b: b["bits"]) if b is not pick],
        })
    return {"suggestions": _label(out[:SHOWN]), "trending": fresh[:TRENDING],
            "ceiling_gb": ceiling_gb, "free_gb": free_gb,
            "built": int(time.time()), "attribution": public_scores.attribution()}


def _year_ago() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400))


def _params(row: Dict[str, Any], base_id: str) -> float:
    """Billions of parameters: from the name when it says, else counted."""
    from .profile import params_from_name
    named = params_from_name(base_id) or params_from_name(row["id"])
    if named:
        return named
    params = (row.get("safetensors") or {}).get("parameters") or {}
    return round(sum(params.values()) / 1e9, 1)


def _entry(base: str) -> Dict[str, Any]:
    return next((e for m, e in public_scores.load()["models"].items()
                 if public_scores._base(m) == base), {})


def _name(base: str) -> str:
    return str(_entry(base).get("name") or base).split("/")[-1]


def _date(base: str) -> str:
    return max((e.get("date", "") for m, e in public_scores.load()["models"].items()
                if public_scores._base(m) == base), default="")


def _label(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A word for the ones that stand out; the rest are just ranked."""
    if not items:
        return items
    for it in items:
        it["label"] = ""
    items[0]["label"] = "Best on this Mac"
    coders = [it for it in items if "code" in it["scores"]]
    if coders:
        best = max(coders, key=lambda it: it["scores"]["code"])
        if best is not items[0]:
            best["label"] = "Best for code"
    top = items[0]["scores"]["overall"]
    light = [it for it in items if it["scores"]["overall"] >= top * 0.75 and it["need_gb"] < items[0]["need_gb"] * 0.5]
    if light:
        pick = min(light, key=lambda it: it["need_gb"])
        if not pick["label"]:
            pick["label"] = "Light and quick"
    return items


async def get(ceiling_gb: float, free_gb: float, installed: List[str],
              fresh: bool = False) -> Dict[str, Any]:
    """Cached for a day — the boards and the catalogue don't move faster,
    and the memory ceiling is a property of the Mac. What's free right
    now is patched in on every call."""
    if not fresh:
        try:
            data = json.loads(CACHE.read_text())
            if (data.get("ceiling_gb") == ceiling_gb
                    and time.time() - data.get("built", 0) < CACHE_HOURS * 3600
                    and data.get("installed_key") == sorted(installed)):
                data["free_gb"] = free_gb
                for it in data["suggestions"]:
                    it["fits_now"] = it["need_gb"] <= free_gb
                return data
        except (OSError, ValueError):
            pass
    data = await build(ceiling_gb, free_gb, installed)
    data["installed_key"] = sorted(installed)
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data))
    except OSError:
        pass
    return data
