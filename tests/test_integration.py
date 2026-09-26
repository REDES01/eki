import os
from pathlib import Path

import pytest

from eki import integration, paths, workspace


def commit(where, name, text="x\n", message=None):
    (Path(where) / name).write_text(text)
    workspace.git(where, "add", "-A")
    workspace.git(where, "commit", "-q", "-m", message or f"add {name}")
    return workspace.head(where)


@pytest.fixture
def origin(tmp_path):
    o = tmp_path / "origin.git"
    workspace.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(o))
    seed = tmp_path / "seed"
    workspace.git(tmp_path, "clone", "-q", str(o), str(seed))
    workspace.git(seed, "checkout", "-q", "-b", "main")
    commit(seed, "a.py", message="first")
    workspace.git(seed, "push", "-q", "origin", "main")
    return o


@pytest.fixture
def src(tmp_path, origin, monkeypatch):
    s = tmp_path / "src"
    workspace.git(tmp_path, "clone", "-q", str(origin), str(s))
    monkeypatch.setenv("EKI_SOURCE", str(s))
    return s


def on_branch(name, fname):
    """A commit in the integration repo on a branch of its own, main left where it was."""
    r = integration.repo()
    wt = paths.home() / "wt"
    workspace.git(r, "worktree", "add", "-q", "-b", name, str(wt), "main")
    sha = commit(wt, fname)
    workspace.git(r, "worktree", "remove", "--force", str(wt))
    return sha


def test_repo_is_cloned_once_with_origin_rerere_and_venv(src, origin):
    (src / ".venv").mkdir()
    r = integration.repo()
    assert r == paths.home() / "self" / "repo" and (r / ".git").is_dir()
    assert workspace.git(r, "remote", "get-url", "origin") == str(origin)
    assert workspace.git(r, "remote", "get-url", "source") == str(src)
    assert workspace.git(r, "config", "rerere.enabled") == "true"
    assert workspace.git(r, "symbolic-ref", "--short", "HEAD") == "main"
    assert (r / ".venv").is_symlink() and os.readlink(r / ".venv") == str(src / ".venv")
    assert workspace.git(r, "status", "--porcelain") == ""
    assert integration.main() == workspace.head(src)
    assert workspace.git(r, "rev-parse", "origin/main") == workspace.head(src)
    commit(r, "b.py")                                 # made once: a second call keeps it
    assert integration.repo() == r and (r / "b.py").exists()


def test_a_half_made_repo_is_made_again(src):
    half = paths.home() / "self" / "repo"
    half.mkdir(parents=True)
    (half / "junk").write_text("x")
    r = integration.repo()
    assert (r / ".git").exists() and not (r / "junk").exists()


def test_no_origin_falls_back_to_the_source_path(tmp_path, monkeypatch):
    s = tmp_path / "lone"
    s.mkdir()
    workspace.git(s, "init", "-q", "-b", "main")
    commit(s, "a.py")
    monkeypatch.setenv("EKI_SOURCE", str(s))
    assert workspace.git(integration.repo(), "remote", "get-url", "origin") == str(s)


def test_fast_forward_moves_main_and_refuses_a_non_ff(src):
    start = integration.main()
    ahead = on_branch("ahead", "b.py")
    integration.fast_forward(ahead)
    assert integration.main() == ahead and (integration.repo() / "b.py").exists()
    integration.fast_forward(ahead)                   # already there: no-op
    r = integration.repo()
    wt = paths.home() / "side"
    workspace.git(r, "worktree", "add", "-q", "-b", "side", str(wt), start)
    side = commit(wt, "c.py")
    with pytest.raises(integration.IntegrationError):
        integration.fast_forward(side)
    assert integration.main() == ahead
    with pytest.raises(integration.IntegrationError):
        integration.fast_forward("0" * 40)


def test_contains_works_both_ways(src):
    start = integration.main()
    ahead = on_branch("ahead", "b.py")
    assert integration.contains(start, ahead) and not integration.contains(ahead, start)
    assert integration.contains(start) and not integration.contains(ahead)
    integration.fast_forward(ahead)
    assert integration.contains(ahead) and integration.contains(start)
    assert not integration.contains("f" * 40) and not integration.contains("")


def test_push_puts_main_in_origin_and_fails_quietly(src, origin, tmp_path):
    assert integration.push() is True                 # already there
    ahead = on_branch("ahead", "b.py")
    integration.fast_forward(ahead)
    assert integration.push() is True
    assert workspace.git(origin, "rev-parse", "main") == ahead
    integration.fast_forward(on_branch("more", "c.py"))
    origin.rename(tmp_path / "gone.git")
    assert integration.push() is False


def test_update_source_moves_only_a_clean_source_on_main(src):
    before = workspace.head(src)
    ahead = on_branch("ahead", "b.py")
    integration.fast_forward(ahead)

    (src / "a.py").write_text("dirty\n")
    assert integration.update_source() is False and workspace.head(src) == before
    workspace.git(src, "checkout", "-q", "--", "a.py")

    workspace.git(src, "checkout", "-q", "-b", "other")
    assert integration.update_source() is False and workspace.head(src) == before
    workspace.git(src, "checkout", "-q", "main")

    assert integration.update_source() is True
    assert workspace.head(src) == ahead and (src / "b.py").exists()
    assert integration.update_source() is False       # already there


def test_update_source_leaves_a_source_with_its_own_commits(src):
    integration.fast_forward(on_branch("ahead", "b.py"))
    mine = commit(src, "mine.py")
    assert integration.update_source() is False and workspace.head(src) == mine


def test_sync_picks_up_origin_and_the_source(src, origin, tmp_path):
    integration.repo()
    other = tmp_path / "someone"
    workspace.git(tmp_path, "clone", "-q", str(origin), str(other))
    theirs = commit(other, "theirs.py")
    workspace.git(other, "push", "-q", "origin", "main")
    assert integration.sync() == theirs

    workspace.git(src, "pull", "-q", "--ff-only")
    mine = commit(src, "mine.py")
    assert integration.sync() == mine

    diverged = commit(other, "diverged.py")           # origin moves off main's line
    workspace.git(other, "push", "-q", "origin", "main")
    rebased = integration.sync()                      # main is rebased onto it (test_integration_diverge.py)
    assert integration.contains(diverged) and integration.carries(mine) and rebased != mine


def test_sync_survives_an_unreachable_origin(src, origin, tmp_path):
    integration.repo()
    origin.rename(tmp_path / "gone.git")
    mine = commit(src, "mine.py")
    assert integration.sync() == mine
