"""The eki skill names every command, so agents know what they can ask for."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from eki.cli import COMMANDS

SKILL = Path(__file__).resolve().parents[1] / "skills/eki/SKILL.md"


def _text() -> str:
    return SKILL.read_text()


def test_skill_has_frontmatter():
    text = _text()
    assert text.startswith("---\n"), "SKILL.md has no frontmatter"
    front = text.split("---\n", 2)[1]
    assert re.search(r"^name: eki$", front, re.M), "frontmatter lacks `name: eki`"


def test_skill_mentions_every_command():
    text = _text()
    missing = [n for n in COMMANDS if not re.search(r"\beki " + re.escape(n) + r"\b", text)]
    assert not missing, f"skills/eki/SKILL.md doesn't mention: {', '.join('eki ' + n for n in missing)}"


@pytest.mark.parametrize("name", COMMANDS)
def test_skill_mentions_command(name):
    assert re.search(r"\beki " + re.escape(name) + r"\b", _text()), f"SKILL.md doesn't mention `eki {name}`"


def test_skill_says_a_wish_is_enough():
    text = _text()
    assert "A wish is enough" in text
    assert "eki self --as-is" in text
    assert "eki self show <goal>" in text


def test_self_build_doc_describes_the_draft():
    doc = (SKILL.parents[2] / "docs/self-build.md").read_text()
    assert "**The draft.**" in doc
    assert re.search(r"^draft +agent run, read-only worktree +→ goal$", doc, re.M)
    for part in ("What must be true when done", "Read first", "Item shape and shared files", "Tests"):
        assert f"**{part}**" in doc, part
