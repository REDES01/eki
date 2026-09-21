"""Routing rules added once the cheap answer stopped being the right one.

Two of these are regression tests for things the router actually did: it sent
"say hi in three words" to an image model because that model was tier 0, and
it happily picked a local server that wasn't running.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from hub.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from hub.policy import Policy
from hub.policy import load as load_policy
from hub.policy import save as save_policy
from hub.router import Need, QuotaSource, Router


class Fake(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield "x"


def make(key, tier, **caps):
    return Fake(BackendInfo(key=key, kind="fake", label=key,
                            capabilities=Capabilities(**caps),
                            cost=Cost(tier=tier)), {})


class NoQuota(QuotaSource):
    def exhausted(self):
        return {}


LOCAL = make("qwen", 0, context_tokens=32000)
IMAGE = make("flux", 0, text=False, images_out=True, context_tokens=512)
CLOUD = make("claude", 50, repo=True, tools=True, context_tokens=200000)


def test_an_image_model_does_not_answer_a_question():
    r = Router([IMAGE, LOCAL, CLOUD], NoQuota())
    choice = r.choose(Need())
    assert choice.backend is LOCAL
    assert any("draws images" in line for line in choice.rejected)


def test_an_image_request_goes_to_the_image_model():
    r = Router([LOCAL, IMAGE, CLOUD], NoQuota())
    choice = r.choose(Need(images_out=True))
    assert choice.backend is IMAGE


def test_nothing_answers_a_question_when_only_an_image_model_is_left():
    r = Router([IMAGE], NoQuota())
    assert r.choose(Need()).backend is None


def test_a_backend_that_is_not_running_is_skipped():
    r = Router([LOCAL, CLOUD], NoQuota(), is_up=lambda key: key != "qwen")
    choice = r.choose(Need())
    assert choice.backend is CLOUD
    assert any("not running" in line for line in choice.rejected)


def test_unknown_liveness_is_not_held_against_a_backend():
    r = Router([LOCAL, CLOUD], NoQuota(), is_up=lambda key: None)
    assert r.choose(Need()).backend is LOCAL


def test_policy_can_turn_a_backend_off():
    r = Router([LOCAL, CLOUD], NoQuota(), policy=Policy(disabled=["qwen"]))
    choice = r.choose(Need())
    assert choice.backend is CLOUD
    assert any("turned off in policy" in line for line in choice.rejected)


def test_a_disabled_backend_is_still_reachable_by_name():
    r = Router([LOCAL, CLOUD], NoQuota(), policy=Policy(disabled=["qwen"]))
    assert r.choose(Need(backend="qwen")).backend is LOCAL


def test_policy_tier_override_changes_the_winner():
    r = Router([LOCAL, CLOUD], NoQuota(), policy=Policy(tiers={"qwen": 90}))
    choice = r.choose(Need())
    assert choice.backend is CLOUD
    assert "by policy" not in choice.reason      # claude's own tier was used


def test_policy_order_breaks_a_tie():
    other = make("other", 0)
    r = Router([LOCAL, other], NoQuota(), policy=Policy(order=["other"]))
    assert r.choose(Need()).backend is other


def test_policy_survives_a_round_trip(tmp_path):
    path = tmp_path / "policy.json"
    save_policy(Policy(disabled=["codex"], tiers={"qwen": 5}, order=["claude"],
                       quota_ceiling=0.8), path)
    again = load_policy(path)
    assert again.disabled == ["codex"]
    assert again.tiers == {"qwen": 5}
    assert again.order == ["claude"]
    assert again.quota_ceiling == 0.8


def test_a_missing_policy_file_is_just_no_preferences(tmp_path):
    empty = load_policy(tmp_path / "nothing.json")
    assert empty == Policy()
