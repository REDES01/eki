# SPDX-License-Identifier: Apache-2.0
"""Labelling a request, and what the label does to routing."""
import asyncio
import json

import httpx
import pytest

from eki import classify, priors
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, register
from eki.router import Need, Router


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("prompt,task", [
    ("say hi in three words", "chat"),
    ("draw a cat wearing a spacesuit", "image"),
    ("translate to French: I'll be ten minutes late", "translate"),
    ("prove that the square root of 2 is irrational", "math"),
    ("write a python function that reverses a string", "code"),
    ("bump the version number in package.json to 1.4.0", "repo"),
    ("what's the latest version of python", "research"),
    ("write a polite email declining a meeting invite", "writing"),
])
def test_rules_read_the_obvious_cases(prompt, task):
    assert classify.rules(prompt).task == task


def test_a_folder_settles_the_question():
    # whatever it reads like, work with a folder is work on a repo
    assert classify.rules("say hi", has_folder=True).task == "repo"


def test_difficulty_follows_wording_before_length():
    assert classify.rules("hi").difficulty == "easy"
    assert classify.rules("prove the Cauchy-Schwarz inequality").difficulty == "hard"
    assert classify.rules("what is 17 times 23").difficulty == "easy"


def test_parse_only_accepts_known_labels():
    assert classify.parse('{"task": "code", "difficulty": "hard"}').task == "code"
    assert classify.parse('here you go: {"task":"math","difficulty":"easy"} ok').task == "math"
    assert classify.parse('{"task": "banana", "difficulty": "easy"}') is None
    assert classify.parse("no json here") is None


def test_model_label_falls_back_and_stands_down(monkeypatch):
    real = httpx.AsyncClient

    def broken(*a, **kw):
        kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(500, text="nope"))
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", broken)
    model = classify.ModelClassifier("http://127.0.0.1:1")
    clf = classify.Classifier(model)
    label = run(clf.label("write a haiku about autumn"))
    assert label.source == "rules" and label.task == "writing"
    assert model.misses == 1
    model.misses = 3                      # after three misses it isn't asked again
    called = []
    monkeypatch.setattr(model, "label", lambda p: called.append(p))
    assert run(clf.label("hello")).source == "rules"
    assert called == []


def test_model_label_is_used_when_it_answers(monkeypatch):
    real = httpx.AsyncClient

    def ok(*a, **kw):
        def handler(request):
            body = json.loads(request.content)
            assert body["temperature"] == 0.0
            return httpx.Response(200, json={"choices": [{"message": {
                "content": '{"task": "research", "difficulty": "hard"}'}}]})
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", ok)
    clf = classify.Classifier(classify.ModelClassifier("http://127.0.0.1:1"))
    label = run(clf.label("what happened this week"))
    assert (label.task, label.difficulty, label.source) == ("research", "hard", "model")


# ---- what the label does to routing ---------------------------------------

@register("stub")
class Stub(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):        # pragma: no cover - never run
        yield ""


def backend(key, kind, tier, options=None, **caps):
    b = Stub(BackendInfo(key=key, kind=kind, label=key,
                         capabilities=Capabilities(**caps), cost=Cost(tier=tier)),
             options or {})
    return b


def test_easy_work_stays_local_and_hard_work_does_not():
    local = backend("qwen", "mlx", 0, {"model": "Qwen3.8-27B-4bit"})
    cli = backend("claude", "claude_code", 50, {}, tools=True, repo=True)
    router = Router([local, cli])
    assert router.choose(Need(task="chat", difficulty="easy")).backend is local
    assert router.choose(Need(task="chat", difficulty="hard")).backend is cli
    # a 27B model is believed to be fine at everyday writing
    assert router.choose(Need(task="writing", difficulty="medium")).backend is local
    # but not at hard maths
    assert router.choose(Need(task="math", difficulty="hard")).backend is cli


def test_with_nothing_good_enough_the_best_available_wins():
    small = backend("tiny", "mlx", 0, {"model": "Qwen3.5-2B-4bit"})
    router = Router([small])
    choice = router.choose(Need(task="math", difficulty="hard"))
    assert choice.backend is small and "best eki has" in choice.reason


def test_the_router_model_is_not_used_for_answers():
    small = backend("router", "mlx", 0, {"model": "Qwen3.5-2B-4bit"})
    big = backend("qwen", "mlx", 0, {"model": "Qwen3.8-27B-4bit"})
    router = Router([small, big], reserved={"router"})
    assert router.choose(Need(task="chat", difficulty="easy")).backend is big
    # still reachable when asked for by name
    assert router.choose(Need(backend="router")).backend is small


def test_class_of_reads_size_and_address():
    assert priors.class_of("claude_code", {}) == "frontier_agent"
    assert priors.class_of("anthropic_api", {}) == "frontier_api"
    assert priors.class_of("openai_compat",
                           {"base_url": "https://api.openai.com/v1"}) == "frontier_api"
    assert priors.class_of("mlx", {"model": "mlx-community/Qwen3.8-27B-4bit"}) == "large_open"
    assert priors.class_of("mlx", {"model": "mlx-community/Qwen3.5-2B-MLX-4bit"}) == "small_open"
    assert priors.class_of("openai_compat",
                           {"base_url": "http://127.0.0.1:1234/v1",
                            "model": "gemma-3-12b-it"}) == "mid_open"


def test_seed_set_labels_are_all_known():
    from eki.evals.label_eval import load
    rows = load()
    assert len(rows) > 150
    assert {r["task"] for r in rows} <= set(classify.TASKS)
    assert {r["difficulty"] for r in rows} <= set(classify.DIFFICULTIES)


def test_rules_beat_chance_on_the_seed_set():
    from eki.evals.label_eval import load, score
    rows = load()
    got = [classify.rules(r["p"]) for r in rows]
    # tuned on this set, so this is a regression guard, not a claim of accuracy
    assert score(rows, got)["task"] >= 0.85
