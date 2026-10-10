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
    cfg = json.loads((home / "providers.json").read_text())
    (home / "providers.json").write_text(json.dumps({**cfg, "claude": {"kind": "claude_code"}}))
    src = components.templates_dir() / "playtester"
    comp = components.load(src, "pool")
    assert comp.kind == "agent" and comp.spec["bare"] is True and comp.spec["access"] == "shared"
    assert comp.spec["mcp"] == ["playwright"]
    assert "--caps=vision" in comp.spec["mcp_servers"]["playwright"]["args"]
    assert "mcp__playwright__browser_evaluate" in comp.spec["deny_tools"]
    args = comp.spec["mcp_servers"]["playwright"]["args"]
    pairs = list(zip(args, args[1:]))
    assert ("--snapshot-mode", "none") in pairs and ("--image-responses", "allow") in pairs
    assert "--isolated" in args and ("--output-dir", "{session}/screenshots") in pairs
    allow = comp.spec["allow_tools"]
    assert "mcp__playwright__browser_mouse_click_xy" in allow and "mcp__playwright__browser_navigate" in allow
    assert "mcp__playwright__browser_snapshot" not in allow and "mcp__playwright__browser_tabs" not in allow
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


URL = "http://127.0.0.1:4200/chapter1/"


def test_navigate_inside_the_url_is_clean(home):
    s = journal.new_session("playtester")
    calls = [{"name": "mcp__playwright__browser_navigate", "url": URL},
             {"name": "mcp__playwright__browser_navigate", "input": {"url": URL + "index.html?level=2"}},
             {"name": "mcp__playwright__browser_take_screenshot", "detail": "{}"}]
    r = journal.keep(s, TEXTS, calls, URL)
    assert json.loads((s / "session.json").read_text())["tainted"] == ""
    assert not r.text.startswith("tainted")


def test_navigate_outside_the_url_taints(home):
    s = journal.new_session("playtester")
    away = "http://127.0.0.1:4200/src/main.js"
    calls = [{"name": "mcp__playwright__browser_navigate", "url": URL},
             {"name": "mcp__playwright__browser_navigate", "url": away}]
    r = journal.keep(s, TEXTS, calls, URL)
    rec = json.loads((s / "session.json").read_text())
    assert rec["tainted"] == f"opened {away}" and rec["opened"] == [away]
    assert r.text.startswith(f"tainted: opened {away}\n") and r.text.endswith(f"session: {s}")
    for bad in ("http://127.0.0.1:4201/chapter1/", "http://127.0.0.1:4200/chapter1/../package.json",
                "http://127.0.0.1:4200/chapter1/%2e%2e/x", "file:///etc/passwd", "http://127.0.0.1:4200/chapter10/"):
        assert journal.opened_elsewhere([{"name": "browser_navigate", "url": bad}], URL) == [bad]
