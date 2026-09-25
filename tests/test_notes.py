# SPDX-License-Identifier: Apache-2.0
"""Memory: plain notes, the project's and yours, through `eki remember` /
`eki recall` and the same MCP tools."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from eki import cli, mcpbridge, notes


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "game"
    (root / ".eki").mkdir(parents=True)
    (root / "src").mkdir()
    return root


def test_a_note_goes_to_the_project_inside_one_and_yours_outside(game, tmp_path):
    n = notes.write("The tavern is called the Gilded Eel.", name="Tavern name",
                    where=str(game / "src"), source="eki remember")
    assert n["scope"] == "project" and n["made"]
    path = game / ".eki" / "memory" / "tavern-name.md"
    raw = path.read_text()
    assert raw.startswith("---\nname: tavern-name\ndescription: The tavern is called the Gilded Eel.")
    assert "from: eki remember" in raw and raw.rstrip().endswith("Gilded Eel.")
    elsewhere = tmp_path / "loose"
    elsewhere.mkdir()
    g = notes.write("I write British English", where=str(elsewhere))
    assert g["scope"] == "global" and g["name"] == "i-write-british-english"
    assert Path(g["path"]).parent == notes.HOME
    # -g from inside the project still goes to yours
    assert notes.write("tabs, not spaces", where=str(game), scope="global")["scope"] == "global"
    with pytest.raises(ValueError):
        notes.write("x", where=str(elsewhere), scope="project")
    with pytest.raises(ValueError):
        notes.write("   ")


def test_reading_sees_the_project_first_then_yours(game):
    notes.write("global palette: muted", name="palette", scope="global")
    notes.write("project palette: neon", name="palette", where=str(game))
    notes.write("the dragon is named Ash", name="dragon", where=str(game))
    rows = notes.listing(str(game))
    assert [(r["name"], r["scope"]) for r in rows] == [
        ("dragon", "project"), ("palette", "project"), ("palette", "global")]
    assert notes.read("palette", str(game))["text"] == "project palette: neon"
    assert notes.read("palette", str(game), scope="global")["text"] == "global palette: muted"
    assert notes.read("palette")["scope"] == "global"            # no folder: only yours
    assert notes.read("nothing", str(game)) is None
    hits = notes.search("NEON palette", str(game))
    assert [h["name"] for h in hits] == ["palette"] and hits[0]["lines"] == ["project palette: neon"]
    assert notes.search("", str(game)) == []


def test_the_same_name_replaces_or_appends_and_keeps_where_it_came_from(game):
    notes.write("first", name="log", where=str(game), description="what happened", source="thread a")
    again = notes.write("second", name="log", where=str(game), append=True)
    assert not again["made"]
    note = notes.read("log", str(game))
    assert note["text"] == "first\n\nsecond"
    assert note["description"] == "what happened" and note["from"] == "thread a"
    notes.write("only this", name="log", where=str(game))
    assert notes.read("log", str(game))["text"] == "only this"


def test_claude_style_notes_are_read_as_they_are():
    notes.HOME.mkdir(parents=True)
    (notes.HOME / "user-role.md").write_text(
        "---\nname: user-role\ndescription: they build games\nmetadata:\n  type: user\n---\n\nSolo game dev.\n")
    (notes.HOME / "plain.md").write_text("# Plain\n\nNo frontmatter at all.\n")
    rows = {r["name"]: r for r in notes.listing()}
    assert rows["user-role"]["description"] == "they build games"
    assert rows["plain"]["description"] == "Plain"
    assert notes.read("user-role")["text"] == "Solo game dev."


def test_the_commands(game, monkeypatch, capsys):
    monkeypatch.chdir(game / "src")
    monkeypatch.delenv("EKI_RUN", raising=False)
    monkeypatch.delenv("EKI_GRANT", raising=False)
    assert cli.main(["remember", "Ash breathes blue fire", "-n", "dragon"]) == 0
    assert "remembered dragon (project)" in capsys.readouterr().out
    assert cli.main(["remember", "-g", "short answers please"]) == 0
    capsys.readouterr()
    assert cli.main(["recall"]) == 0
    out = capsys.readouterr().out
    assert "dragon  [project]  Ash breathes blue fire" in out and "[global]" in out
    assert cli.main(["recall", "dragon"]) == 0
    assert capsys.readouterr().out.strip() == "Ash breathes blue fire"
    assert cli.main(["recall", "blue", "fire"]) == 0              # not a name: searched
    assert "dragon" in capsys.readouterr().out
    assert cli.main(["recall", "-g", "--json"]) == 0
    assert [r["scope"] for r in json.loads(capsys.readouterr().out)] == ["global"]
    assert cli.main(["recall", "unicorn"]) == 1


def test_a_read_only_run_cant_remember(game, monkeypatch, capsys):
    monkeypatch.chdir(game)
    monkeypatch.setenv("EKI_GRANT", json.dumps({"level": "read"}))
    assert cli.main(["remember", "sneaky"]) == 1
    assert notes.listing(str(game)) == []
    out = asyncio.run(mcpbridge.Bridge(None, "c1", folder=str(game)).call(
        "eki_remember", {"text": "sneaky"}))
    assert out["isError"]


def test_the_tools(game, monkeypatch):
    monkeypatch.delenv("EKI_GRANT", raising=False)
    bridge = mcpbridge.Bridge(None, "thread-1", screen=False, folder=str(game))
    assert {"eki_recall", "eki_remember"} <= set(bridge.tools)
    assert bridge.tools["eki_recall"].read_only

    def call(tool, **kw):
        out = asyncio.run(bridge.call(tool, kw))
        assert not out.get("isError"), out
        return out["content"][0]["text"]

    assert call("eki_recall") == "memory is empty"
    assert "remembered dragon (project)" in call("eki_remember", text="Ash breathes blue fire",
                                                 name="dragon")
    assert notes.read("dragon", str(game))["from"] == "thread thread-1"
    assert "- dragon [project]: Ash breathes blue fire" in call("eki_recall")
    assert call("eki_recall", name="dragon").endswith("Ash breathes blue fire")
    assert "dragon" in call("eki_recall", query="blue")
    assert "no note mentions" in call("eki_recall", query="unicorn")
    # a folder named in the call goes before the server's own
    other = game.parent / "other"
    (other / ".eki").mkdir(parents=True)
    call("eki_remember", text="elsewhere", name="x", folder=str(other))
    assert notes.read("x", str(other))["scope"] == "project"
    assert notes.read("x", str(game)) is None
