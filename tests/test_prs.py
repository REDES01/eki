"""PRs out: a proposed project item on a GitHub-path project becomes a pull request, and the PR's fate settles it."""
import json
from pathlib import Path

import pytest

from eki import github, projects, projectwork, prs, selfwork, workspace
from conftest import run_inline
from test_project_goals import built, planned, src  # noqa: F401 — src is a fixture

PR = "https://github.com/me/proj/pull/7"


@pytest.fixture(autouse=True)
def always_due(monkeypatch):
    monkeypatch.setattr(github, "due", lambda name, now=None: True)


def proposed(conn, tmp_path, monkeypatch, proj, check=None, repo=None, check_from=None):
    pid, it = planned(conn, tmp_path, monkeypatch, proj, check)
    if repo:                                   # as eki's own clone of `repo` would be
        conn.execute("UPDATE projects SET repo=?, check_from=? WHERE id=?", (repo, check_from, pid))
    it = built(conn, tmp_path, monkeypatch, it)
    if check:
        run_inline(conn, it["run_id"])
        selfwork.tick(conn)
        it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed"
    return pid, it


def creates(gh):
    return [c for c in gh.calls() if c[:2] == ["pr", "create"]]


def arg(call, flag):
    return call[call.index(flag) + 1]


def test_a_built_item_becomes_a_pr(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], f"Creating pull request\n{PR}\n")
    pid, it = proposed(conn, tmp_path, monkeypatch, proj)
    conn.execute("UPDATE goals SET issue=12 WHERE id=?", (it["goal_id"],))
    said = prs.tick(conn)
    assert any(PR in s for s in said)
    sha = workspace.git(gh_repo.bare, "rev-parse", f"refs/heads/eki/{it['id']}")
    assert sha == it["commit_sha"]
    [call] = creates(gh_repo)
    assert arg(call, "--base") == "trunk" and arg(call, "--head") == f"eki/{it['id']}"
    assert arg(call, "--repo") == "me/proj" and arg(call, "--title") == it["title"]
    body = arg(call, "--body")
    assert "made greet." in body and "- `greet.py`" in body and projectwork.NOT_JUDGED in body
    assert "Closes #12" in body and body.rstrip().endswith(github.MARK)
    it = selfwork.store_item(conn, it["id"])
    assert (it["pr"], it["pr_state"], it["pushed"]) == (PR, "open", it["commit_sha"])
    assert it["pr_seen"].endswith("+00:00") and it["why"] is None
    assert not [c for c in gh_repo.calls() if "--force" in c]
    prs.tick(conn)                                                      # polled; no second create
    assert len(creates(gh_repo)) == 1


def test_the_check_tail_and_part_of_while_another_item_is_unbuilt(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], PR)
    _, it = proposed(conn, tmp_path, monkeypatch, proj, check="echo all-green-here")
    conn.execute("UPDATE goals SET issue=5 WHERE id=?", (it["goal_id"],))
    selfwork.new_item(conn, it["goal_id"], "More", "more", [], [], False)
    prs.tick(conn)
    body = arg(creates(gh_repo)[0], "--body")
    assert "Part of #5" in body and "Closes" not in body
    assert "```" in body and "all-green-here" in body and projectwork.NOT_JUDGED not in body


def test_a_repo_project_with_no_check_says_how_to_set_one(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], PR)
    (proj / "bin").mkdir()
    (proj / "bin" / "check").write_text("#!/bin/sh\nexit 1\n")
    (proj / "bin" / "check").chmod(0o755)                   # not used: the repo project's check is check_cmd only
    _, it = proposed(conn, tmp_path, monkeypatch, proj, repo="me/proj", check_from="none: no check found")
    line = "not judged: no check found; set one with `eki project me/proj --check '…'`"
    assert it["verdict"] == line and it["judged_by"] is None
    prs.tick(conn)
    body = arg(creates(gh_repo)[0], "--body")
    assert f"Check: {line}" in body and projectwork.NOT_JUDGED not in body


def test_a_repo_projects_check_is_named(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], PR)
    _, it = proposed(conn, tmp_path, monkeypatch, proj, check="echo make-x-green", repo="me/proj", check_from="set")
    assert it["judged_by"] == "echo make-x-green (set)"
    prs.tick(conn)
    body = arg(creates(gh_repo)[0], "--body")
    assert "Check: `echo make-x-green` (set)\n```" in body and "make-x-green\n```" in body


