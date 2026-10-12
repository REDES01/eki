"""`eki project [key]`: list, show and make projects; on GitHub set the check or branch, retry a fault."""
import json
import subprocess

import pytest

from conftest import run_inline
from eki import builds, paths, projectchats, projects, projectsetup, store, workspace
from eki.cli import main


@pytest.fixture
def gh(tmp_path, fake_gh):
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], "me")
    w = tmp_path / "upstream"
    w.mkdir()
    (w / "Makefile").write_text("check:\n\ttrue\n")
    workspace.git(w, "init", "-q", "-b", "trunk")
    workspace.git(w, "add", "-A")
    workspace.git(w, "commit", "-q", "-m", "first")
    bare = tmp_path / "me-proj.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True, capture_output=True)
    fake_gh.repo("me/proj", bare)
    return fake_gh


def ready(conn):
    p = projectsetup.ensure(conn, "me/proj")
    run_inline(conn, p["setup_run"])
    projectsetup.tick(conn)
    return projects.get(conn, p["id"])


def test_check_is_set_and_survives_a_later_tick(conn, capsys, gh):
    p = projectsetup.ensure(conn, "me/proj")
    assert main(["project", "me/proj", "--check", "make x"]) == 0
    assert capsys.readouterr().out.strip() == "me/proj: check make x (set)"
    run_inline(conn, p["setup_run"])
    projectsetup.tick(conn)                                # the clone lands: no guess over a set check
    p = projects.get(conn, p["id"])
    assert p["setup"] == "ready" and p["check_cmd"] == "make x" and p["check_from"] == "set"
    projectsetup.tick(conn)
    assert projects.get(conn, p["id"])["check_cmd"] == "make x"


def test_an_empty_check_means_none(conn, capsys, gh):
    p = ready(conn)
    assert p["check_cmd"] == "make check"
    assert main(["project", "me/proj", "--check", ""]) == 0
    p = projects.get(conn, p["id"])
    assert p["check_cmd"] is None and p["check_from"] == "set"
    assert projects.check_argv(p) is None


def test_branch_overrides(conn, capsys, gh):
    p = ready(conn)
    assert p["branch"] == "trunk"
    assert main(["project", "me/proj", "--branch", "dev"]) == 0
    assert projects.get(conn, p["id"])["branch"] == "dev"


def test_retry_starts_a_fault_again(conn, capsys, gh):
    p = ready(conn)
    conn.execute("UPDATE projects SET setup='fault', fault='install failed: broken', install_cmd='true' WHERE id=?",
                 (p["id"],))
    assert main(["project", "me/proj", "--retry"]) == 0
    assert "install started again" in capsys.readouterr().out
    p = projects.get(conn, p["id"])
    assert p["setup"] == "installing" and p["fault"] is None
    assert json.loads(store.run(conn, p["setup_run"])["prompt"]) == ["/bin/sh", "-c", "true"]
    assert main(["project", "me/proj", "--retry"]) == 1
    assert "no setup fault" in capsys.readouterr().err


def test_no_flags_prints_the_block(conn, capsys, gh):
    p = ready(conn)
    assert main(["project", "me/proj"]) == 0
    assert capsys.readouterr().out.splitlines()[:4] == [
        "me/proj", "  check: make check (guessed: Makefile target check)", f"  clone: {p['path']}", "  setup: ready"]


def test_an_unknown_repo_fails(conn, capsys, fake_gh):
    assert main(["project", "me/nothing"]) == 1
    assert "no project me/nothing" in capsys.readouterr().err
    assert main(["project", "nothing"]) == 1
    assert "isn't OWNER/REPO" in capsys.readouterr().err


