"""Starting a project from the window: where it goes, what's there, what to add first."""
import json
import os
import shutil

import pytest

from eki import api_cardadd, api_cards, paths, projectchats, projectstart, workspace


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def home(*parts):
    return projectchats.norm(paths.user_home().joinpath(*parts))


def test_no_projects_proposes_projects_folder(conn, src):
    got = projectstart.proposal(conn, name="x")
    assert got == {"folder": home("Projects", "x"), "base": home("Projects"), "recent": [],
                   "exists": False, "project": None}


def test_the_last_used_parent_is_proposed(conn, src):
    (paths.user_home() / "code").mkdir()
    pid = projectchats.create(conn, "a", paths.user_home() / "code" / "a")
    got = projectstart.proposal(conn, name="b")
    assert got["base"] == home("code") and got["folder"] == home("code", "b")
    assert got["recent"] == [projectchats.folder_of(conn, pid)]


def test_the_home_root_is_never_proposed(conn, src):
    projectchats.create(conn, "sss")                       # the CLI's ~/sss
    got = projectstart.proposal(conn, name="next")
    assert got["base"] == home("Projects") and got["folder"] == home("Projects", "next")
    assert got["recent"] == [home("sss")]


def test_recent_is_newest_first_distinct_and_still_there(conn, src):
    made = [projectchats.create(conn, f"p{i}", paths.user_home() / "w" / f"p{i}") for i in range(10)]
    shutil.rmtree(projectchats.folder_of(conn, made[9]))      # gone from disk: left out
    got = projectstart.recent(conn)
    assert got == [home("w", f"p{i}") for i in (8, 7, 6, 5, 4, 3, 2, 1)]


def test_exists_and_project_flags(conn, src):
    (paths.user_home() / "Projects" / "plain").mkdir(parents=True)
    got = projectstart.proposal(conn, name="plain")
    assert got["exists"] is True and got["project"] is None
    pid = projectchats.create(conn, "plain", got["folder"], history="none")
    by_folder = projectstart.proposal(conn, folder="~/Projects/plain/")
    assert by_folder["folder"] == home("Projects", "plain")
    assert by_folder["exists"] is True and by_folder["project"] == pid


def test_a_bad_name_or_no_folder_is_refused(conn, src):
    for bad in ("", "a/b", ".hidden"):
        with pytest.raises(ValueError):
            projectstart.proposal(conn, name=bad)
    with pytest.raises(ValueError):
        projectstart.proposal(conn, folder=" ")


def test_create_with_no_folder_lands_in_projects(conn, src):
    got = projectstart.create(conn, {"name": "game", "description": "a small game"})
    assert got == {"id": got["id"], "folder": home("Projects", "game"), "existing": False}
    assert os.path.isdir(os.path.join(got["folder"], ".git"))
    assert not os.path.exists(home("game"))


def test_the_same_folder_twice_is_one_project(conn, src):
    (paths.user_home() / "site").mkdir()
    first = projectstart.create(conn, {"folder": "~/site", "history": "none"})
    again = projectstart.create(conn, {"name": "other", "folder": str(paths.user_home() / "site")})
    assert first["existing"] is False and again == {**first, "existing": True}
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM projects").fetchone()[0] == "site"   # named after the folder


# ---- suggestions ----------------------------------------------------------------------------

@pytest.fixture
def proj(conn, src):
    return projectchats.create(conn, "app", paths.user_home() / "Projects" / "app")


def folder(conn, pid):
    from pathlib import Path
    return Path(projectchats.folder_of(conn, pid))


def kinds(conn, pid):
    return [(s["template"], s["from"]) for s in projectstart.starter(conn, pid)["suggestions"]]


def test_an_empty_folder_suggests_nothing(conn, proj):
    assert projectstart.starter(conn, proj) == {"suggestions": []}


def test_readme_starts_the_instructions(conn, proj):
    (folder(conn, proj) / "README.md").write_text("# app")
    got = projectstart.starter(conn, proj)["suggestions"]
    assert got == [{"template": "instructions", "from": "README.md",
                    "why": "README.md says what this is: start the instructions from it"}]


@pytest.mark.parametrize("own", ["CLAUDE.md", "AGENTS.md"])
def test_existing_instructions_win_over_readme(conn, proj, own):
    (folder(conn, proj) / "README.md").write_text("# app")
    (folder(conn, proj) / own).write_text("rules")
    assert kinds(conn, proj) == [("instructions", own)]


def test_package_json_scripts(conn, proj):
    (folder(conn, proj) / "package.json").write_text(json.dumps(
        {"scripts": {"dev": "vite", "test": "vitest run"}}))
    got = projectstart.starter(conn, proj)["suggestions"]
    assert [s["template"] for s in got] == ["serve", "workflow"]
    assert "vitest run" in got[1]["why"] and "test script" in got[1]["why"]
    (folder(conn, proj) / "package.json").write_text(json.dumps({"scripts": {"start": "node ."}}))
    assert kinds(conn, proj) == [("serve", "package.json")]
    (folder(conn, proj) / "package.json").write_text("not json")
    assert kinds(conn, proj) == []


@pytest.mark.parametrize("name", ["assets", "Images", "media", "art", "sounds", "audio", "video"])
def test_media_folders_suggest_assets(conn, proj, name):
    (folder(conn, proj) / name).mkdir()
    assert kinds(conn, proj) == [("assets", name + "/")]


def test_five_media_files_suggest_assets(conn, proj):
    for i in range(4):
        (folder(conn, proj) / f"p{i}.png").write_bytes(b"x")
    assert kinds(conn, proj) == []
    (folder(conn, proj) / "song.mp3").write_bytes(b"x")
    assert kinds(conn, proj) == [("assets", "5 media files")]


def test_cards_already_there_are_left_out(conn, proj):
    f = folder(conn, proj)
    (f / "README.md").write_text("# app")
    (f / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "test": "vitest"}}))
    (f / "assets").mkdir()
    assert [t for t, _ in kinds(conn, proj)] == ["instructions", "serve", "workflow", "assets"]
    base = api_cards.writable_root(conn, proj) / "components"
    from eki.components import cards
    cards.add_template(base, "serve")
    api_cardadd.add_workflow(conn, base.parent)
    assert [t for t, _ in kinds(conn, proj)] == ["instructions", "assets"]


def test_a_project_with_no_folder_has_no_suggestions(conn, src):
    from tests.test_api_projects import github_row
    assert projectstart.starter(conn, github_row(conn)) == {"suggestions": []}
