# SPDX-License-Identifier: Apache-2.0
"""The public benchmark items: built from dataset rows, checked by rule."""
import json

from eki import bench, measure


# ---- rows → items ----------------------------------------------------------

def test_gsm8k_rows_become_number_items():
    item = bench._gsm8k({"question": "Janet has 16 eggs…", "answer": "16 - 3 = 13\n#### 1,300"}, 0)
    assert item["task"] == "math" and item["difficulty"] == "easy"
    assert item["check"] == {"type": "number", "value": 1300.0, "max_tokens": 1500}
    assert "Answer:" in item["prompt"]


def test_only_level_5_math_counts_and_the_boxed_answer_is_the_gold():
    row = {"problem": "Find x.", "level": "Level 5", "type": "Algebra",
           "solution": "So $x = \\boxed{\\frac{1}{2}}$ and we are done."}
    item = bench._math(row, 3)
    assert item["check"] == {"type": "boxed", "value": "\\frac{1}{2}", "max_tokens": 4000}
    assert bench._math({**row, "level": "Level 2"}, 0) is None


def test_last_boxed_balances_braces():
    assert bench.last_boxed("\\boxed{\\frac{a}{b}} then \\boxed{x^{2}}") == "x^{2}"
    assert bench.last_boxed("no box") == ""


def test_code_rows_carry_their_tests():
    mbpp = bench._mbpp({"prompt": "Write a function to add.", "test_imports": [],
                        "test_list": ["assert add(1, 2) == 3", "assert add(0, 0) == 0"]}, 0)
    assert "Name the function `add`" in mbpp["prompt"]
    assert mbpp["check"]["type"] == "tests" and "add(1, 2)" in mbpp["check"]["tests"]
    he = bench._humaneval({"prompt": "def inc(x):\n    \"\"\"x plus one\"\"\"\n",
                           "test": "def check(c):\n    assert c(1) == 2\n", "entry_point": "inc"}, 0)
    assert he["check"]["tests"].endswith("check(inc)\n")


def test_choice_and_trivia_rows():
    mm = bench._mmlu_pro({"question": "Which?", "options": ["a", "b", "c"], "answer": "B"}, 0)
    assert "B. b" in mm["prompt"] and mm["check"] == {"type": "letter", "value": "B",
                                                        "max_tokens": 2500}
    assert bench._mmlu_pro({"question": "?", "options": [], "answer": "A"}, 0) is None
    tq = bench._triviaqa({"question": "Who?", "answer": {"normalized_aliases": ["david seville", "x"]}}, 0)
    assert tq["check"]["any"] == ["david seville"]


# ---- checking by rule -------------------------------------------------------

def _check(kind, value, answer, **extra):
    return measure.check({"check": {"type": kind, "value": value, **extra}}, answer)


def test_boxed_answers_are_compared_as_maths_not_strings():
    assert _check("boxed", "\\frac{1}{2}", "so the answer is $\\boxed{\\dfrac{1}{2}}$.") == 1.0
    assert _check("boxed", "\\frac{1}{2}", "Answer: 0.5") == 1.0
    assert _check("boxed", "12", "\\boxed{12}") == 1.0
    assert _check("boxed", "12", "\\boxed{13}") == 0.0
    assert _check("boxed", "12", "I think it's 12") == 0.0     # no answer given, no credit
    assert _check("boxed", "90^\\circ", "Answer: 90") == 1.0


def test_letter_answers_take_the_last_one_said():
    assert _check("letter", "B", "A looks right… no, Answer: B") == 1.0
    assert _check("letter", "B", "The answer is **(B)**") == 1.0
    assert _check("letter", "B", "Answer: C") == 0.0
    assert _check("letter", "B", "I cannot tell") == 0.0


def test_tests_run_the_models_code():
    good = "Here you go:\n```python\ndef add(a, b):\n    return a + b\n```"
    tests = "assert add(1, 2) == 3\nassert add(0, 0) == 0"
    assert measure.check({"check": {"type": "tests", "tests": tests}}, good) == 1.0
    bad = "```python\ndef add(a, b):\n    return a - b\n```"
    assert measure.check({"check": {"type": "tests", "tests": tests}}, bad) == 0.0
    forever = "```python\ndef add(a, b):\n    while True: pass\n```"
    measure.TEST_TIMEOUT = 2.0
    assert measure.check({"check": {"type": "tests", "tests": tests}}, forever) == 0.0


def test_a_humaneval_body_or_a_whole_function_both_run():
    setup = "def inc(x):\n    \"\"\"x plus one\"\"\"\n"
    tests = "def check(c):\n    assert c(1) == 2\ncheck(inc)\n"
    whole = "```python\ndef inc(x):\n    return x + 1\n```"
    body = "```python\n    return x + 1\n```"
    assert measure.run_tests(measure._code_block(whole), setup, tests)
    assert measure.run_tests(measure._code_block(body), setup, tests)


# ---- the cache -------------------------------------------------------------

