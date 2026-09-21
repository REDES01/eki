# SPDX-License-Identifier: Apache-2.0
"""What each model can do: recorded, measured, and used by the router."""
import asyncio

from eki import measure
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from eki.capability import COST_WEIGHT, MIN_ITEMS, SOLID_ITEMS, ModelRecord, Registry
from eki.router import Need, Router


def run(coro):
    return asyncio.run(coro)


# ---- the registry ---------------------------------------------------------

def test_a_listing_keeps_what_was_measured(tmp_path):
    reg = Registry(tmp_path / "eki.db")
    reg.seen("claude", "sonnet", label="Sonnet", klass="frontier_agent_fast")
    reg.record("claude", "sonnet", "math", 0.6, 5, difficulty="easy")
    reg.set_enabled("claude", "sonnet", False)
    # the provider lists it again, with a new label
    again = reg.seen("claude", "sonnet", label="Sonnet 5")
    assert again.label == "Sonnet 5" and again.enabled is False
    assert again.measured["math/easy"]["n"] == 5


def test_measurements_blend_and_only_speak_for_their_difficulty(tmp_path):
    reg = Registry(tmp_path / "eki.db")
    reg.seen("q", "", klass="small_open")
    prior = ModelRecord("q", "", klass="small_open").quality("chat", "hard")
    reg.record("q", "", "chat", 1.0, 5, difficulty="easy")
    rec = reg.get("q", "")
    assert rec.quality("chat", "easy") == 1.0 and rec.basis("chat", "easy") == "measured"
    # five perfect easy answers say nothing about hard chat
    assert rec.quality("chat", "hard") == prior and rec.basis("chat", "hard") == "prior"
    reg.record("q", "", "chat", 0.0, 5, difficulty="easy")
    assert reg.get("q", "").quality("chat", "easy") == 0.5      # item-weighted
    reg.record("q", "", "math", 0.9, MIN_ITEMS - 1, difficulty="easy")
    assert reg.get("q", "").basis("math", "easy") == "prior"     # not enough yet


# ---- the battery ----------------------------------------------------------

def test_checks_by_rule():
    item = lambda c: {"check": c}                       # noqa: E731
    assert measure.check(item({"type": "contains", "any": ["canberra"]}), "It's Canberra.") == 1.0
    assert measure.check(item({"type": "number", "value": 391}), "17 × 23 = 391") == 1.0
    assert measure.check(item({"type": "number", "value": 0.518, "tolerance": 0.002}),
                         "<think>…</think>\n0.5177") == 1.0
    assert measure.check(item({"type": "number", "value": 8}), "eight") == 0.0
    assert measure.check(item({"type": "lines", "min": 5}), "a\nb\nc") == 0.0
    assert measure.check(item({"type": "judge"}), "anything") is None


def test_the_battery_is_well_formed():
    items = measure.load()
    assert len(items) >= 20
    for it in items:
        assert it["task"] in ("chat", "math", "translate", "code", "writing")
        assert it.get("difficulty") in ("easy", "medium", "hard")
        assert it["check"]["type"] in ("contains", "regex", "number", "lines", "judge")
        if it["check"]["type"] == "judge":
            assert it["check"]["judge"]


class Scripted(Backend):
    """Answers from a script; the judge is another instance."""
    answers: dict = {}

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        prompt = messages[-1].content
        if prompt.startswith("You grade"):
            yield "4"
        else:
            yield self.answers.get(prompt, "no idea")