def test_a_guessed_check_says_so(conn):
    assert prs._named("npm run test (guessed: package.json script test via npm)") == \
        "Check: `npm run test` (guessed: package.json script test via npm)"
    assert prs._named("make x") == "Check: `make x`" and prs._named(None) == "Check:"


def test_an_existing_pr_is_reused(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "list"], json.dumps([{"url": PR, "number": 7, "state": "OPEN"}]))
    _, it = proposed(conn, tmp_path, monkeypatch, proj)
    prs.tick(conn)
    assert not creates(gh_repo)
    assert selfwork.store_item(conn, it["id"])["pr"] == PR


def test_merged_on_github_is_applied(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], PR)
    _, it = proposed(conn, tmp_path, monkeypatch, proj)
    prs.tick(conn)
    gh_repo.answer(["pr", "view"], json.dumps({"state": "MERGED", "mergedAt": "2026-09-28T10:00:00Z",
                                               "comments": [], "reviews": []}))
    said = prs.tick(conn)
    got = selfwork.store_item(conn, it["id"])
    assert (got["state"], got["pr_state"]) == ("applied", "merged")
    assert not Path(it["worktree"]).exists() and not workspace.git(proj, "branch", "--list", it["branch"])
    assert any("merged on GitHub" in s for s in said)


def test_closed_on_github_is_dropped_with_the_last_comment(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], PR)
    _, it = proposed(conn, tmp_path, monkeypatch, proj)
    prs.tick(conn)
    gh_repo.answer(["pr", "view"], json.dumps({"state": "CLOSED", "comments": [
        {"body": "first thought"}, {"body": "not the way to do it\nmore"}], "reviews": []}))
    prs.tick(conn)
    got = selfwork.store_item(conn, it["id"])
    assert (got["state"], got["pr_state"]) == ("dropped", "closed")
    assert got["error"] == "PR closed without merging: not the way to do it"
    assert not Path(it["worktree"]).exists() and workspace.git(proj, "branch", "--list", it["branch"])


def test_dropping_in_eki_closes_the_pr(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], PR)
    _, it = proposed(conn, tmp_path, monkeypatch, proj)
    prs.tick(conn)
    selfwork.drop(conn, it["id"])
    prs.tick(conn)
    closes = [c for c in gh_repo.calls() if c[:2] == ["pr", "close"]]
    assert closes == [["pr", "close", PR, "--comment", f"dropped in eki {github.MARK}"]]
    assert selfwork.store_item(conn, it["id"])["pr_state"] == "closed"
    prs.tick(conn)
    assert len([c for c in gh_repo.calls() if c[:2] == ["pr", "close"]]) == 1


def test_a_gh_failure_is_said_and_retried(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    gh_repo.answer(["pr", "create"], "GraphQL: base branch missing\ndetails", code=1)
    _, it = proposed(conn, tmp_path, monkeypatch, proj)
    prs.tick(conn)
    got = selfwork.store_item(conn, it["id"])
    assert got["why"] == "GitHub: GraphQL: base branch missing" and got["pr"] is None
    gh_repo.answer(["pr", "create"], PR)
    prs.tick(conn)
    got = selfwork.store_item(conn, it["id"])
    assert got["pr"] == PR and got["why"] is None


def test_the_local_path_is_untouched(conn, src, proj, fake_gh, tmp_path, monkeypatch):
    _, it = proposed(conn, tmp_path, monkeypatch, proj)
    assert prs.tick(conn) == []
    assert fake_gh.calls() == []
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"
    workspace.git(proj, "merge", "-q", "--no-edit", it["branch"])
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "applied"
    assert fake_gh.calls() == []


def test_base_on_the_github_path_is_origins_tip(conn, proj, gh_repo, tmp_path):
    pid = projects.add(conn, proj)
    local = workspace.git(proj, "rev-parse", "refs/heads/trunk")
    other = tmp_path / "other"
    workspace.git(tmp_path, "clone", "-q", str(gh_repo.bare), str(other))
    (other / "new.txt").write_text("merged on GitHub\n")
    workspace.git(other, "add", "new.txt")
    workspace.git(other, "-c", "user.name=p", "-c", "user.email=p@x", "commit", "-q", "-m", "merged")
    workspace.git(other, "push", "-q", "origin", "trunk")
    tip = workspace.git(other, "rev-parse", "HEAD")
    assert projects.base(projects.get(conn, pid)) == tip != local
    assert workspace.git(proj, "rev-parse", "refs/heads/trunk") == local
