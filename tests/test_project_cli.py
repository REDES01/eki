"""`eki project <owner>/<repo>`: show it, set the check or branch, retry a setup fault."""
import json
import subprocess

import pytest

from conftest import run_inline
from eki import projects, projectsetup, store, workspace
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