def test_run_scores_by_rule_and_by_judge():
    items = [
        {"task": "chat", "difficulty": "easy", "prompt": "capital?",
         "check": {"type": "contains", "any": ["canberra"]}},
        {"task": "chat", "difficulty": "easy", "prompt": "legs?",
         "check": {"type": "number", "value": 8}},
        {"task": "writing", "difficulty": "easy", "prompt": "haiku",
         "check": {"type": "judge", "judge": "is it good"}},
    ]
    Scripted.answers = {"capital?": "Canberra", "legs?": "six", "haiku": "rain falls"}
    subject = Scripted(BackendInfo(key="s", kind="scripted", label="s"), {})
    judge = lambda: Scripted(BackendInfo(key="j", kind="scripted", label="j"), {})  # noqa: E731

    async def go():
        out = []
        async for piece in measure.run(subject, judge, items):
            out.append(piece)
        return out

    pieces = run(go())
    final = pieces[-1]
    assert final["results"]["chat/easy"] == {"score": 0.5, "n": 2}
    assert final["results"]["writing/easy"] == {"score": 0.75, "n": 1}   # a 4 out of 5

    # without a judge, judged items don't count either way
    pieces = go_without(subject, items)
    assert "writing/easy" not in pieces[-1]["results"] and pieces[-1]["skipped"] == 1


def go_without(subject, items):
    async def go():
        out = []
        async for piece in measure.run(subject, lambda: None, items):
            out.append(piece)
        return out
    return run(go())


# ---- the router choosing a model ------------------------------------------

class Stub(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):          # pragma: no cover
        yield ""


def _backend(key, kind, tier, **caps):
    return Stub(BackendInfo(key=key, kind=kind, label=key, capabilities=Capabilities(**caps),
                            cost=Cost(tier=tier)), {})


def test_router_picks_the_cheapest_adequate_model_behind_a_provider():
    claude = _backend("claude", "claude_code", 50, tools=True, repo=True)
    records = {
        "claude": [ModelRecord("claude", "", klass="frontier_agent_fast"),     # default: sonnet-class
                   ModelRecord("claude", "opus", klass="frontier_agent"),
                   ModelRecord("claude", "sonnet", klass="frontier_agent_fast")],
    }
    router = Router([claude], models_for=lambda k: records.get(k, []))
    # medium work: the default clears the bar, so no model is named
    choice = router.choose(Need(task="repo", difficulty="medium"))
    assert choice.model == "" and choice.backend is claude
    # hard maths: the fast tier's 0.82 misses the 0.88 bar; opus is named
    choice = router.choose(Need(task="math", difficulty="hard"))
    assert choice.model == "opus" and "claude (opus)" in choice.reason


def test_measured_scores_change_the_choice():
    small = _backend("tiny", "mlx", 0)
    big = _backend("claude", "claude_code", 50)
    tiny = ModelRecord("tiny", "", klass="small_open")
    tiny.measured["chat/easy"] = {"score": 1.0, "n": 5}         # proved itself on easy chat
    router = Router([small, big], models_for=lambda k: [tiny] if k == "tiny" else [])
    assert router.choose(Need(task="chat", difficulty="easy")).backend is small
    # but that measurement says nothing about hard chat, where its prior loses
    assert router.choose(Need(task="chat", difficulty="hard")).backend is big


# ---- the public boards ----------------------------------------------------

def test_public_scores_rank_what_the_battery_cannot():
    from eki import public_scores as ps
    fable = ps.lookup("claude_code", "fable")
    opus = ps.lookup("claude_code", "opus")
    sonnet = ps.lookup("claude_code", "sonnet")
    assert fable and opus and sonnet
    assert fable["base"].startswith("claude-fable-") and opus["base"].startswith("claude-opus-")
    hard = lambda r: ps.nearest(r["scores"], "chat", "hard")     # noqa: E731
    assert hard(fable) >= hard(opus) > hard(sonnet)
    # a local build resolves to its base model on the open board — the 4-bit
    # part is what eki's own measurement is for — and prices come along
    small = ps.lookup("mlx", "mlx-community/Qwen3.5-2B-MLX-4bit")
    assert small and small["base"] == "qwen3.5-2b" and "chat/medium" in small["scores"]
    assert ps.lookup("mlx", "mlx-community/Qwen3.5-27B-MLX-4bit")["price"]["out"] > 0
    assert fable["price"]["out"] > opus["price"]["out"] > sonnet["price"]["out"]
    # a build the boards never saw stays unknown, not guessed
    assert ps.lookup("mlx", "someone/Made-Up-7B-4bit") is None
    # a family name resolves to its newest version
    assert ps.resolve("mlx", "qwen3.5-9b") is not None


