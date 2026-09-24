# SPDX-License-Identifier: Apache-2.0
"""One standing context: AGENTS.md is canonical, CLAUDE.md imports it, and
each CLI's global file is a link into eki's source (conftest keeps all of
it in a temporary home)."""
import json
import os

import pytest

from eki import cli, standing


def _home():
    return standing.HOME


def test_boot_makes_the_source_and_links_both_clis():
    report = standing.boot()
    agents = (_home() / "AGENTS.md").read_text()
    claude = (_home() / "CLAUDE.md").read_text()
    assert standing.BEGIN in agents and "eki ask" in agents
    assert claude == "@AGENTS.md\n"
    for backend, path, name in standing.views():
        assert path.is_symlink() and path.resolve() == (_home() / name).resolve()
    assert len(report["linked"]) == 3 and not report["conflicts"]
    # Claude's import resolves beside the link as well as in the source
    assert (standing.CLAUDE_HOME / "AGENTS.md").read_text() == agents
    assert standing.boot()["linked"] == [] and standing.boot()["changed"] == []


def test_the_eki_section_is_kept_current_and_the_rest_left_alone():
    standing.boot()
    path = _home() / "AGENTS.md"
    text = path.read_text()
    old = text.replace("The `eki` skill says how.", "An old line.")
    path.write_text("# Mine\n\nUse pnpm.\n\n" + old + "\nAfter it.\n")
    standing.boot()
    now = path.read_text()
    assert now.startswith("# Mine\n\nUse pnpm.\n\n" + standing.BEGIN)
    assert "An old line." not in now and "The `eki` skill says how." in now
    assert now.endswith(standing.END + "\nAfter it.\n")
    assert now.count(standing.BEGIN) == 1
    standing.boot()
    assert path.read_text() == now


def test_a_file_eki_didnt_make_is_reported_not_replaced():
    standing.CLAUDE_HOME.mkdir(parents=True)
    mine = standing.CLAUDE_HOME / "CLAUDE.md"
    mine.write_text("Be terse.\n")
    report = standing.boot()
    assert str(mine) in report["conflicts"]
    assert not mine.is_symlink() and mine.read_text() == "Be terse.\n"
    states = {v["path"]: v["state"] for v in standing.state()}
    assert states[str(mine)] == "conflict"
    assert states[str(standing.CODEX_HOME / "AGENTS.md")] == "linked"


def test_import_takes_both_files_in_once_and_links_them_back():
    standing.CLAUDE_HOME.mkdir(parents=True)
    standing.CODEX_HOME.mkdir(parents=True)
    (standing.CODEX_HOME / "AGENTS.md").write_text("Use pnpm.\n")
    # a copy of the shared text, plus a line only Claude needs
    (standing.CLAUDE_HOME / "CLAUDE.md").write_text("Use pnpm.\n")
    standing.boot()
    report = standing.import_existing()
    assert len(report["imported"]) == 2 and not report["conflicts"]
    agents = (_home() / "AGENTS.md").read_text()
    assert agents.startswith("Use pnpm.\n\n" + standing.BEGIN)
    assert (_home() / "CLAUDE.md").read_text() == "@AGENTS.md\n"        # not said twice
    kept = sorted(p.name for p in standing.IMPORTED.iterdir())
    assert kept[0].startswith("claude-CLAUDE.md-") and kept[1].startswith("codex-AGENTS.md-")
    assert all(v["state"] == "linked" for v in standing.state())
    assert (standing.CODEX_HOME / "AGENTS.md").read_text() == agents


def test_claude_only_text_goes_below_the_import():
    standing.CLAUDE_HOME.mkdir(parents=True)
    (standing.CLAUDE_HOME / "CLAUDE.md").write_text("Use the /review command.\n")
    standing.import_existing()
    assert (_home() / "CLAUDE.md").read_text() == "@AGENTS.md\n\nUse the /review command.\n"


def test_a_link_someone_else_made_is_theirs(tmp_path):
    elsewhere = tmp_path / "dotfiles" / "AGENTS.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("from my dotfiles\n")
    standing.CODEX_HOME.mkdir(parents=True)
    os.symlink(elsewhere, standing.CODEX_HOME / "AGENTS.md")
    report = standing.import_existing()
    assert report["imported"] == [] and str(standing.CODEX_HOME / "AGENTS.md") in report["conflicts"]
    assert os.readlink(standing.CODEX_HOME / "AGENTS.md") == str(elsewhere)


def test_a_project_s_claude_md_becomes_an_import(tmp_path):
    only_claude = tmp_path / "a"
    only_claude.mkdir()
    (only_claude / "CLAUDE.md").write_text("# A\n\nRun make test.\n")
    standing.project(str(only_claude))
    assert (only_claude / "AGENTS.md").read_text() == "# A\n\nRun make test.\n"
    assert (only_claude / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    both = tmp_path / "b"
    both.mkdir()
    (both / "AGENTS.md").write_text("shared\n")
    (both / "CLAUDE.md").write_text("claude only\n")
    standing.project(str(both))
    assert (both / "CLAUDE.md").read_text() == "@AGENTS.md\n\nclaude only\n"
    assert standing.project(str(both))["done"] == []                   # already so

    only_agents = tmp_path / "c"
    only_agents.mkdir()
    (only_agents / "AGENTS.md").write_text("shared\n")
    standing.project(str(only_agents))
    assert (only_agents / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    empty = tmp_path / "d"
    empty.mkdir()
    assert standing.project(str(empty))["done"] == [] and not list(empty.iterdir())
    with pytest.raises(ValueError):
        standing.project(str(tmp_path / "nowhere"))


def test_use_here_adds_eki_s_section_and_allows_eki_for_claude(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    r = standing.use_here(str(empty))
    agents = (empty / "AGENTS.md").read_text()
    assert agents == standing.eki_section()
    assert (empty / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    settings = json.loads((empty / ".claude" / "settings.local.json").read_text())
    assert settings["permissions"]["allow"] == [standing.ALLOW]
    assert len(r["done"]) == 3
    assert standing.use_here(str(empty))["done"] == []                  # already so

    only_claude = tmp_path / "c"
    (only_claude / ".claude").mkdir(parents=True)
    (only_claude / "CLAUDE.md").write_text("# C\n\nRun make test.\n")
    (only_claude / ".claude" / "settings.local.json").write_text(
        '{"model": "opus", "permissions": {"allow": ["Bash(make:*)"], "deny": ["Read(.env)"]}}')
    standing.use_here(str(only_claude))
    agents = (only_claude / "AGENTS.md").read_text()
    assert agents.startswith("# C\n\nRun make test.\n\n") and agents.endswith(standing.END + "\n")
    assert (only_claude / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    settings = json.loads((only_claude / ".claude" / "settings.local.json").read_text())
    assert settings == {"model": "opus", "permissions": {
        "allow": ["Bash(make:*)", standing.ALLOW], "deny": ["Read(.env)"]}}


def test_use_here_leaves_a_broken_settings_file_alone(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text("{not json")
    with pytest.raises(ValueError):
        standing.use_here(str(tmp_path))
    assert (tmp_path / ".claude" / "settings.local.json").read_text() == "{not json"


def test_cli_status_and_show(capsys):
    assert cli.main(["context"]) == 0
    out = capsys.readouterr().out
    assert "linked" in out and "AGENTS.md" in out
    assert cli.main(["context", "show", "--claude"]) == 0
    assert capsys.readouterr().out == "@AGENTS.md\n"
