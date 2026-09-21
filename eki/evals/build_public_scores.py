# SPDX-License-Identifier: Apache-2.0
"""Turn the public boards into eki's starting beliefs.

    python -m eki.evals.build_public_scores            # fetch + rebuild
    python -m eki.evals.build_public_scores /path/to/benchmark_data.zip

Three sources, each open:
  - Epoch AI's benchmark hub (CC BY 4.0): every score it has collected or
    run, per model, per benchmark — the closed frontier models above all.
  - Hugging Face's official leaderboard data (MIT): the open-weight models,
    down to the 0.8B ones, on the same benchmarks where they overlap.
  - OpenRouter's model list (public, no key): API prices, which become the
    cost weights the router balances against quality.

The script keeps the benchmarks that map onto the kinds of work eki routes,
expresses each score relative to the best model on that benchmark across
both boards, and averages per (task, difficulty). The result is one small
JSON file shipped with eki: for a model the boards have seen, a far better
starting belief than "it's a frontier model" — until eki measures it.

Attribution: Epoch AI, 'Capabilities & Benchmarking', https://epoch.ai/benchmarks;
OpenEvals/leaderboard-data, https://huggingface.co/datasets/OpenEvals/leaderboard-data;
OpenRouter, https://openrouter.ai/models
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import net

URL = "https://epoch.ai/data/benchmark_data.zip"
HF_ROWS = ("https://datasets-server.huggingface.co/rows?dataset=OpenEvals%2Fleaderboard-data"
           "&config=default&split=train&offset={offset}&length=100")
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
OUT = Path(__file__).with_name("public_scores.json")

#: benchmark file → (task, difficulty). A model's public score for a
#: (task, difficulty) is the mean of its relative scores on these.
MAP: Dict[str, Tuple[str, str]] = {
    "gsm8k_external.csv": ("math", "easy"),
    "math_level_5.csv": ("math", "medium"),
    "otis_mock_aime_2024_2025.csv": ("math", "hard"),
    "frontiermath_tiers_1_3_v2.csv": ("math", "hard"),
    # "medium" here means everyday-hard, not graduate-exam-hard: MMLU is a
    # fair test of ordinary explanation; GPQA Diamond and HLE are the top
    "trivia_qa_external.csv": ("chat", "easy"),
    "mmlu_external.csv": ("chat", "medium"),
    "gpqa_diamond.csv": ("chat", "hard"),
    "hle_external.csv": ("chat", "hard"),
    "simplebench_external.csv": ("chat", "hard"),
    "simpleqa_verified.csv": ("research", "medium"),
    "aider_polyglot_external.csv": ("code", "medium"),
    "scicode_external.csv": ("code", "hard"),
    "frontiercode_external.csv": ("code", "hard"),
    "swe_bench_verified.csv": ("repo", "medium"),
    "terminalbench_external.csv": ("repo", "hard"),
    "frontierswe_external.csv": ("repo", "hard"),
    "lech_mazur_writing_external.csv": ("writing", "medium"),
    "fictionlivebench_external.csv": ("writing", "medium"),
    "deepresearchbench_external.csv": ("research", "hard"),
}
#: Hugging Face column → the Epoch benchmark it is the same test as (so the
#: two boards share one top), or its own name → (task, difficulty)
HF_MAP: Dict[str, Tuple[str, str, str]] = {
    "gsm8k_score": ("gsm8k_external.csv", "math", "easy"),
    "aime2026_score": ("aime2026", "math", "hard"),
    "hmmt2026_score": ("hmmt2026", "math", "hard"),
    "mmluPro_score": ("mmlu_pro", "chat", "medium"),
    "gpqa_score": ("gpqa_diamond.csv", "chat", "hard"),
    "hle_score": ("hle_external.csv", "chat", "hard"),
    "sweVerified_score": ("swe_bench_verified.csv", "repo", "medium"),
    "swePro_score": ("swe_pro", "repo", "hard"),
    "terminalBench_score": ("terminalbench_external.csv", "repo", "hard"),
}
#: models older than this don't inform anything eki can run today
MAX_AGE_DAYS = 730


def _score_column(meta: Dict[str, Dict[str, str]], name: str, header: List[str]) -> Optional[str]:
    m = meta.get(name)
    if m and m["score_column"] in header:
        return m["score_column"]
    for guess in ("Best score (across scorers)", "Score", "Accuracy", "Percent correct",
                  "Mean score", "EM", "Accuracy mean", "Average score", "16k token score",
                  "Main score", "Pass@1"):
        if guess in header:
            return guess
    return None


def _get_json(url: str) -> Dict:
    return net.get_json(url)


def fetch_hf() -> List[Dict]:
    """Every row of the Hugging Face leaderboard dataset, 100 at a time."""
    rows: List[Dict] = []
    offset = 0
    while True:
        data = _get_json(HF_ROWS.format(offset=offset))
        got = [r["row"] for r in data.get("rows", [])]
        rows.extend(got)
        offset += len(got)
        if not got or offset >= int(data.get("num_rows_total", 0)):
            return rows


def fetch_prices() -> Dict[str, Dict[str, float]]:
    """{base id: {"in": $/M, "out": $/M}} from OpenRouter's public model list."""
    out: Dict[str, Dict[str, float]] = {}
    for m in _get_json(OPENROUTER_MODELS).get("data", []):
        slug = m.get("id", "")
        if ":" in slug or slug.startswith("~"):
            continue                                    # :batch, :free, aliases
        pricing = m.get("pricing") or {}
        try:
            p_in, p_out = float(pricing.get("prompt") or 0), float(pricing.get("completion") or 0)
        except (TypeError, ValueError):
            continue
        if p_out <= 0:
            continue
        base = slug.split("/", 1)[-1].lower()
        if slug.startswith("anthropic/"):
            base = base.replace(".", "-")               # "claude-fable-5.1" → Epoch's "claude-fable-5-1"
        out[base] = {"in": round(p_in * 1e6, 4), "out": round(p_out * 1e6, 4)}
    return out


