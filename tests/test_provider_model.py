"""A per-run model (turn.extra["model"]) reaches the provider's CLI."""
from eki.providers.base import Turn
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex


def turn(model=None):
    return Turn(prompt="hi", history=[], extra={"model": model} if model else {})


def models(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--model"]


def test_claude_run_model_passed_once():
    assert models(ClaudeCode("claude", {}).argv(turn("opus"))) == ["opus"]


def test_claude_run_model_beats_config():
    assert models(ClaudeCode("claude", {"model": "sonnet"}).argv(turn("opus"))) == ["opus"]


def test_claude_config_model_when_run_has_none():
    assert models(ClaudeCode("claude", {"model": "sonnet"}).argv(turn())) == ["sonnet"]
    assert models(ClaudeCode("claude", {"model": "sonnet"}).argv(
        Turn(prompt="hi", history=[], extra={"model": None}))) == ["sonnet"]


def test_claude_no_model_no_flag():
    assert "--model" not in ClaudeCode("claude", {}).argv(turn())


def test_codex_run_model_in_thread_params():
    assert Codex("codex", {})._thread_params(turn("gpt-5"))["model"] == "gpt-5"
    assert Codex("codex", {"model": "o3"})._thread_params(turn("gpt-5"))["model"] == "gpt-5"
    assert Codex("codex", {"model": "o3"})._thread_params(turn())["model"] == "o3"
    assert "model" not in Codex("codex", {})._thread_params(turn())
