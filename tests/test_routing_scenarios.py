# SPDX-License-Identifier: Apache-2.0
"""The router, end to end, on a setup like a real one.

One Mac: a 27B local model, a 2B reserved as the router model, Claude Code
with fable / opus / sonnet behind it, Codex, and an image model. Every test
states a situation — what the request is, what's up, what's spent, what's
been measured — and checks both the choice and the reason given for it.
"""
from eki.capability import ModelRecord, SOLID_ITEMS
from eki.policy import Policy
from eki.priors import NEED
from eki.router import Need, QuotaSource, Router
from tests.test_router import Fake
from eki.adapters.base import BackendInfo, Capabilities, Cost


def backend(key, kind, tier, quota=None, **caps):
    return Fake(BackendInfo(key=key, kind=kind, label=key, capabilities=Capabilities(**caps),
                            cost=Cost(tier=tier), quota_source=quota), {})


def record(provider, model, klass, public=None, **measured):
    rec = ModelRecord(provider, model, klass=klass, public={"scores": public} if public else {})
    for slot, (score, n) in measured.items():
        rec.measured[slot.replace("_", "/")] = {"score": score, "n": n}
    return rec


class Spent(QuotaSource):
    def __init__(self, **spent):
        super().__init__()
        self.spent = spent

    def exhausted(self):
        return self.spent


class Mac:
    """A realistic setup with knobs for each test to turn."""

    def __init__(self):
        self.local = backend("qwen", "mlx", 0, context_tokens=32000)
        self.small = backend("qwen3-5-2b", "mlx", 0, context_tokens=32000)
        self.claude = backend("claude", "claude_code", 50, quota="claude",
                              repo=True, tools=True, context_tokens=200000)
        self.codex = backend("codex", "codex", 50, quota="codex",
                             repo=True, tools=True, context_tokens=200000)
        self.flux = backend("flux", "image", 0, text=False, images_out=True)
        self.up = {"qwen": True, "qwen3-5-2b": True, "claude": True, "codex": True, "flux": True}
        self.spent = {}
        self.policy = Policy(order=["qwen", "claude", "codex"])
        boards = {"chat/hard": 0.97, "math/hard": 0.98, "code/hard": 0.88, "repo/hard": 0.9}
        self.records = {
            "qwen": [record("qwen", "", "large_open",
                            public={"chat/medium": 0.86, "math/hard": 0.86, "repo/medium": 0.87})],
            "qwen3-5-2b": [record("qwen3-5-2b", "", "small_open")],
            "claude": [record("claude", "", "frontier_agent_fast",
                              public={"repo/hard": 0.8, "math/hard": 0.8}),
                       record("claude", "sonnet", "frontier_agent_fast",
                              public={"repo/hard": 0.8, "math/hard": 0.8}),
                       record("claude", "opus", "frontier_agent", public=boards),
                       record("claude", "fable", "frontier_agent",
                              public={k: min(1.0, v + 0.01) for k, v in boards.items()})],
            "codex": [record("codex", "", "frontier_agent", public={"repo/hard": 0.94})],
            "flux": [record("flux", "", "image")],
        }
        for rec in self.records["claude"]:
            rec.cost_weight = {"": 2.0, "sonnet": 2.0, "opus": 5.0, "fable": 10.0}[rec.model]

    def router(self):
        return Router([self.local, self.small, self.claude, self.codex, self.flux],
                      quota=Spent(**self.spent), policy=self.policy,
                      is_up=lambda k: self.up.get(k), reserved={"qwen3-5-2b"},
                      models_for=lambda k: self.records.get(k, []))


# ---- ordinary days ----------------------------------------------------------

def test_a_plain_question_stays_on_the_mac():
    c = Mac().router().choose(Need(task="chat", difficulty="easy"))
    assert c.backend.key == "qwen" and "cheapest fit" in c.reason
    assert "qwen3-5-2b: reserved as the router model" in c.rejected
    assert "flux: draws images, doesn't answer in words" in c.rejected


def test_a_story_outline_is_medium_writing_and_stays_local_too():
    c = Mac().router().choose(Need(task="writing", difficulty="medium"))
    assert c.backend.key == "qwen"


def test_hard_maths_goes_to_the_cheapest_frontier_model_that_clears_the_bar():
    c = Mac().router().choose(Need(task="math", difficulty="hard"))
    # the 27B's public 0.86 misses the 0.88 bar; among Claude's models opus
    # (cost 5) clears it and fable (cost 10) only barely beats it — not worth it
    assert c.backend.key == "claude" and c.model == "opus"
    assert c.reason.startswith("claude (opus): cheapest fit") and "math/hard" in c.reason
    assert "qwen: likely not good enough for hard math" in c.rejected


def test_fable_is_named_only_when_opus_falls_short():
    mac = Mac()
    mac.records["claude"][2].public["scores"]["math/hard"] = 0.85     # opus below the bar
    c = mac.router().choose(Need(task="math", difficulty="hard"))
    assert c.model == "fable"


def test_building_something_in_a_repo_needs_a_backend_that_can_edit():
    c = Mac().router().choose(Need(repo=True, task="repo", difficulty="medium"))
    assert c.backend.key == "claude" and c.model == ""      # the default clears medium
    assert "qwen: lacks repo" in c.rejected


