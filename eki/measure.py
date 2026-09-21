# SPDX-License-Identifier: Apache-2.0
"""Find out what a model can do, instead of assuming from its size.

Items from the public benchmarks (see bench.py) and a small hand battery
are put to one (provider, model) and scored. Answers with a right answer are
checked by rule — a number, a boxed expression, a letter, a test suite;
answers that are matters of quality are graded 1–5 by a local judge model
against a one-line rubric, and skipped when no local judge is up, so a paid
model is never spent on grading.

Scores land in the registry per (kind of work, difficulty), alongside how
fast the model answered. Because every model sees the same items through
the same adapters, a 4-bit local build and a frontier model land on one
scale — which no public board can offer.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from . import bench
from .adapters.base import Backend, Message
from .classify import TASKS

BATTERY = Path(__file__).with_name("evals") / "capability.jsonl"
ITEM_TIMEOUT = 150.0
#: a hard item gets longer: a thinking model at 15 tokens/s needs it
LONG_TIMEOUT = 420.0
TEST_TIMEOUT = 15.0
JUDGE_PROMPT = ("You grade an answer to a task. Reply with a single digit from 1 to 5 and "
                "nothing else.\n\nTask given to the model:\n{prompt}\n\nThe model's "
                "answer:\n{answer}\n\nGrading rubric: {rubric}")


def _slot(item: Dict[str, Any]) -> str:
    return f"{item['task']}/{item.get('difficulty', 'easy')}"


def load(path: Path = BATTERY) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def all_items(fetch_missing: bool = True) -> List[Dict[str, Any]]:
    """The public items first (fetched on first use), then the hand battery."""
    return bench.items(fetch_missing=fetch_missing) + load()


def _normalize_math(s: str) -> str:
    s = s.strip().strip("$").strip()
    s = re.sub(r"\\(left|right|!|,|;|text|mathrm|displaystyle)\b", "", s)
    s = s.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    s = s.replace("^{\\circ}", "").replace("^\\circ", "").replace("\\%", "")
    s = re.sub(r"\\text\{[^}]*\}", "", s)
    s = s.replace(" ", "").rstrip(".")
    m = re.fullmatch(r"\\frac\{?(-?\d+)\}?\{?(\d+)\}?", s)
    if m:
        s = f"{m.group(1)}/{m.group(2)}"
    return s


def _same_math(a: str, b: str) -> bool:
    na, nb = _normalize_math(a), _normalize_math(b)
    if na == nb:
        return True
    try:
        return abs(float(na) - float(nb)) < 1e-6
    except ValueError:
        pass
    try:
        from fractions import Fraction
        return Fraction(na) == Fraction(nb)
    except (ValueError, ZeroDivisionError):
        return False


def _code_block(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return blocks[-1] if blocks else text


def _limits() -> None:
    # the model's code runs here: cap its cpu and memory, drop its env
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    except (ValueError, OSError):
        pass


def run_tests(code: str, setup: str, tests: str) -> bool:
    """True when the model's code passes the item's tests, in a scratch dir,
    with no environment, a cpu cap and a wall clock — the usual HumanEval
    procedure, not a sandbox against a hostile author."""
    program = f"{setup}\n\n{code}\n\n{tests}\n" if setup and setup not in code else f"{code}\n\n{tests}\n"
    with tempfile.TemporaryDirectory() as d:
        try:
            done = subprocess.run([sys.executable, "-I", "-"], input=program, cwd=d,
                                  capture_output=True, text=True, timeout=TEST_TIMEOUT,
                                  env={"PATH": os.environ.get("PATH", "")}, preexec_fn=_limits)
        except (subprocess.TimeoutExpired, OSError):
            return False
    return done.returncode == 0


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
    if kind == "boxed":
        got = bench.last_boxed(text)
        if not got:
            m = re.findall(r"[Aa]nswer\s*[:：]\s*(.+)", text)
            got = m[-1].strip() if m else ""
        return 1.0 if got and _same_math(got, str(c["value"])) else 0.0
    if kind == "letter":
        m = re.findall(r"[Aa]nswer\s*[:：]?\s*\**\(?([A-J])\)?\b", text)
        got = m[-1] if m else ""
        if not got:
            tail = re.findall(r"\b([A-J])\b", text[-200:])
            got = tail[-1] if tail else ""
        return 1.0 if got == str(c["value"]).upper() else 0.0
    if kind == "tests":
        return 1.0 if run_tests(_code_block(text), c.get("setup", ""), c["tests"]) else 0.0
    return None                                     # "judge": rule can't say


def judged(grade: str) -> Optional[float]:
    m = re.search(r"[1-5]", grade)
    return (int(m.group(0)) - 1) / 4.0 if m else None


async def _answer(backend: Backend, prompt: str, max_tokens: int = 400,
                  timeout: float = ITEM_TIMEOUT) -> str:
    parts: List[str] = []

    async def collect() -> None:
        async for chunk in backend.stream([Message("user", prompt)],
                                          max_tokens=max_tokens, temperature=0.0):
            parts.append(chunk)

    await asyncio.wait_for(collect(), timeout=timeout)
    return "".join(parts)


async def run(backend: Backend, judge: Optional[Callable[[], Optional[Backend]]] = None,
              items: Optional[List[Dict[str, Any]]] = None
              ) -> AsyncIterator[Any]:
    """Yield progress lines, then one final dict of results.

    `judge` returns a fresh local backend to grade with, or None. It's
    called per item so a judge that went away mid-run is noticed.
    """
    items = items if items is not None else all_items()
    by_task: Dict[str, List[float]] = {}
    tokens, seconds = 0, 0.0
    skipped = 0
    # slot by slot, so a slot's score is reported the moment it's complete
    # and survives a run cut short
    order = {s: n for n, s in enumerate(dict.fromkeys(_slot(it) for it in items))}
    items = sorted(items, key=lambda it: order[_slot(it)])
    reported: Dict[str, int] = {}

    def slot_done(i: int) -> Optional[Dict[str, Any]]:
        here = _slot(items[i - 1])
        if i < len(items) and _slot(items[i]) == here:
            return None
        got = by_task.get(here) or []
        if not got or reported.get(here) == len(got):
            return None
        reported[here] = len(got)
        return {"slot": here, "score": round(sum(got) / len(got), 3), "n": len(got)}

    for i, item in enumerate(items, 1):
        started = time.time()
        budget = int(item["check"].get("max_tokens", 400))
        try:
            answer = await _answer(backend, item["prompt"], budget,
                                   LONG_TIMEOUT if budget > 1000 else ITEM_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # noqa: BLE001
            yield f"  {i}/{len(items)} {_slot(item)}: failed ({str(e)[:60]})\n"
            by_task.setdefault(_slot(item), []).append(0.0)
            if (done := slot_done(i)):
                yield done
            continue
        took = time.time() - started
        usage = getattr(backend, "last_usage", {}) or {}
        out_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        if out_tokens:
            tokens += int(out_tokens)
            seconds += took
        score = await asyncio.get_running_loop().run_in_executor(None, check, item, answer)
        rubric = item["check"].get("judge")
        if rubric and (score is None or score > 0):
            # the rule says it's shaped right; only a judge says whether it's
            # good — without one, the item doesn't count either way
            grader = judge() if judge else None
            if grader is None:
                skipped += 1
                yield f"  {i}/{len(items)} {item['task']}: needs a judge, none up — skipped\n"
                if (done := slot_done(i)):
                    yield done
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
                    if (done := slot_done(i)):
                        yield done
                    continue
        by_task.setdefault(_slot(item), []).append(float(score))
        yield f"  {i}/{len(items)} {_slot(item)}: {score:.2f} in {took:.1f}s\n"
        if (done := slot_done(i)):
            yield done
    results = {t: {"score": round(sum(v) / len(v), 3), "n": len(v)}
               for t, v in by_task.items() if v}
    yield {"results": results, "skipped": skipped,
           "tok_s": round(tokens / seconds, 1) if seconds and tokens else None}


def summary(results: Dict[str, Dict[str, Any]]) -> str:
    bits = [f"{slot} {r['score']:.2f} ({r['n']})" for slot, r in results.items()
            if slot.split("/")[0] in TASKS or slot.startswith("image/")]
    return ", ".join(bits) if bits else "nothing measured"
