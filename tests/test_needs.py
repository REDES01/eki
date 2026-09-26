"""Row and request needs, what a provider lacks, and the routing table's defaults."""
import json

from eki.routing import needs, table


def _providers(home, entries):
    (home / "providers.json").write_text(json.dumps(entries))


def test_row_needs_defaults_by_key():
    assert needs.row_needs({"key": "code"}) == ["tools"]
    assert needs.row_needs({"key": "web"}) == ["web"]
    assert needs.row_needs({"key": "general"}) == []
    assert needs.row_needs({"key": "answer"}) == ["text"]


def test_row_needs_override():
    assert needs.row_needs({"key": "code", "needs": ["image"]}) == ["image"]
    assert needs.row_needs({"key": "answer", "needs": []}) == []


def test_request_needs_adds_vision_for_a_picture_only():
    row = {"key": "answer"}
    assert needs.request_needs(row, ["/tmp/a.PNG"]) == ["text", "vision"]
    assert needs.request_needs(row, ["/tmp/notes.txt"]) == ["text"]
    assert needs.request_needs(row) == ["text"]
    assert needs.request_needs({"key": "x", "needs": ["vision"]}, ["a.jpg", "b.webp"]) == ["vision"]


def test_is_picture():
    assert needs.is_picture("x.heic") and needs.is_picture("y.jpeg")
    assert not needs.is_picture("y.pdf")


def test_lacking_and_cant(home):
    _providers(home, {"plain": {"kind": "fake", "can": ["text"]}, "fake": {"kind": "fake"}})
    assert needs.lacking("plain", ["image"]) == ["image"]
    assert needs.cant(needs.lacking("plain", ["image"])) == "can't do images"
    assert needs.lacking("plain", ["text"]) == []
    assert needs.lacking("fake", ["text", "tools", "web"]) == ["web"]
    assert needs.lacking("nobody", ["text"]) == ["text"]
    assert needs.cant(["web", "vision"]) == "can't do the web or pictures"


def test_describe_has_tags_and_about(home):
    _providers(home, {"plain": {"kind": "fake", "can": ["text"], "about": "A small model."},
                      "fake": {"kind": "fake"}})
    assert needs.describe("plain") == "plain [text] — A small model."
    assert needs.describe("fake") == "fake [text, tools]"


def test_default_rows_declare_needs_and_general_is_cheapest_first(home):
    (home / "routing.json").unlink()
    _providers(home, {"claude": {"kind": "claude_code"}, "codex": {"kind": "codex"},
                      "local": {"kind": "local"}})
    rows = {r["key"]: r for r in table.rows()}
    assert set(rows) == {"answer", "code", "web", "general"}
    assert rows["general"]["targets"] == ["local", "claude", "codex"]
    assert rows["general"]["needs"] == [] and rows["code"]["needs"] == ["tools"]
    assert rows["answer"]["needs"] == ["text"] and rows["web"]["needs"] == ["web"]
    assert json.loads((home / "routing.json").read_text())["rows"][-1]["targets"] == [
        "local", "claude", "codex"]


def test_cheapest_first_is_a_stable_kind_sort(home):
    _providers(home, {"api": {"kind": "fake"}, "c": {"kind": "codex"}, "cc": {"kind": "claude_code"},
                      "m": {"kind": "local"}})
    assert table.cheapest_first(["api", "c", "m", "cc"]) == ["m", "c", "cc", "api"]
    assert table.cheapest_first(["x", "m"]) == ["m", "x"]


def test_existing_routing_json_is_left_as_written(home):
    written = {"rows": [{"key": "general", "title": "all", "targets": ["codex", "claude", "local"]}]}
    text = json.dumps(written)
    (home / "routing.json").write_text(text)
    assert table.row("general")["targets"] == ["codex", "claude", "local"]
    assert (home / "routing.json").read_text() == text


def test_checker_wakes(home):
    assert table.checker_wakes() is True
    data = json.loads((home / "routing.json").read_text())
    (home / "routing.json").write_text(json.dumps({**data, "checker_wakes": False}))
    assert table.checker_wakes() is False
    assert table.settings()["checker"] == "none"
