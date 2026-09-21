"""Routing decisions, including the ones that only happen when quota runs out."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from hub.router import Need, QuotaSource, Router


class Fake(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield "x"


def make(key, tier, *, repo=False, tools=False, context=8000, quota=None):
    return Fake(BackendInfo(
        key=key, kind="fake", label=key,
        capabilities=Capabilities(context_tokens=context, repo=repo, tools=tools),
        cost=Cost(tier=tier), quota_source=quota), {})


LOCAL = make("qwen", 0, context=32000)
CLAUDE = make("claude", 50, repo=True, tools=True, context=200000, quota="claude")
API = make("api", 100, repo=True, tools=True, context=200000)


class NoQuota(QuotaSource):
    def __init__(self, spent=None):
        super().__init__()
        self._spent = spent or {}

    def exhausted(self):
        return self._spent


def test_plain_question_goes_to_the_cheapest():
    r = Router([CLAUDE, LOCAL, API], NoQuota())
    choice = r.choose(Need())
    assert choice.backend is LOCAL
    assert "tier 0" in choice.reason


def test_repo_work_skips_a_backend_that_cannot_edit():
    r = Router([LOCAL, CLAUDE, API], NoQuota())
    choice = r.choose(Need(repo=True))
    assert choice.backend is CLAUDE
    assert any("qwen" in line and "repo" in line for line in choice.rejected)


def test_spent_quota_is_skipped_not_attempted():
    r = Router([LOCAL, CLAUDE, API], NoQuota({"claude": "5H at 100%"}))
    choice = r.choose(Need(repo=True))
    assert choice.backend is API, "should fall through to metered API"
    assert any("5H at 100%" in line for line in choice.rejected)


def test_context_requirement_filters():
    r = Router([LOCAL, CLAUDE], NoQuota())
    choice = r.choose(Need(context_tokens=100_000))
    assert choice.backend is CLAUDE


def test_explicit_backend_wins_over_everything():
    r = Router([LOCAL, CLAUDE], NoQuota({"claude": "5H at 100%"}))
    choice = r.choose(Need(backend="claude"))
    assert choice.backend is CLAUDE
    assert "asked for by name" in choice.reason


def test_nothing_fits_says_why():
    r = Router([LOCAL], NoQuota())
    choice = r.choose(Need(images_out=True))
    assert choice.backend is None
    assert choice.rejected and "images" in choice.rejected[0]


def test_unknown_quota_is_never_held_against_a_backend():
    assert QuotaSource().exhausted() == {}
