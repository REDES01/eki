import pytest

from eki import targets
from eki.targets import Target


@pytest.mark.parametrize("s,want", [
    ("claude", Target("claude")),
    ("claude:fast", Target("claude", tier="fast")),
    ("claude:claude-haiku-4-5", Target("claude", model="claude-haiku-4-5")),
    ("codex:top", Target("codex", tier="top")),
])
def test_parse_and_fmt_round_trip(s, want):
    assert targets.parse(s) == want
    assert targets.fmt(targets.parse(s)) == s


@pytest.mark.parametrize("bad", ["", ":fast", "claude:"])
def test_parse_refuses_an_empty_part(bad):
    with pytest.raises(ValueError):
        targets.parse(bad)


def test_provider_of():
    assert targets.provider_of("claude:fast") == "claude"
    assert targets.provider_of("local") == "local"


CFG = {"kind": "claude_code", "model": "claude-sonnet-4-5",
       "models": {"fast": "claude-haiku-4-5", "top": "claude-opus-4-1"}}


def test_resolve_a_tier_from_models():
    assert targets.resolve("claude:fast", CFG, None) == ("claude-haiku-4-5", "")
    assert targets.resolve(Target("claude", tier="top"), CFG, None) == ("claude-opus-4-1", "")


def test_resolve_default_falls_back_to_the_old_model():
    assert targets.resolve("claude:default", CFG, None) == ("claude-sonnet-4-5", "")
    picked = {**CFG, "models": {**CFG["models"], "default": "claude-sonnet-5"}}
    assert targets.resolve("claude:default", picked, None) == ("claude-sonnet-5", "")


def test_resolve_an_unpicked_tier_is_the_default_with_a_note():
    assert targets.resolve("claude:fast", {"kind": "claude_code"}, None) == \
        (None, "claude has no fast model picked; its default")


def test_resolve_a_model_gone_from_the_read_list():
    listed = ["claude-haiku-4-5", "claude-sonnet-4-5"]
    assert targets.resolve("claude:claude-opus-4-1", CFG, listed) == \
        (None, "claude-opus-4-1 isn't in claude's list any more; its default")
    assert targets.resolve("claude:top", CFG, listed)[0] is None
    assert targets.resolve("claude:fast", CFG, listed) == ("claude-haiku-4-5", "")


def test_resolve_an_unread_list_trusts_the_name():
    assert targets.resolve("claude:claude-opus-4-1", CFG, None) == ("claude-opus-4-1", "")


def test_resolve_a_bare_provider_is_its_default():
    assert targets.resolve("claude", CFG, ["x"]) == (None, "")


def test_problems():
    known = {"claude", "local"}
    assert targets.problems("claude:fast", known) == []
    assert targets.problems("claude:claude-haiku-4-5", known) == []
    assert any("unknown provider 'nope'" in p for p in targets.problems("nope:fast", known))
    assert targets.problems("claude:", known)
    assert targets.problems("claude:a b", known)
    assert targets.problems(3, known)