def test_items_come_from_the_cache_and_never_fetch_when_told_not_to(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "HOME", tmp_path)
    assert bench.items(fetch_missing=False) == []
    item = bench._gsm8k({"question": "q", "answer": "#### 4"}, 0)
    (tmp_path / "gsm8k.jsonl").write_text(json.dumps(item) + "\n")
    got = bench.items(["gsm8k"], fetch_missing=False)
    assert got == [item] and bench.available() == ["gsm8k"]
    assert bench.status()["gsm8k"]["items"] == 1 and bench.status()["math"]["items"] == 0
    # the whole battery is the public items first, then the hand ones
    all_ = measure.all_items(fetch_missing=False)
    assert all_[0] == item and len(all_) > 1


def test_fetch_samples_a_seeded_window(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "HOME", tmp_path)
    rows = [{"question": f"q{i}", "answer": f"#### {i}"} for i in range(500)]
    calls = []

    def fake_total(dataset, config, split):
        return len(rows)

    def fake_rows(dataset, config, split, offset, length):
        calls.append((offset, length))
        return rows[offset:offset + length]

    monkeypatch.setattr(bench, "_total", fake_total)
    monkeypatch.setattr(bench, "_rows", fake_rows)
    first = bench.fetch("gsm8k", per_slot=5)
    assert len(first) == 5 and len(calls) == 1 and calls[0][1] == bench.WINDOW
    # cached: no second fetch; forced: the same seed gives the same sample
    assert bench.fetch("gsm8k", per_slot=5) == first and len(calls) == 1
    assert bench.fetch("gsm8k", per_slot=5, force=True) == first


# ---- measuring on its own -------------------------------------------------

def test_auto_measure_waits_for_the_right_moment(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from eki import settings
    from eki.capability import SOLID_ITEMS, Registry
    from eki.engine import Engine
    from eki.quota.base import Reading, Window

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    reg = Registry(tmp_path / "eki.db")
    reg.seen("qwen", "", klass="large_open")
    reg.seen("claude", "", klass="frontier_agent")
    reg.seen("codex", "", klass="frontier_agent")
    local = NS(key="qwen", running=False)
    fake = NS(
        backends=[NS(info=NS(key=k)) for k in ("qwen", "claude", "codex")],
        registry=reg,
        models=NS(for_backend=lambda k: local if k == "qwen" else None,
                  can_start=lambda k: True),
        quota=NS(latest={"claude": Reading("claude", [Window("five_hour", "5H", 0.6)]),
                         "codex": Reading("codex", [Window("five_hour", "5H", 0.1)])}),
        runner=NS(running=[]),
        AUTO_DONE=tmp_path / "done.json", AUTO_REPEAT_DAYS=Engine.AUTO_REPEAT_DAYS,
        AUTO_QUOTA=Engine.AUTO_QUOTA,
    )
    fake._auto_done = lambda: Engine._auto_done(fake)
    fake._quota_idle = lambda k: Engine._quota_idle(fake, k)

    due = Engine.auto_measure_due(fake)
    by = {d["provider"]: d["hold"] for d in due}
    assert by["qwen"] == ""                              # local, memory ok → go
    assert "only local" in by["claude"] and "only local" in by["codex"]

    settings.save({"auto_measure": "all"})
    by = {d["provider"]: d["hold"] for d in Engine.auto_measure_due(fake)}
    assert "5H at 60%" in by["claude"]                   # a busy window waits
    assert by["codex"] == ""                             # a quiet one goes

    fake.models.can_start = lambda k: False
    by = {d["provider"]: d["hold"] for d in Engine.auto_measure_due(fake)}
    assert "memory" in by["qwen"]

    # once measured solidly, or once attempted, it's not due again
    reg.record("codex", "", "math", 0.9, SOLID_ITEMS, difficulty="easy")
    Engine._auto_mark(fake, "claude", "", "started")
    assert {d["provider"] for d in Engine.auto_measure_due(fake)} == {"qwen"}

    settings.save({"auto_measure": "off"})
    assert Engine.auto_measure_due(fake) == []


def test_each_slot_is_reported_the_moment_it_completes():
    import asyncio
    from tests.test_capability import Scripted
    from eki.adapters.base import BackendInfo
    items = [
        {"task": "math", "difficulty": "easy", "prompt": "a", "check": {"type": "number", "value": 1}},
        {"task": "chat", "difficulty": "easy", "prompt": "b", "check": {"type": "contains", "any": ["x"]}},
        {"task": "math", "difficulty": "easy", "prompt": "c", "check": {"type": "number", "value": 2}},
    ]
    Scripted.answers = {"a": "1", "b": "y", "c": "3"}
    subject = Scripted(BackendInfo(key="s", kind="scripted", label="s"), {})

    async def go():
        return [p async for p in measure.run(subject, None, items)]

    pieces = asyncio.run(go())
    slots = [p for p in pieces if isinstance(p, dict) and "slot" in p]
    # regrouped by slot: math's two items finish together, before chat's
    assert slots == [{"slot": "math/easy", "score": 0.5, "n": 2},
                     {"slot": "chat/easy", "score": 0.0, "n": 1}]
    assert pieces[-1]["results"]["math/easy"] == {"score": 0.5, "n": 2}