def test_nearest_prefers_harder_evidence_and_discounts_easier():
    from eki import public_scores as ps
    assert ps.nearest({"math/hard": 0.9}, "math", "easy") == 0.9
    assert ps.nearest({"math/easy": 0.9}, "math", "hard") == 0.81
    assert ps.nearest({"chat/medium": 0.8}, "translate", "medium") == 0.8
    assert ps.nearest({}, "math", "hard") is None


def test_quality_prefers_measured_then_public_then_class():
    rec = ModelRecord("claude", "sonnet", klass="frontier_agent_fast",
                      public={"scores": {"chat/hard": 0.74}})
    assert rec.basis("chat", "hard") == "public" and rec.quality("chat", "hard") == 0.74
    assert rec.basis("image", "hard") == "prior"
    # a few of eki's own items don't outrank a board that ran thousands…
    rec.measured["chat/hard"] = {"score": 0.5, "n": 5}
    assert rec.basis("chat", "hard") == "public"
    # …but a few dozen on this very build do
    rec.measured["chat/hard"] = {"score": 0.5, "n": SOLID_ITEMS}
    assert rec.basis("chat", "hard") == "measured" and rec.quality("chat", "hard") == 0.5


def test_public_prices_become_cost_weights():
    opus = ModelRecord("claude", "opus", klass="frontier_agent",
                       public={"price": {"in": 5.0, "out": 25.0}})
    fable = ModelRecord("claude", "fable", klass="frontier_agent",
                        public={"price": {"in": 10.0, "out": 50.0}})
    sonnet = ModelRecord("claude", "sonnet", klass="frontier_agent_fast",
                         public={"price": {"in": 2.0, "out": 10.0}})
    assert opus.cost == 5.0 and fable.cost == 10.0 and sonnet.cost == 2.0
    # a local build's price is memory and time, not the API's dollars
    local = ModelRecord("qwen", "", klass="large_open", public={"price": {"in": 0.2, "out": 1.5}})
    assert local.cost == COST_WEIGHT["large_open"]
    # and an explicit weight beats everything
    assert ModelRecord("claude", "opus", klass="frontier_agent", cost_weight=1.5,
                       public={"price": {"in": 5.0, "out": 25.0}}).cost == 1.5


def test_router_pays_for_the_stronger_model_only_when_the_bar_needs_it():
    claude = _backend("claude", "claude_code", 50, tools=True, repo=True)
    sonnet = ModelRecord("claude", "", klass="frontier_agent_fast",
                         public={"scores": {"math/hard": 0.85, "chat/medium": 0.8}})
    opus = ModelRecord("claude", "opus", klass="frontier_agent",
                       public={"scores": {"math/hard": 0.978, "chat/medium": 0.97}})
    fable = ModelRecord("claude", "fable", klass="frontier_agent",
                        public={"scores": {"math/hard": 0.981, "chat/medium": 0.937}})
    assert fable.cost > opus.cost > sonnet.cost
    router = Router([claude], models_for=lambda k: [sonnet, opus, fable])
    # the default clears medium chat: no model named
    assert router.choose(Need(task="chat", difficulty="medium")).model == ""
    # hard maths: sonnet's 0.85 misses 0.88; opus and fable both clear, opus is cheaper
    assert router.choose(Need(task="math", difficulty="hard")).model == "opus"
    # raise the bar past opus and fable earns its price
    fable.public["scores"]["math/hard"] = 0.99
    opus.public["scores"]["math/hard"] = 0.87
    assert router.choose(Need(task="math", difficulty="hard")).model == "fable"