def test_the_listing_shows_eki_a_folder_project_and_a_github_project(conn, capsys, gh):
    ready(conn)
    pid = projectchats.create(conn, "notes")
    assert main(["project"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:2] == ["eki", projectchats.norm(builds.source())]
    notes = next(line for line in out if line.startswith("notes "))
    assert projectchats.folder_of(conn, pid) in notes and "0 chats · 0 goals · 0 PRs" in notes
    proj = next(line for line in out if "me/proj" in line)
    assert "no folder" in proj


def test_new_makes_a_folder_in_your_home(conn, capsys):
    assert main(["project", "--new", "tokenbar"]) == 0
    where = paths.user_home() / "tokenbar"
    assert (where / ".git").is_dir()
    out = capsys.readouterr().out
    assert projectchats.norm(where) in out
    pid = projectchats.find(conn, "tokenbar")
    assert projects.get(conn, pid)["folder"] == projectchats.norm(where)
    assert main(["project", "--new", "eki"]) == 1
    assert "eki itself" in capsys.readouterr().err


def test_new_takes_a_folder(conn, capsys, tmp_path):
    mine = tmp_path / "elsewhere"
    mine.mkdir()
    assert main(["project", "--new", "work", "--folder", str(mine), "--chat-only"]) == 0
    assert projectchats.folder_of(conn, projectchats.find(conn, "work")) == projectchats.norm(mine)


def test_folder_sets_it(conn, capsys, gh, tmp_path):
    p = ready(conn)
    mine = tmp_path / "user" / "myproj"
    mine.mkdir()
    assert main(["project", "me/proj", "--folder", str(mine)]) == 0
    assert projectchats.folder_of(conn, p["id"]) == projectchats.norm(mine)
    assert main(["project", "me/proj", "--folder", str(builds.source())]) == 1
    assert "eki itself" in capsys.readouterr().err


def test_a_key_resolves_by_name_and_by_repo(conn, capsys, gh):
    p = ready(conn)
    projectchats.create(conn, "notes")
    capsys.readouterr()
    assert main(["project", "notes"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "notes" and "  background: none" in out
    assert main(["project", "me/proj"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("me/proj\n") and f"background: eki's clone at {p['path']}" in out
    assert main(["project", "eki"]) == 0
    assert "background: eki's own queue" in capsys.readouterr().out


def test_github_flags_on_other_rows_say_github_only(conn, capsys):
    projectchats.create(conn, "notes")
    capsys.readouterr()
    for flags in (["--check", "true"], ["--branch", "dev"], ["--retry"]):
        assert main(["project", "notes", *flags]) == 1
        assert "GitHub-only" in capsys.readouterr().err
    assert main(["project", "eki", "--check", "true"]) == 1
    assert "GitHub-only" in capsys.readouterr().err


@pytest.fixture
def folder_proj(conn, tmp_path, monkeypatch):
    """A local project with a chat, beside eki's own source."""
    src = tmp_path / "src"
    src.mkdir()
    workspace.git(src, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(src))
    pid = projectchats.create(conn, "tokenbar")
    tid = store.create_thread(conn, "hi", projectchats.folder_of(conn, pid))
    conn.execute("UPDATE threads SET project=? WHERE id=?", (pid, tid))
    conn.commit()
    return pid


def test_rm_yes_deletes_and_says_what_it_did(conn, capsys, folder_proj):
    folder = projectchats.folder_of(conn, folder_proj)
    assert main(["project", "rm", "tokenbar", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "deleted tokenbar" in out
    assert "stopped: 0 runs, 0 goals, 0 items, 0 standing goals, 0 asks" in out
    assert "archived: 1 chat" in out
    assert out.strip().endswith(f"your folder {folder} is not touched")
    assert projects.get(conn, folder_proj)["state"] == "deleted"
    assert folder_proj not in [e["id"] for e in projectchats.listing(conn)]


def test_rm_asks_once_and_n_keeps_it(conn, capsys, folder_proj, monkeypatch):
    asked = []
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or "n")
    assert main(["project", "rm", "tokenbar"]) == 1
    assert asked == ["Delete tokenbar? [y/N] "]
    assert "archives its 1 chats" in capsys.readouterr().out
    assert projects.get(conn, folder_proj)["state"] != "deleted"


def test_rm_eki_is_refused(conn, capsys, folder_proj):
    assert main(["project", "rm", "eki", "--yes"]) == 1
    assert capsys.readouterr().err.startswith("eki: eki can't be deleted")


def test_rm_unknown_says_so(conn, capsys, folder_proj):
    assert main(["project", "rm", "nope", "--yes"]) == 1
    assert "no project named nope" in capsys.readouterr().err
