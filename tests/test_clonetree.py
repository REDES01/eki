import os
import sys
from pathlib import Path

import pytest

from eki import clonetree, paths, rebase, workspace
from eki.workspace import git


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src" / "deep").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    (r / "src" / "deep" / "b.bin").write_bytes(bytes(range(256)) * 64)
    (r / "run.sh").write_text("#!/bin/sh\necho hi\n")
    (r / "run.sh").chmod(0o755)
    os.symlink("src/deep/b.bin", r / "link")
    (r / ".gitignore").write_text("ignored/\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def _tree(root: Path) -> dict:
    """Every path under root but .git: its kind, bytes and executable bit."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files + [d for d in dirs if Path(dirpath, d).is_symlink()]:
            p = Path(dirpath, name)
            rel = str(p.relative_to(root))
            if rel == ".git":
                continue
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            else:
                out[rel] = ("file", p.read_bytes(), os.access(p, os.X_OK))
    return out


def _plain(repo, tmp_path, sha) -> Path:
    ref = tmp_path / "plain"
    git(repo, "worktree", "add", "-q", "--detach", str(ref), sha)
    return ref


def _fresh(repo, sha) -> Path:
    dest = paths.work() / "fill"
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--no-checkout", "--detach", str(dest), sha)
    return dest


def test_a_cloned_worktree_is_a_plain_checkout_byte_for_byte(repo, tmp_path):
    sha = workspace.head(repo)
    dest = _fresh(repo, sha)
    mode = clonetree.fill(repo, dest, sha)
    if sys.platform == "darwin":
        assert mode == "cloned"
    assert _tree(dest) == _tree(_plain(repo, tmp_path, sha))
    assert (dest / "link").is_symlink() and os.access(dest / "run.sh", os.X_OK)
    assert git(dest, "status", "--porcelain") == ""
    assert workspace.head(dest) == sha


def test_changes_and_untracked_files_in_the_source_never_come_along(repo, tmp_path):
    sha = workspace.head(repo)
    (repo / "a.txt").write_text("changed, not committed\n")
    (repo / "extra.txt").write_text("untracked\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x").write_text("ignored\n")
    dest = _fresh(repo, sha)
    mode = clonetree.fill(repo, dest, sha)
    if sys.platform == "darwin":
        assert mode == "partly cloned (1 checked out)"
    assert (dest / "a.txt").read_text() == "one\n"
    assert not (dest / "extra.txt").exists() and not (dest / "ignored").exists()
    assert _tree(dest) == _tree(_plain(repo, tmp_path, sha))
    assert git(dest, "status", "--porcelain") == ""


@pytest.mark.parametrize("how", ["switch", "clone fails"])
def test_without_cloning_a_plain_checkout_just_as_good(repo, tmp_path, monkeypatch, how):
    if how == "switch":
        monkeypatch.setattr(clonetree, "CLONE", False)
    else:
        def nope(src, dst):
            raise OSError(45, "Operation not supported")
        monkeypatch.setattr(clonetree, "_clone", nope)
    sha = workspace.head(repo)
    dest = _fresh(repo, sha)
    assert clonetree.fill(repo, dest, sha) == "checkout"
    assert _tree(dest) == _tree(_plain(repo, tmp_path, sha))
    assert git(dest, "status", "--porcelain") == ""


def test_add_on_an_existing_branch_resets_it_and_fills(repo):
    first = workspace.head(repo)
    git(repo, "branch", "eki/old")
    (repo / "a.txt").write_text("two\n")
    git(repo, "commit", "-qam", "second")
    later = workspace.head(repo)
    wt = workspace.add(repo, "old", base=later)
    assert first != later and workspace.head(wt) == later
    assert git(wt, "branch", "--show-current") == "eki/old"
    assert (wt / "a.txt").read_text() == "two\n"
    assert git(wt, "status", "--porcelain") == ""


def test_add_finishes_a_worktree_killed_before_its_fill(repo):
    sha = workspace.head(repo)
    dest = paths.work() / "half"
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--no-checkout", "-b", "eki/half", str(dest), sha)
    assert workspace.add(repo, "half") == dest
    assert (dest / "a.txt").read_text() == "one\n"
    assert git(dest, "status", "--porcelain") == ""


def test_a_failing_fill_never_fails_add(repo, monkeypatch):
    def broken(src, dest, sha):
        raise RuntimeError("boom")
    monkeypatch.setattr(clonetree, "fill", broken)
    wt = workspace.add(repo, "k")
    assert (wt / "a.txt").read_text() == "one\n"
    assert git(wt, "status", "--porcelain") == ""


def test_gate_tree_is_filled_and_clean(repo):
    (repo / "node_modules").mkdir()
    sha = workspace.head(repo)
    t = rebase.gate_tree(repo, "gate-c", sha)
    assert workspace.head(t) == sha and (t / "run.sh").read_text().startswith("#!")
    assert (t / "node_modules").is_symlink()
    assert not git(t, "status", "--porcelain", "--", ".", *workspace.NOT_LINKED)
