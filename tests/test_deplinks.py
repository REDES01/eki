"""Dependencies stay links: the build brief says so, and eki puts back a node_modules an agent installed."""
import json
import os
from pathlib import Path

import pytest

from eki import deplinks, projectbrief, selfbrief, selfview, selfwork, workspace
from conftest import run_inline
from test_project_goals import planned, says, src  # noqa: F401  (src is a fixture)

RULE = "Never run `npm install`, `pip install` or any other install inside this"


def source_with(tmp_path, *names):
    s = tmp_path / "source"
    for name in names:
        (s / name).mkdir(parents=True)
        (s / name / "marker").write_text(f"{name}\n")
    return s


def test_a_node_modules_folder_is_relinked_to_the_source(tmp_path):
    s = source_with(tmp_path, "node_modules", ".venv")
    wt = tmp_path / "wt"
    (wt / "node_modules" / "left-pad").mkdir(parents=True)          # npm install replaced the link
    (wt / "node_modules" / "left-pad" / "index.js").write_text("x\n")
    os.symlink(s / ".venv", wt / ".venv")                            # still a link: untouched
    assert deplinks.restore(wt, s) == ["node_modules"]
    assert (wt / "node_modules").is_symlink()
    assert Path(os.readlink(wt / "node_modules")) == s / "node_modules"
    assert (wt / "node_modules" / "marker").read_text() == "node_modules\n"
    assert (wt / ".venv").is_symlink() and Path(os.readlink(wt / ".venv")) == s / ".venv"


def test_nothing_to_restore(tmp_path):
    s = source_with(tmp_path, "node_modules")
    (s / "venv-real").mkdir()
    os.symlink(s / "venv-real", s / "venv")                          # a source that is itself a link
    wt = tmp_path / "wt"
    (wt / "venv").mkdir(parents=True)
    os.symlink(s / "node_modules", wt / "node_modules")
    (wt / ".venv").mkdir()                                           # no .venv in the source
    assert deplinks.restore(wt, s) == []
    assert (wt / "venv").is_dir() and not (wt / "venv").is_symlink()
    assert (wt / ".venv").is_dir() and not (wt / ".venv").is_symlink()


def test_a_folder_that_cant_be_removed_is_skipped(tmp_path, monkeypatch):
    s = source_with(tmp_path, "node_modules")
    wt = tmp_path / "wt"
    (wt / "node_modules").mkdir(parents=True)

    def nope(p):
        raise PermissionError("no")
    monkeypatch.setattr(deplinks.shutil, "rmtree", nope)
    assert deplinks.restore(wt, s) == []
    assert (wt / "node_modules").is_dir() and not (wt / "node_modules").is_symlink()


def test_both_build_briefs_say_never_install():
    for brief in (selfbrief.BUILD, projectbrief.BUILD):
        assert RULE in brief and "node_modules and .venv are links" in brief
        assert "eki installs in the project folder" in brief


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    (r / "README.md").write_text("# proj\n")
    (r / "package.json").write_text('{"dependencies": {}}\n')
    (r / "node_modules" / "dep").mkdir(parents=True)                 # untracked: the person's install
    (r / "node_modules" / "dep" / "index.js").write_text("dep\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "app.py", "README.md", "package.json")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_a_project_item_that_installed_gets_the_link_back(conn, src, proj, tmp_path, monkeypatch):  # noqa: F811
    _, it = planned(conn, tmp_path, monkeypatch, proj, "true")
    wt = Path(it["worktree"])
    assert (wt / "node_modules").is_symlink()
    says(tmp_path, monkeypatch, "SUMMARY: added left-pad.\n\nITEM: done\n")
    run_inline(conn, it["run_id"])
    (wt / "node_modules").unlink()                                   # the agent ran npm install
    (wt / "node_modules" / "left-pad").mkdir(parents=True)
    (wt / "node_modules" / "left-pad" / "index.js").write_text("pad\n")
    (wt / "package.json").write_text('{"dependencies": {"left-pad": "1"}}\n')
    said = selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "judging"
    assert it["why"] == ("relinked node_modules: the agent installed inside the worktree;"
                         " dependencies come from the project folder")
    assert any(it["why"] in s for s in said)
    assert (wt / "node_modules").is_symlink() and (wt / "node_modules" / "dep" / "index.js").exists()
    assert json.loads(it["touched"]) == ["package.json"]             # the package change is still the item's
    shown = workspace.git(proj, "show", "--stat", "--format=", it["commit_sha"])
    assert "package.json" in shown and "node_modules" not in shown
    assert selfview.said(it, c=conn) == it["why"]                    # judging says nothing else here
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed"
    line = selfview.said(it, c=conn)
    assert line.startswith("✓ 1 files; ") and line.endswith(" · " + it["why"])
