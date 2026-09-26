"""Providers declare what they can do: per-kind defaults, an entry's own `can`."""
import json

import pytest

from eki import cli, providers


@pytest.mark.parametrize("kind,can", [
    ("claude_code", ["text", "tools", "web", "vision"]),
    ("codex", ["text", "tools", "web", "vision"]),
    ("local", ["text"]),
    ("command", []),
    ("fake", ["text", "tools"]),
])
def test_kind_defaults(kind, can):
    assert providers.capabilities("x", {"kind": kind}) == can
    assert providers.build("x", {"kind": kind}).can == can


def test_every_default_is_a_known_ability():
    for can in providers.CAN.values():
        assert set(can) <= set(providers.ABILITIES)


def test_an_entrys_can_replaces_the_default():
    p = providers.build("x", {"kind": "local", "can": ["text", "vision"], "about": "A small model."})
    assert p.can == ["text", "vision"]
    assert p.about == "A small model."
    assert not p.harness
    assert providers.build("y", {"kind": "claude_code", "can": ["text"]}).harness is False


def test_bare_fake_is_text_only():
    p = providers.get("bare")
    assert p.can == ["text"]
    assert p.harness is False
    assert providers.get("fake").harness is True


def test_harness_follows_tools():
    assert providers.build("c", {"kind": "claude_code"}).harness is True
    assert providers.build("o", {"kind": "codex"}).harness is True
    assert providers.build("l", {"kind": "local"}).harness is False
    cmd = providers.build("command", providers.BUILTIN["command"])
    assert cmd.can == [] and cmd.harness is False
    assert cmd.about == ""


def test_providers_command_shows_tags(home, capsys):
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake", "about": "Pretends to be a harness."},
        "bare": {"kind": "fake", "harness": False}}))
    assert cli.main(["providers"]) == 0
    out = capsys.readouterr().out
    assert "[text, tools]" in out
    assert "[text]" in out
    assert "Pretends to be a harness." in out
