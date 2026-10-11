"""`eki component merge`: knowledge cards into one card of documents, a service and an mcp card into one."""
import json

from eki import components
from eki.cli import main


def _card(base, name, spec):
    (base / name).mkdir(parents=True)
    (base / name / "component.json").write_text(json.dumps(spec))


def _spec(base, name):
    return json.loads((base / name / "component.json").read_text())


def _project(proj):
    base = proj / "components"
    for f in ("design/DECISIONS.md", "design/rules.md", "notes.md"):
        (proj / f).parent.mkdir(parents=True, exist_ok=True)
        (proj / f).write_text("x\n")
    _card(base, "decisions", {"kind": "knowledge", "about": "approved", "files": ["design/DECISIONS.md"],
                              "read": "always"})
    _card(base, "rules", {"kind": "knowledge", "about": "the rules", "files": ["design/rules.md"],
                          "read": "when-relevant"})
    _card(base, "notes", {"kind": "knowledge", "about": "notes", "files": ["notes.md"], "read": "when-relevant"})
    _card(base, "music", {"kind": "service", "about": "renders cues", "access": "exclusive",
                          "run": ["node", "tools/music/auto.mjs", "--all-new"], "conflicts": ["ableton"]})
    _card(base, "ableton", {"kind": "mcp", "about": "Live over MCP", "access": "queue",
                            "server": {"command": "uvx", "args": ["ableton-mcp"], "env": {}},
                            "conflicts": ["music", "images"]})
    _card(base, "images", {"kind": "service", "about": "pictures", "provider": "comfyui",
                           "conflicts": ["voice", "music", "ableton"]})
    _card(base, "voice", {"kind": "service", "about": "clips", "run": ["say"], "conflicts": ["images", "music"]})
    return base


def test_knowledge_cards_become_one_card_of_documents(proj, capsys):
    base = _project(proj)
    assert main(["component", "merge", str(proj), "knowledge", "knowledge", "--about", "shared docs"]) == 0
    spec = _spec(base, "knowledge")
    assert spec["about"] == "shared docs" and "files" not in spec
    got = {d["name"]: (d["files"], d["about"], d["read"]) for d in spec["docs"]}
    assert got == {"decisions": (["design/DECISIONS.md"], "approved", "always"),
                   "rules": (["design/rules.md"], "the rules", "when-relevant"),
                   "notes": (["notes.md"], "notes", "when-relevant")}
    for n in ("decisions", "rules", "notes"):
        assert not (base / n).exists()
        assert (proj.parent / "user" / ".Trash" / n / "component.json").is_file()
    assert "to the Trash" in capsys.readouterr().out
    found, errors = components.own(proj)
    assert not errors and found["knowledge"].kind == "knowledge"


def test_knowledge_merge_keeps_an_existing_cards_documents(proj):
    base = _project(proj)
    assert main(["component", "merge", str(proj), "knowledge", "knowledge", "decisions"]) == 0
    assert main(["component", "merge", str(proj), "knowledge", "knowledge", "rules"]) == 0
    assert [d["name"] for d in _spec(base, "knowledge")["docs"]] == ["decisions", "rules"]
    assert (base / "notes").is_dir()


def test_service_and_mcp_become_one_card(proj):
    base = _project(proj)
    assert main(["component", "merge", str(proj), "service", "ableton", "music", "ableton",
                 "--about", "Live: script or MCP"]) == 0
    spec = _spec(base, "ableton")
    assert spec["kind"] == "service" and spec["about"] == "Live: script or MCP"
    assert spec["run"] == ["node", "tools/music/auto.mjs", "--all-new"]
    assert spec["mcp"] == {"command": "uvx", "args": ["ableton-mcp"], "env": {}}
    assert spec["access"] == "exclusive"
    assert spec["conflicts"] == ["images"]              # music and ableton gone, no self-conflict
    assert _spec(base, "images")["conflicts"] == ["voice", "ableton"]
    assert _spec(base, "voice")["conflicts"] == ["images", "ableton"]
    assert not (base / "music").exists()
    assert (proj.parent / "user" / ".Trash" / "music").is_dir()
    found, errors = components.own(proj)
    assert not errors and found["ableton"].kind == "service" and "music" not in found


def test_dry_run_writes_nothing(proj, capsys):
    base = _project(proj)
    before = {d.name: (d / "component.json").read_text() for d in base.iterdir()}
    assert main(["component", "merge", str(proj), "service", "ableton", "music", "ableton", "--dry-run"]) == 0
    assert main(["component", "merge", str(proj), "knowledge", "knowledge", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "music/ to the Trash" in out and "3 documents" in out
    assert {d.name: (d / "component.json").read_text() for d in base.iterdir()} == before
    assert not (proj.parent / "user" / ".Trash").exists()


def test_wrong_kinds_are_refused(proj, capsys):
    _project(proj)
    assert main(["component", "merge", str(proj), "service", "ableton", "ableton", "music"]) == 1
    assert main(["component", "merge", str(proj), "knowledge", "knowledge", "music"]) == 1
    assert "not" in capsys.readouterr().err
