# SPDX-License-Identifier: Apache-2.0
"""Find out what a model can do, instead of assuming from its size.

A small battery of tasks with checkable answers — capitals, arithmetic,
translations, a haiku — is put to one (provider, model) and scored. Answers
with a right answer are checked by rule; answers that are matters of quality
are graded 1–5 by a local judge model against a one-line rubric, and skipped
when no local judge is up, so a paid model is never spent on grading.

Scores land in the registry per kind of work, alongside how fast the model
answered. They are small numbers of small tasks: enough to tell a 2B model
from a 27B one and to catch a broken setup, not a leaderboard. Real use
refines them later.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .adapters.base import Backend, Message
from .classify import TASKS

BATTERY = Path(__file__).with_name("evals") / "capability.jsonl"
ITEM_TIMEOUT = 150.0
JUDGE_PROMPT = ("You grade an answer to a task. Reply with a single digit from 1 to 5 and "
                "nothing else.\n\nTask given to the model:\n{prompt}\n\nThe model's "
                "answer:\n{answer}\n\nGrading rubric: {rubric}")


def _slot(item: Dict[str, Any]) -> str:
    return f"{item['task']}/{item.get('difficulty', 'easy')}"


def load(path: Path = BATTERY) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _last_number(text: str) -> Optional[float]:
    """The answer is the last number said: "17 × 23 = 391" means 391."""
    found = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(found[-1]) if found else None


def check(item: Dict[str, Any], answer: str) -> Optional[float]:
    """0..1 by rule, or None when only a judge can say."""
    c = item["check"]
    kind = c["type"]
    text = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
    low = text.lower()
    if kind == "contains":
        return 1.0 if any(a.lower() in low for a in c["any"]) else 0.0
    if kind == "regex":
        return 1.0 if re.search(c["pattern"], text, re.M) else 0.0
    if kind == "number":
        got = _last_number(text)
        if got is None:
            return 0.0
        return 1.0 if abs(got - float(c["value"])) <= float(c.get("tolerance", 1e-6)) else 0.0
    if kind == "lines":
        n = sum(1 for l in text.splitlines() if l.strip())
        return 1.0 if n >= int(c.get("min", 1)) else 0.0
    return None                                     # "judge": rule can't say


def judged(grade: str) -> Optional[float]:
    m = re.search(r"[1-5]", grade)
    return (int(m.group(0)) - 1) / 4.0 if m else None


async def _answer(backend: Backend, prompt: str, max_tokens: int = 400) -> str:
    parts: List[str] = []

    async def collect() -> None:
        async for chunk in backend.stream([Message("user", prompt)],
                                          max_tokens=max_tokens, temperature=0.0):
            parts.append(chunk)

    await asyncio.wait_for(collect(), timeout=ITEM_TIMEOUT)
    return "".join(parts)


async def run(backend: Backend, judge: Optional[Callable[[], Optional[Backend]]] = None,
              items: Optional[List[Dict[str, Any]]] = None
              ) -> AsyncIterator[Any]:
    """Yield progress lines, then one final dict of results.

    `judge` returns a fresh local backend to grade with, or None. It's
    called per item so a judge that went away mid-run is noticed.
    """
    items = items if items is not None else load()
    by_task: Dict[str, List[float]] = {}
    tokens, seconds = 0, 0.0
    skipped = 0
    for i, item in enumerate(items, 1):
        started = time.time()
        try:
            answer = await _answer(backend, item["prompt"])
        except Exception as e:                      # noqa: BLE001
            yield f"  {i}/{len(items)} {item['task']}: failed ({str(e)[:60]})\n"
            by_task.setdefault(_slot(item), []).append(0.0)
            continue
        took = time.time() - started
        usage = getattr(backend, "last_usage", {}) or {}
        out_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        if out_tokens:
            tokens += int(out_tokens)
            seconds += took
        score = check(item, answer)
        rubric = item["check"].get("judge")
        if rubric and (score is None or score > 0):
            # the rule says it's shaped right; only a judge says whether it's
            # good — without one, the item doesn't count either way
            grader = judge() if judge else None
            if grader is None:
                skipped += 1
                yield f"  {i}/{len(items)} {item['task']}: needs a judge, none up — skipped\n"
                continue
            else:
                try:
                    grade = await _answer(grader, JUDGE_PROMPT.format(
                        prompt=item["prompt"], answer=answer[:1500], rubric=rubric), 8)
                    g = judged(grade)
                    if g is not None:
                        score = g if score is None else min(score, g) if score == 0 else g
                except Exception:                   # noqa: BLE001
                    pass
                finally:
                    try:
                        await grader.close()
                    except Exception:               # noqa: BLE001
                        pass
                if score is None:
                    skipped += 1
                    yield f"  {i}/{len(items)} {item['task']}: judge gave no grade — skipped\n"
                    continue
        by_task.setdefault(_slot(item), []).append(float(score))
        yield f"  {i}/{len(items)} {_slot(item)}: {score:.2f} in {took:.1f}s\n"
    results = {t: {"score": round(sum(v) / len(v), 3), "n": len(v)}
               for t, v in by_task.items() if v}
    yield {"results": results, "skipped": skipped,
           "tok_s": round(tokens / seconds, 1) if seconds and tokens else None}


def summary(results: Dict[str, Dict[str, Any]]) -> str:
    bits = [f"{slot} {r['score']:.2f} ({r['n']})" for slot, r in results.items()
            if slot.split("/")[0] in TASKS]
    return ", ".join(bits) if bits else "nothing measured"
