"""prmirror: eki's own landed items as read-only PRs against eki/landed."""
import json
import subprocess

import pytest

from eki import db, integration, paths, prmirror, selfwork, train, workspace

URL = "https://github.com/me/eki.git"
PR = "https://github.com/me/eki/pull/7"


def mirror(on):
    p = paths.config("routing")
    got = json.loads(p.read_text())
    got["self"] = {"pr_mirror": on, "issues_minutes": 0}
    p.write_text(json.dumps(got))


@pytest.fixture
def src(tmp_path, monkeypatch, fake_gh):
    """A source checkout whose origin is github.com/me/eki, every fetch and push landing in a bare repo."""
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "engine.py").write_text("# the engine\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    bare = tmp_path / "eki.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(r), str(bare)], check=True, capture_output=True)
    workspace.git(r, "remote", "add", "origin", URL)
    for k, v in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", f"url.{bare}.insteadOf"),
                 ("GIT_CONFIG_VALUE_0", URL)):
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EKI_SOURCE", str(r))
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], "me")
    fake_gh.bare = bare
    workspace.git(integration.repo(), "config", "remote.origin.url", URL)   # get-url gave the bare path
    return r


def land(conn, monkeypatch, name="a"):
    """A commit on integration main, the build running it, and an own item landed with it."""
    r = integration.repo()
    (r / "eki" / f"{name}.py").write_text(f"# {name}\n")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", f"self: {name}")
    sha = workspace.head(r)
    monkeypatch.setattr(train, "running_commit", lambda: sha)
    iid = selfwork.new_item(conn, "g1", f"do {name}", name, [], [], False)
    conn.execute("UPDATE items SET state='landed', rebased=?, commit_sha=?, landed_at=?, build=? "
                 "WHERE id=?", (sha, sha, db.now(), "b1", iid))
    return iid, sha


def row(conn, iid):
    return conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()


def in_bare(gh, ref):
    return workspace.git(gh.bare, "rev-parse", "--verify", "-q", ref, check=False)


def test_off_means_no_gh_at_all(conn, src, fake_gh, monkeypatch):
    mirror(False)
    land(conn, monkeypatch)
    assert prmirror.tick(conn) == []
    assert fake_gh.calls() == []


def test_a_project_item_is_not_mirrored(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, _ = land(conn, monkeypatch)
    conn.execute("INSERT INTO goals(id, text, project, created_at) VALUES ('g1', 'x', 'p1', ?)", (db.now(),))
    prmirror.tick(conn)
    assert not any(c[:2] == ["pr", "create"] for c in fake_gh.calls())
    assert row(conn, iid)["pr"] is None


def test_landed_opens_a_pr_and_live_closes_it(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, sha = land(conn, monkeypatch)
    fake_gh.answer(["pr", "create"], f"Creating…\n{PR}\n")
    said = prmirror.tick(conn)
    assert any(PR in s for s in said)
    create = [c for c in fake_gh.calls() if c[:2] == ["pr", "create"]]
    assert len(create) == 1
    argv = create[0]
    assert argv[argv.index("--base") + 1] == "eki/landed"
    assert argv[argv.index("--head") + 1] == f"eki/{iid}"
    assert argv[argv.index("--title") + 1] == "[landed] do a"
    body = argv[argv.index("--body") + 1]
    assert sha[:7] in body and "Don't merge." in body and body.endswith("<!-- eki -->")
    assert in_bare(fake_gh, f"refs/heads/eki/{iid}") == sha
    assert in_bare(fake_gh, "refs/heads/eki/landed") == sha
    it = row(conn, iid)
    assert (it["pr"], it["pr_state"], it["pushed"]) == (PR, "open", sha)
    assert not any("--force" in c or "-f" in c for c in fake_gh.calls())

    prmirror.tick(conn)                                   # nothing new: no second PR
    assert len([c for c in fake_gh.calls() if c[:2] == ["pr", "create"]]) == 1

    conn.execute("UPDATE items SET state='live' WHERE id=?", (iid,))
    fake_gh.answer(["pr", "view"], json.dumps({"state": "OPEN", "comments": [], "reviews": []}))
    prmirror.tick(conn)
    close = [c for c in fake_gh.calls() if c[:2] == ["pr", "close"]]
    assert len(close) == 1 and close[0][2] == PR
    assert close[0][close[0].index("--comment") + 1].startswith("already live in build b1")
    assert row(conn, iid)["pr_state"] == "closed"


def test_rolled_back_closes_with_the_reason(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, _ = land(conn, monkeypatch)
    fake_gh.answer(["pr", "create"], PR + "\n")
    prmirror.tick(conn)
    conn.execute("UPDATE items SET state='rolled back', error='exit 1 after 3s' WHERE id=?", (iid,))
    prmirror.tick(conn)
    close = [c for c in fake_gh.calls() if c[:2] == ["pr", "close"]]
    assert len(close) == 1
    assert close[0][close[0].index("--comment") + 1].startswith("rolled back: exit 1 after 3s")
    assert row(conn, iid)["pr_state"] == "closed"


def test_merged_on_github_is_recorded_without_a_close(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, _ = land(conn, monkeypatch)
    fake_gh.answer(["pr", "create"], PR + "\n")
    prmirror.tick(conn)
    conn.execute("UPDATE items SET state='live' WHERE id=?", (iid,))
    fake_gh.answer(["pr", "view"], json.dumps({"state": "MERGED", "mergedAt": "2026-09-28T00:00:00Z"}))
    prmirror.tick(conn)
    assert not any(c[:2] == ["pr", "close"] for c in fake_gh.calls())
    assert row(conn, iid)["pr_state"] == "merged"


def test_an_existing_pr_is_reused_and_a_stale_landing_is_left(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, _ = land(conn, monkeypatch)
    old, _ = land(conn, monkeypatch, "b")
    conn.execute("UPDATE items SET landed_at=? WHERE id=?", (db.now() - 2 * 86400, old))
    fake_gh.answer(["pr", "list"], json.dumps([{"url": PR, "number": 7, "state": "OPEN"}]))
    prmirror.tick(conn)
    assert not any(c[:2] == ["pr", "create"] for c in fake_gh.calls())
    assert row(conn, iid)["pr"] == PR and row(conn, old)["pr"] is None


def test_a_rejected_landed_push_is_a_line_not_a_force(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, sha = land(conn, monkeypatch)
    fake_gh.answer(["pr", "create"], PR + "\n")
    prmirror.tick(conn)
    r = integration.repo()                                # a commit off to the side: not a fast-forward
    workspace.git(r, "checkout", "-q", "-b", "side", f"{sha}~1")
    (r / "side.txt").write_text("x\n")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "side")
    side = workspace.head(r)
    workspace.git(r, "checkout", "-q", "main")
    monkeypatch.setattr(train, "running_commit", lambda: side)
    said = prmirror.tick(conn)
    assert any("couldn't move eki/landed" in s for s in said)
    assert in_bare(fake_gh, "refs/heads/eki/landed") == sha


def test_a_gh_failure_on_one_item_is_a_line(conn, src, fake_gh, monkeypatch):
    mirror(True)
    iid, _ = land(conn, monkeypatch)
    fake_gh.answer(["pr", "create"], "boom: no permission\n", code=1)
    said = prmirror.tick(conn)
    assert any("boom: no permission" in s for s in said)
    assert row(conn, iid)["pr"] is None
