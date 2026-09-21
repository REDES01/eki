# SPDX-License-Identifier: Apache-2.0
"""The same public test items, put to every model eki has.

The boards can't compare a 4-bit local build with Opus: nobody benchmarks
the build you run. eki can, by sampling items from the open benchmark
datasets — GSM8K, MATH, AIME, MBPP, HumanEval, TriviaQA, MMLU-Pro — and
asking every provider the same ones through the same adapters. Most have a
checkable answer (a number, a letter, a test suite), so no judge is needed
and the scores land on one scale, closed and open alike.

Items are fetched once from the Hugging Face datasets server (no key, no
library) and cached under ~/.eki/bench. The sample is seeded, so every model
sees the same items until the cache is cleared.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import net

HOME = Path("~/.eki/bench").expanduser()
API = "https://datasets-server.huggingface.co/rows"
SEED = 20260921
#: items per (task, difficulty) unless the set is smaller
PER_SLOT = 30
WINDOW = 100                                    # rows the API hands out per call

#: benchmark → where it lives, what it tests, how it's checked
SETS: Dict[str, Dict[str, Any]] = {
    "gsm8k": {"dataset": "openai/gsm8k", "config": "main", "split": "test",
              "task": "math", "difficulty": "easy", "license": "MIT",
              "cite": "Cobbe et al. 2021, GSM8K"},
    "math": {"dataset": "EleutherAI/hendrycks_math", "split": "test",
             "configs": ["algebra", "number_theory", "counting_and_probability",
                         "intermediate_algebra", "prealgebra", "precalculus", "geometry"],
             "task": "math", "difficulty": "medium", "license": "MIT",
             "cite": "Hendrycks et al. 2021, MATH (Level 5)"},
    "aime25": {"dataset": "math-ai/aime25", "config": "default", "split": "test",
               "task": "math", "difficulty": "hard", "license": "Apache-2.0",
               "cite": "AIME 2025", "per_slot": 15},        # the slow ones
    "mbpp": {"dataset": "google-research-datasets/mbpp", "config": "sanitized", "split": "test",
             "task": "code", "difficulty": "easy", "license": "CC BY 4.0",
             "cite": "Austin et al. 2021, MBPP (sanitized)"},
    "humaneval": {"dataset": "openai/openai_humaneval", "config": "openai_humaneval",
                  "split": "test", "task": "code", "difficulty": "medium", "license": "MIT",
                  "cite": "Chen et al. 2021, HumanEval"},
    "triviaqa": {"dataset": "mandarjoshi/trivia_qa", "config": "rc.nocontext",
                 "split": "validation", "task": "chat", "difficulty": "easy",
                 "license": "Apache-2.0", "cite": "Joshi et al. 2017, TriviaQA"},
    "mmlu_pro": {"dataset": "TIGER-Lab/MMLU-Pro", "config": "default", "split": "test",
                 "task": "chat", "difficulty": "medium", "license": "MIT",
                 "cite": "Wang et al. 2024, MMLU-Pro"},
}

MATH_TAIL = ("\n\nWork it out, then finish with a line of the form "
             "`Answer: <final answer>`.")
BOXED_TAIL = ("\n\nWork it out, then give the final answer in \\boxed{}.")
LETTER_TAIL = ("\n\nThink it through, then finish with a line of the form "
               "`Answer: <letter>`.")
CODE_TAIL = "\n\nReply with the complete Python code in one ```python block and nothing else."


# ---- fetching ------------------------------------------------------------

def _rows(dataset: str, config: str, split: str, offset: int, length: int) -> List[Dict[str, Any]]:
    q = urllib.parse.urlencode({"dataset": dataset, "config": config, "split": split,
                                "offset": offset, "length": length})
    data = net.get_json(f"{API}?{q}")
    return [row["row"] for row in data.get("rows", [])]


def _total(dataset: str, config: str, split: str) -> int:
    q = urllib.parse.urlencode({"dataset": dataset, "config": config, "split": split,
                                "offset": 0, "length": 1})
    return int(net.get_json(f"{API}?{q}").get("num_rows_total", 0))


def _window(dataset: str, config: str, split: str, rng: random.Random,
            want: int) -> List[Dict[str, Any]]:
    """A seeded window of rows: one API call, `want` rows sampled from it."""
    total = _total(dataset, config, split)
    if total <= 0:
        return []
    length = min(WINDOW, total)
    offset = rng.randrange(0, max(1, total - length + 1))
    rows = _rows(dataset, config, split, offset, length)
    rng.shuffle(rows)
    return rows[:want]


# ---- turning rows into items ---------------------------------------------

def _item(name: str, i: int, prompt: str, check: Dict[str, Any]) -> Dict[str, Any]:
    spec = SETS[name]
    return {"id": f"{name}/{i}", "set": name, "task": spec["task"],
            "difficulty": spec["difficulty"], "prompt": prompt, "check": check}


def _gsm8k(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    gold = row["answer"].rsplit("####", 1)[-1].strip().replace(",", "")
    try:
        value = float(gold)
    except ValueError:
        return None
    return _item("gsm8k", i, row["question"].strip() + MATH_TAIL,
                 {"type": "number", "value": value, "max_tokens": 1500})


def _math(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    if row.get("level") != "Level 5":
        return None
    gold = last_boxed(row.get("solution", ""))
    if not gold:
        return None
    return _item("math", i, row["problem"].strip() + BOXED_TAIL,
                 {"type": "boxed", "value": gold, "max_tokens": 4000})


def _aime(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    try:
        value = float(str(row["answer"]).strip())
    except ValueError:
        return None
    return _item("aime25", i, row["problem"].strip() + MATH_TAIL,
                 {"type": "number", "value": value, "max_tokens": 6000})


def _mbpp(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    tests = list(row.get("test_list") or [])
    if not tests:
        return None
    m = re.search(r"assert\s+(\w+)\s*\(", tests[0])
    name = m.group(1) if m else ""
    hint = f" Name the function `{name}`." if name else ""
    prompt = row["prompt"].strip() + hint + CODE_TAIL
    setup = "\n".join(row.get("test_imports") or [])
    return _item("mbpp", i, prompt,
                 {"type": "tests", "setup": setup, "tests": "\n".join(tests), "max_tokens": 1200})


def _humaneval(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    prompt = ("Complete this Python function.\n\n```python\n" + row["prompt"].rstrip()
              + "\n```" + CODE_TAIL)
    tests = row["test"] + f"\n\ncheck({row['entry_point']})\n"
    return _item("humaneval", i, prompt,
                 {"type": "tests", "setup": row["prompt"], "tests": tests, "max_tokens": 1200})


def _triviaqa(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    aliases = [a for a in (row.get("answer") or {}).get("normalized_aliases") or [] if len(a) > 1]
    if not aliases:
        return None
    return _item("triviaqa", i, row["question"].strip() + "\n\nAnswer briefly.",
                 {"type": "contains", "any": aliases[:20], "max_tokens": 200})


def _mmlu_pro(row: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    options = list(row.get("options") or [])
    answer = str(row.get("answer", "")).strip().upper()
    if not options or answer not in "ABCDEFGHIJ" or len(answer) != 1:
        return None
    letters = "ABCDEFGHIJ"
    choices = "\n".join(f"{letters[k]}. {o}" for k, o in enumerate(options))
    return _item("mmlu_pro", i, f"{row['question'].strip()}\n\n{choices}" + LETTER_TAIL,
                 {"type": "letter", "value": answer, "max_tokens": 2500})


BUILDERS = {"gsm8k": _gsm8k, "math": _math, "aime25": _aime, "mbpp": _mbpp,
            "humaneval": _humaneval, "triviaqa": _triviaqa, "mmlu_pro": _mmlu_pro}


def last_boxed(text: str) -> str:
    """The contents of the last \\boxed{…} in a LaTeX solution, braces balanced."""
    start = text.rfind("\\boxed")
    if start < 0:
        return ""
    i = text.find("{", start)
    if i < 0:
        return ""
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j].strip()
        j += 1
    return ""


# ---- the cache -------------------------------------------------------------

def path(name: str) -> Path:
    return HOME / f"{name}.jsonl"


def fetch(name: str, per_slot: int = PER_SLOT, force: bool = False) -> List[Dict[str, Any]]:
    """Fetch and cache one set; the cached file wins unless `force`."""
    p = path(name)
    if p.exists() and not force:
        return load(name)
    spec = SETS[name]
    per_slot = min(per_slot, int(spec.get("per_slot", per_slot)))
    rng = random.Random(f"{SEED}/{name}")
    build = BUILDERS[name]
    items: List[Dict[str, Any]] = []
    configs = spec.get("configs") or [spec["config"]]
    rng.shuffle(configs)
    for config in configs:
        # a window per config until the slot is full — MATH needs several,
        # since only Level 5 rows count
        rows = _window(spec["dataset"], config, spec["split"], rng, WINDOW)
        for row in rows:
            item = build(row, len(items))
            if item is not None:
                items.append(item)
            if len(items) >= per_slot:
                break
        if len(items) >= per_slot:
            break
    HOME.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items))
    return items


def load(name: str) -> List[Dict[str, Any]]:
    try:
        return [json.loads(l) for l in path(name).read_text().splitlines() if l.strip()]
    except OSError:
        return []


def available() -> List[str]:
    return [n for n in SETS if path(n).exists()]


def items(names: Optional[Iterable[str]] = None, fetch_missing: bool = True
          ) -> List[Dict[str, Any]]:
    """All cached items (fetching the missing sets when allowed), in set order."""
    out: List[Dict[str, Any]] = []
    for name in names or SETS:
        got = load(name)
        if not got and fetch_missing:
            try:
                got = fetch(name)
            except Exception:                       # noqa: BLE001 — offline, or a set moved
                got = []
        out.extend(got)
    return out


def status() -> Dict[str, Any]:
    return {name: {"items": len(load(name)), "task": s["task"], "difficulty": s["difficulty"],
                   "license": s["license"], "source": s["dataset"],
                   "fetched": int(path(name).stat().st_mtime) if path(name).exists() else None}
            for name, s in SETS.items()}


def attribution() -> str:
    return "; ".join(f"{s['cite']} ({s['license']})" for s in SETS.values())
