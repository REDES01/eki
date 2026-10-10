"""The playtester template and the session journal its calls are kept in."""
import json
import shutil

from eki import components
from eki.components import journal

TEXTS = [
    "Opening the page.\nNOTE: a title screen with a glowing card\nFEEL curious: the card shimmers when I move the mouse",
    "- NOTE: clicked Start, a dialogue box appears\n**FEEL confused**: no hint which key advances the text\n"
    "FEEL Delighted: the card flips with a sound",
    "NOTE: finished the first scene\nREVIEW: Pretty, but the first minute leaves you guessing.\nFix the hints first.",
]


def test_template_is_a_valid_agent(home):
    src = components.templates_dir() / "playtester"
    comp = components.load(src, "pool")
    assert comp.kind == "agent" and comp.spec["bare"] is True and comp.spec["access"] == "shared"
    assert comp.spec["mcp"] == ["playwright"]
    assert "--caps=vision" in comp.spec["mcp_servers"]["playwright"]["args"]
    assert "mcp__playwright__browser_evaluate" in comp.spec["deny_tools"]
    prompt = (src / "prompt.md").read_text()
    assert "{url}" in prompt and "NOTE:" in prompt and "FEEL" in prompt and "REVIEW:" in prompt
    shutil.copytree(src, components.pool_dir() / "playtester")
    found, errors = components.find(None)
    assert errors == [] and found["playtester"].scope == "pool"


def test_sessions_are_separate(home):
    a, b = journal.new_session("playtester"), journal.new_session("playtester")
    assert a != b and a.is_dir() and b.is_dir()
    assert a.parent == b.parent == components.sessions_dir() / "playtester"


def test_keep_parses_notes_emotions_review(home):
    s = journal.new_session("playtester")
    (s / "screenshots").mkdir()
    (s / "screenshots" / "1.png").write_bytes(b"png")
    r = journal.keep(s, TEXTS)
    review = "Pretty, but the first minute leaves you guessing.\nFix the hints first."
    assert (s / "review.md").read_text() == review + "\n"
    assert (s / "notes.md").read_text().splitlines() == [
        "- a title screen with a glowing card", "- clicked Start, a dialogue box appears",
        "- finished the first scene"]
    feels = [json.loads(l) for l in (s / "emotions.jsonl").read_text().splitlines()]
    assert [(f["emotion"], f["why"]) for f in feels] == [
        ("curious", "the card shimmers when I move the mouse"),
        ("confused", "no hint which key advances the text"),
        ("delighted", "the card flips with a sound")]
    assert all(f["t"].endswith("+00:00") for f in feels)
    assert [f["t"] for f in feels] == sorted(f["t"] for f in feels)
    assert all(t in (s / "transcript.md").read_text() for t in TEXTS)
    assert (s / "screenshots" / "1.png").read_bytes() == b"png"
    assert r.text == f"{review}\n\nsession: {s}" and r.paths == [str(s)]


def test_missing_review_falls_back_to_last_text(home):
    s = journal.new_session("playtester")
    r = journal.keep(s, ["NOTE: something", "I ran out of time; it felt slow."])
    assert (s / "review.md").read_text() == "I ran out of time; it felt slow.\n"
    assert r.text.startswith("I ran out of time") and r.text.endswith(f"session: {s}")
    assert (s / "emotions.jsonl").read_text() == ""