def _key(name: str) -> str:
    """One spelling for both boards: "Qwen/Qwen3.5-27B" and "qwen3.5-27B" → "qwen3.5-27b"."""
    return name.split("/")[-1].lower()


def build(zip_bytes: bytes, hf_rows: Optional[List[Dict]] = None,
          prices: Optional[Dict[str, Dict[str, float]]] = None) -> Dict:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = set(z.namelist())

    def rows(name: str) -> List[Dict[str, str]]:
        with z.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))

    meta = {r["source_file"]: r for r in rows("benchmark_metadata.csv")} \
        if "benchmark_metadata.csv" in names else {}
    dates: Dict[str, str] = {}
    display: Dict[str, str] = {}
    for r in rows("model_metadata.csv"):
        dates[r["model_version"]] = r.get("date", "")
        display[r["model_version"]] = r.get("model_group") or r.get("display_name") or ""
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - MAX_AGE_DAYS * 86400))

    per_model: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    raw: Dict[str, Dict[str, float]] = defaultdict(dict)
    tops: Dict[str, float] = {}
    used = []
    for name, (task, difficulty) in MAP.items():
        if name not in names:
            continue
        table = rows(name)
        if not table:
            continue
        col = _score_column(meta, name, list(table[0].keys()))
        if col is None:
            continue
        scale = float((meta.get(name) or {}).get("scale") or 1.0)
        scored: List[Tuple[str, float]] = []
        for r in table:
            try:
                value = float(r[col]) * scale
            except (KeyError, ValueError, TypeError):
                continue
            model = r.get("Model version") or r.get("model_version") or ""
            if not model or (dates.get(model, "9999") < cutoff):
                continue
            scored.append((model, value))
        if not scored:
            continue
        # the top is Epoch's own: one harness, closed models included. A
        # board that reports a higher number for the same benchmark ran it
        # differently (HLE with tools, say) and is capped at 1.0, not
        # allowed to move the closed models' ranking
        top = max(v for _, v in scored)
        if top <= 0:
            continue
        tops[name] = top
        used.append(name.replace("_external", "").replace(".csv", ""))
        for model, value in scored:
            rel = min(1.0, value / top)
            per_model[model][f"{task}/{difficulty}"].append(rel)
            raw[model][name.replace("_external.csv", "").replace(".csv", "")] = round(value, 3)

    # the Hugging Face board: shared benchmarks against the shared top. Its
    # own benchmarks list open models only, so their best isn't the best
    # there is; those are taken against a perfect score instead, which can
    # only understate an open model, never inflate it
    by_key = {_key(_strip_effort(m)): m for m in sorted(per_model, key=len, reverse=True)}
    # (plain ids sort last, so they win over their effort variants)
    for r in hf_rows or []:
        name = r.get("model_name") or ""
        if not name:
            continue
        model = by_key.get(_key(name)) or _key(name)
        if model not in display:
            display[model] = name
        for col, (epoch, task, difficulty) in HF_MAP.items():
            value = r.get(col)
            if value is None:
                continue
            value = float(value)
            if epoch in tops:
                top = tops[epoch]
                if top <= 1.0 < value:
                    value = value / 100.0             # Epoch keeps fractions; HF percents
                rel = min(1.0, value / top)
            else:
                rel = min(1.0, value / 100.0)
            bench = epoch.replace("_external", "").replace(".csv", "")
            if bench in raw[model]:
                continue                              # Epoch already has this one
            per_model[model][f"{task}/{difficulty}"].append(rel)
            raw[model][bench] = round(value, 3)
            if bench not in used:
                used.append(bench)

    models = {}
    for model, slots in per_model.items():
        entry = {
            "date": dates.get(model, ""),
            "name": display.get(model, ""),
            "scores": {slot: round(sum(v) / len(v), 3) for slot, v in slots.items()},
            "benchmarks": raw[model],
        }
        price = (prices or {}).get(_key(_strip_effort(model)))
        if price:
            entry["price"] = price
        models[model] = entry
    return {
        "source": "Epoch AI, 'Capabilities & Benchmarking', https://epoch.ai/benchmarks (CC BY 4.0); "
                  "OpenEvals/leaderboard-data on Hugging Face (MIT); prices from openrouter.ai",
        "retrieved": time.strftime("%Y-%m-%d"),
        "note": "scores are relative to the best model on each benchmark, averaged per task/difficulty",
        "benchmarks_used": sorted(set(used)),
        "models": models,
    }


_EFFORT_SUFFIX = re.compile(r"_(none|minimal|low|medium|high|xhigh|max|unknown)$")


def _strip_effort(model_id: str) -> str:
    return _EFFORT_SUFFIX.sub("", model_id)


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        data = Path(argv[1]).read_bytes()
    else:
        print(f"fetching {URL}…")
        data = net.get_bytes(URL, timeout=120)
    print("fetching the Hugging Face board…")
    try:
        hf_rows = fetch_hf()
    except Exception as e:                              # noqa: BLE001
        print(f"  skipped ({e})")
        hf_rows = []
    print("fetching prices…")
    try:
        prices = fetch_prices()
    except Exception as e:                              # noqa: BLE001
        print(f"  skipped ({e})")
        prices = {}
    out = build(data, hf_rows, prices)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"{len(out['models'])} models, {len(out['benchmarks_used'])} benchmarks → {OUT} "
          f"({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
