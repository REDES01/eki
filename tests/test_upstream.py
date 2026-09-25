# SPDX-License-Identifier: Apache-2.0
"""Offering a change upstream (eki/upstream.py): a public repo that's a bare
git folder, this Mac's checkout a clone with work of its own, and `gh` a
stand-in. The real thing is `eki self offer <id>`."""
import subprocess
from pathlib import Path

import pytest

from eki import selfwork, upstream


@pytest.fixture(autouse=True)
def who(monkeypatch):
    for k in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{k}_NAME", "t")
        monkeypatch.setenv(f"GIT_{k}_EMAIL", "t@t")


def run(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(root: Path, name: str, text: str, msg: str) -> str:
    (root / name).write_text(text)
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", msg)
    return run(root, "rev-parse", "HEAD")


def setup(tmp_path: Path, local_touches_same_file: bool = False):
    """The public repo, and a checkout with a local-only commit under a change."""
    public = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(public)], check=True)
    mac = tmp_path / "mac"
    subprocess.run(["git", "clone", "-q", str(public), str(mac)], check=True, capture_output=True)
    run(mac, "checkout", "-q", "-b", "main")
    commit(mac, "a.txt", "one\n", "release")
    run(mac, "push", "-q", "origin", "main")
    # this Mac's own work, never upstream
    commit(mac, "b.txt" if not local_touches_same_file else "a.txt", "local\n", "local only")
    base = run(mac, "rev-parse", "HEAD")
    run(mac, "checkout", "-q", "-b", "self/abc123")
    sha = commit(mac, "a.txt" if local_touches_same_file else "c.txt", "the change\n", "self: the change")
    run(mac, "checkout", "-q", "main")
    home = tmp_path / "home"
    selfwork.record(selfwork.Proposal(id="abc123", request="make it better", root=str(mac), base=base,
                                      branch="self/abc123", commit=sha, fit=True, verdict="fit",
                                      summary="Better now."), home)
    return public, mac, home


def fake_gh(calls, existing=""):
    def gh(args, cwd):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return 0, existing, ""
        return 0, "https://github.com/o/eki/pull/7", ""
    return gh


def test_github_repo():
    assert upstream.github_repo("https://github.com/REDES01/eki.git") == "REDES01/eki"
    assert upstream.github_repo("git@github.com:me/eki.git") == "me/eki"
    assert upstream.github_repo("/tmp/public.git") == ""


def test_offer_pushes_only_the_changes_own_commits(tmp_path):
    public, mac, home = setup(tmp_path)
    got = upstream.offer("abc", home=home, gh=fake_gh([]))
    assert got["branch"] == "eki/abc123" and got["commits"] == 1
    files = run(public, "ls-tree", "--name-only", "eki/abc123").split()
    assert files == ["a.txt", "c.txt"]          # the local-only b.txt stayed on this Mac
    assert run(public, "log", "--format=%s", "main..eki/abc123") == "self: the change"
    assert got["url"] == "" and "isn't on GitHub" in got["why"]
    assert upstream.offers(home)["abc123"]["branch"] == "eki/abc123"
    # the checkout wasn't touched
    assert run(mac, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert run(mac, "status", "--porcelain") == ""


def test_offer_opens_a_pull_request_on_github(tmp_path):
    public, mac, home = setup(tmp_path)
    run(mac, "remote", "set-url", "origin", "https://github.com/o/eki.git")
    # fetched from and pushed to the stand-in, as GitHub isn't reachable here
    run(mac, "config", "url." + str(public) + ".insteadOf", "https://github.com/o/eki.git")
    calls = []
    got = upstream.offer("abc123", home=home, gh=fake_gh(calls))
    assert got["url"] == "https://github.com/o/eki/pull/7" and got["opened"]
    create = calls[-1]
    assert create[:2] == ["pr", "create"] and "--repo" in create and "o/eki" in create
    assert create[create.index("--head") + 1] == "eki/abc123"
    assert "Better now." in create[create.index("--body") + 1]
    # offered again: the same branch, the pull request already open
    calls.clear()
    again = upstream.offer("abc123", home=home, gh=fake_gh(calls, existing="https://github.com/o/eki/pull/7"))
    assert again["url"].endswith("/pull/7") and not again["opened"]
    assert all(c[:2] != ["pr", "create"] for c in calls)


def test_offer_without_gh_gives_the_page_to_open_it(tmp_path):
    public, mac, home = setup(tmp_path)
    run(mac, "remote", "set-url", "origin", "https://github.com/o/eki.git")
    run(mac, "config", "url." + str(public) + ".insteadOf", "https://github.com/o/eki.git")
    got = upstream.offer("abc123", home=home, gh=lambda a, c: (127, "", "gh isn't installed"))
    assert got["url"] == "" and "gh isn't installed" in got["why"]
    assert got["compare"] == "https://github.com/o/eki/compare/main...eki/abc123?expand=1"


def test_a_change_that_leans_on_local_work_is_not_pushed(tmp_path):
    public, mac, home = setup(tmp_path, local_touches_same_file=True)
    with pytest.raises(selfwork.SelfWorkError, match="only this Mac has, in a.txt"):
        upstream.offer("abc123", home=home, gh=fake_gh([]))
    assert "eki/abc123" not in run(public, "branch", "--list")
    assert run(mac, "worktree", "list").count("\n") == 0


def test_only_a_fit_change_is_offered(tmp_path):
    public, mac, home = setup(tmp_path)
    selfwork.set_state("abc123", "discarded", home)
    with pytest.raises(selfwork.SelfWorkError, match="discarded"):
        upstream.offer("abc123", home=home, gh=fake_gh([]))


def test_body_says_how_it_was_judged():
    body = upstream.body_of({"id": "x1", "summary": "New thing.", "request": "do it",
                             "report": {"checks": [{"name": "tests", "ok": True, "skipped": False,
                                                    "detail": "12 passed"}]},
                             "backend": "claude"})
    assert "New thing." in body and "> do it" in body and "- tests: ok 12 passed" in body
    assert "self/x1" in body and "eki self offer" in body


def test_cli_asks_before_publishing(tmp_path, monkeypatch, capsys):
    """Not from a terminal and no --yes: nothing is pushed."""
    from eki.cli import self_ as cli
    public, mac, home = setup(tmp_path)
    monkeypatch.setattr(selfwork, "HOME", home)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    offered = []
    monkeypatch.setattr(upstream, "offer", lambda *a, **k: offered.append(a))
    assert cli._self_offer(["abc123"], yes=False, as_json=False) == 1
    assert not offered and "--yes" in capsys.readouterr().err
