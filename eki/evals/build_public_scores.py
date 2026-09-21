# SPDX-License-Identifier: Apache-2.0
"""Turn Epoch AI's benchmark hub into eki's public priors.

    python -m eki.evals.build_public_scores            # fetch + rebuild
    python -m eki.evals.build_public_scores /path/to/benchmark_data.zip

Epoch AI publishes every score it has collected or run, per model, per
benchmark, as CSV under CC BY 4.0. This script keeps the benchmarks that map
onto the kinds of work eki routes, expresses each score relative to the best
current model on that benchmark, and averages per (task, difficulty). The
result is a small JSON file shipped with eki: for a model the public boards
have seen, that is a far better starting belief than "it's a frontier model".

Attribution: Epoch AI, 'Capabilities & Benchmarking', https://epoch.ai/benchmarks
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
from urllib.request import urlopen

URL = "https://epoch.ai/data/benchmark_data.zip"
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


def build(zip_bytes: bytes) -> Dict:
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
        top = max(v for _, v in scored)
        if top <= 0:
            continue
        used.append(name.replace("_external", "").replace(".csv", ""))
        for model, value in scored:
            rel = min(1.0, value / top)
            per_model[model][f"{task}/{difficulty}"].append(rel)
            raw[model][name.replace("_external.csv", "").replace(".csv", "")] = round(value, 3)

    models = {}
    for model, slots in per_model.items():
        models[model] = {
            "date": dates.get(model, ""),
            "name": display.get(model, ""),
            "scores": {slot: round(sum(v) / len(v), 3) for slot, v in slots.items()},
            "benchmarks": raw[model],
        }
    return {
        "source": "Epoch AI, 'Capabilities & Benchmarking', https://epoch.ai/benchmarks (CC BY 4.0)",
        "retrieved": time.strftime("%Y-%m-%d"),
        "note": "scores are relative to the best model on each benchmark, averaged per task/difficulty",
        "benchmarks_used": sorted(set(used)),
        "models": models,
    }


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        data = Path(argv[1]).read_bytes()
    else:
        print(f"fetching {URL}…")
        with urlopen(URL, timeout=120) as r:            # noqa: S310 (a known public URL)
            data = r.read()
    out = build(data)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"{len(out['models'])} models, {len(out['benchmarks_used'])} benchmarks → {OUT} "
          f"({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
