"""`eki self show|diff <item>` on an item of a person's own project."""
import pytest

from eki import db, projects, selfwork, store, workspace
from eki.cli import main
import eki.cli.self as self_cmd


@pytest.fixture(autouse=True)
def no_engine(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)


@pytest.fixture
def item(conn, tmp_path):
    r = tmp_path / "garden"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    base = workspace.git(r, "rev-parse", "HEAD")
    workspace.git(r, "checkout", "-q", "-b", "eki/x")
    (r / "app.py").write_text("print('hello')\n")
    workspace.git(r, "commit", "-q", "-am", "hello")
    sha = workspace.git(r, "rev-parse", "HEAD")
    workspace.git(r, "checkout", "-q", "trunk")
    pid = projects.add(conn, r)
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project)"
                     " VALUES (?,?,?,?,?,?,?)", (gid, "tidy the garden", "standing", "eki", "planned", db.now(), pid))
        iid = selfwork.new_item(conn, gid, "say hello", "do it", ["app.py"], [], False)
        selfwork._set(conn, iid, state="proposed", branch="eki/x", base=base, commit_sha=sha)
    return iid, r.resolve()


def test_show_names_the_project_and_the_branch(conn, capsys, item):
    iid, path = item
    assert main(["self", "show", iid]) == 0
    out = capsys.readouterr().out
    assert "project:  garden" in out and str(path) in out
    assert "branch eki/x" in out
    assert f"git -C {path} merge eki/x" in out


def test_diff_reads_the_project_repo(conn, capsys, item):
    iid, _ = item
    assert main(["self", "diff", iid]) == 0
    out = capsys.readouterr().out
    assert "-print('hi')" in out and "+print('hello')" in out
