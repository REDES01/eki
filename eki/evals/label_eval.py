# SPDX-License-Identifier: Apache-2.0
"""Is the router model actually better than the rules?

Caveat worth keeping in mind: the rules in eki.classify were tuned against
this same seed set, so their score here is optimistic and the model's is
not. A tie in these numbers probably means the model is ahead in the wild.
eki still ships with the rules on, because they cost nothing and never wait.

Runs both labellers over seed.jsonl and prints accuracy and latency. The
model is only worth switching on if it wins on task accuracy without being
slow enough to notice — eki's default stays on the rules otherwise.

    python -m eki.evals.label_eval                 # rules only
    python -m eki.evals.label_eval --url http://127.0.0.1:8090
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from .. import classify

SEED = Path(__file__).with_name("seed.jsonl")


def load(path: Path = SEED) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score(rows: List[Dict[str, Any]], got: List[classify.Label]) -> Dict[str, Any]:
    task_hits = sum(1 for r, g in zip(rows, got) if r["task"] == g.task)
    diff_hits = sum(1 for r, g in zip(rows, got) if r["difficulty"] == g.difficulty)
    both = sum(1 for r, g in zip(rows, got)
               if r["task"] == g.task and r["difficulty"] == g.difficulty)
    confusion = Counter((r["task"], g.task) for r, g in zip(rows, got) if r["task"] != g.task)
    latencies = [g.ms for g in got if g.ms]
    return {
        "n": len(rows),
        "task": round(task_hits / len(rows), 3),
        "difficulty": round(diff_hits / len(rows), 3),
        "both": round(both / len(rows), 3),
        "fell_back": sum(1 for g in got if g.source == "rules"),
        "p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "p95_ms": int(sorted(latencies)[int(len(latencies) * 0.95)]) if latencies else 0,
        "worst_confusions": confusion.most_common(6),
    }


async def run(url: str = "", model: str = "", deadline: float = classify.DEADLINE) -> Dict[str, Any]:
    rows = load()
    started = time.time()
    by_rules = [classify.rules(r["p"], has_folder=False) for r in rows]
    out = {"rules": {**score(rows, by_rules),
                     "total_s": round(time.time() - started, 2)}}
    if url:
        clf = classify.Classifier(classify.ModelClassifier(url, model, deadline))
        started = time.time()
        by_model = []
        for r in rows:
            by_model.append(await clf.label(r["p"], has_folder=False))
            clf.model.misses = 0        # the eval wants every attempt, not a stand-down
        out["model"] = {**score(rows, by_model),
                        "total_s": round(time.time() - started, 2)}
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="label_eval")
    ap.add_argument("--url", default="", help="OpenAI-compatible base URL of the router model")
    ap.add_argument("--model", default="")
    ap.add_argument("--deadline", type=float, default=classify.DEADLINE)
    args = ap.parse_args(argv)
    report = asyncio.run(run(args.url, args.model, args.deadline))
    print(json.dumps(report, indent=2))
    if "model" in report:
        better = (report["model"]["task"] > report["rules"]["task"]
                  and report["model"]["p95_ms"] < 1000)
        print("\nverdict:", "switch the router model on" if better
              else "keep the rules as the default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