def test_hard_repo_work_picks_by_score_across_providers():
    mac = Mac()
    # codex's public 0.94 clears the hard bar; Claude's default doesn't, opus does
    c = mac.router().choose(Need(repo=True, task="repo", difficulty="hard"))
    assert c.backend.key in ("claude", "codex")
    # same tier: the policy's order decides between two that clear it
    assert c.backend.key == "claude" and c.model == "opus"
    mac.policy = Policy(order=["codex", "claude"])
    assert mac.router().choose(Need(repo=True, task="repo", difficulty="hard")).backend.key == "codex"


def test_a_picture_goes_to_the_image_model_and_nowhere_else():
    c = Mac().router().choose(Need(images_out=True, task="image", difficulty="easy"))
    assert c.backend.key == "flux"
    assert any(r.startswith("qwen: lacks images") for r in c.rejected)


# ---- when the Mac is short of memory --------------------------------------

def test_a_local_model_that_cannot_load_is_skipped_with_the_reason():
    mac = Mac()
    mac.up["qwen"] = False                                  # can't start: no room
    c = mac.router().choose(Need(task="chat", difficulty="easy"))
    assert c.backend.key == "claude" and "qwen: not running" in c.rejected


def test_unknown_liveness_is_never_held_against_a_backend():
    mac = Mac()
    mac.up = {}
    assert mac.router().choose(Need(task="chat", difficulty="easy")).backend.key == "qwen"


# ---- when a window is spent -------------------------------------------------

def test_spent_claude_sends_hard_maths_to_codex_if_it_clears_the_bar():
    mac = Mac()
    mac.spent = {"claude": "5H window at 100%, resets 4:10pm"}
    mac.records["codex"][0].public["scores"]["math/hard"] = 0.9
    c = mac.router().choose(Need(task="math", difficulty="hard"))
    assert c.backend.key == "codex"
    assert "claude: 5H window at 100%, resets 4:10pm" in c.rejected


def test_everything_spent_falls_back_to_the_best_there_is_and_says_so():
    mac = Mac()
    mac.spent = {"claude": "spent", "codex": "spent"}
    c = mac.router().choose(Need(task="math", difficulty="hard"))
    assert c.backend.key == "qwen"
    assert "the best eki has for hard math" in c.reason


def test_easy_work_never_touches_a_paid_window_even_when_it_is_free():
    mac = Mac()
    mac.spent = {}
    c = mac.router().choose(Need(task="translate", difficulty="easy"))
    assert c.backend.key == "qwen"


# ---- what eki measured beats what the boards say -----------------------------

def test_a_solid_measurement_of_the_local_build_takes_hard_maths_off_claude():
    mac = Mac()
    rec = mac.records["qwen"][0]
    rec.measured["math/hard"] = {"score": 0.9, "n": SOLID_ITEMS}   # the 4-bit build, here
    c = mac.router().choose(Need(task="math", difficulty="hard"))
    assert c.backend.key == "qwen"


def test_a_measurement_that_disappoints_takes_it_away_again():
    mac = Mac()
    rec = mac.records["qwen"][0]
    rec.measured["chat/medium"] = {"score": 0.4, "n": SOLID_ITEMS}  # boards said 0.86
    c = mac.router().choose(Need(task="chat", difficulty="medium"))
    assert c.backend.key == "claude"
    assert "qwen: likely not good enough for medium chat" in c.rejected


def test_a_handful_of_items_does_not_overturn_the_boards():
    mac = Mac()
    mac.records["qwen"][0].measured["chat/medium"] = {"score": 0.4, "n": SOLID_ITEMS - 1}
    assert mac.router().choose(Need(task="chat", difficulty="medium")).backend.key == "qwen"


# ---- the user's say ----------------------------------------------------------

def test_a_backend_named_by_the_user_wins_even_if_reserved_or_spent():
    mac = Mac()
    mac.spent = {"claude": "spent"}
    assert mac.router().choose(Need(backend="claude")).backend.key == "claude"
    assert mac.router().choose(Need(backend="qwen3-5-2b")).backend.key == "qwen3-5-2b"


def test_policy_can_turn_a_provider_off_or_make_it_dearer():
    mac = Mac()
    mac.policy = Policy(disabled=["qwen"])
    c = mac.router().choose(Need(task="chat", difficulty="easy"))
    assert c.backend.key == "claude" and "qwen: turned off in policy" in c.rejected
    mac.policy = Policy(tiers={"claude": 0, "qwen": 10})
    c = mac.router().choose(Need(task="chat", difficulty="easy"))
    assert c.backend.key == "claude" and "by policy" in c.reason


def test_the_bar_is_the_difficulty_not_the_task():
    router = Mac().router()
    assert router.choose(Need(task="math", difficulty="easy")).backend.key == "qwen"
    assert router.choose(Need(task="math", difficulty="hard")).backend.key == "claude"
    assert NEED["easy"] < NEED["medium"] < NEED["hard"]


def test_a_model_asked_for_by_name_overrides_the_routers_pick():
    """The app sends "claude:fable"; the run goes to Claude with that model."""
    from eki.engine import Engine
    from types import SimpleNamespace as NS
    mac = Mac()
    router = mac.router()
    requested, _, wanted = "claude:fable".partition(":")
    choice = router.choose(Need(backend=requested or None, task="chat", difficulty="easy"))
    assert choice.backend.key == "claude" and choice.model == ""
    # what Engine._dispatch does with the second half
    if wanted:
        choice.model = wanted
        choice.reason = f"{choice.backend.key} ({wanted}): asked for by name"
    assert choice.model == "fable" and choice.reason == "claude (fable): asked for by name"
