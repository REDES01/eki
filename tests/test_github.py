"""The GitHub groundwork: which path a project is on, every gh argv, pushes and fetches, the new columns."""
import shutil
import sqlite3

import pytest

from eki import db, github, housekeep, paths, projects, selfwork, standing, store, workspace


def origin(repo, url):
    workspace.git(repo, "remote", "add", "origin", url)


@pytest.mark.parametrize("url", ["https://github.com/me/proj.git", "https://github.com/me/proj",
                                 "https://x-token@github.com/me/proj.git", "ssh://git@github.com/me/proj.git",
                                 "git@github.com:me/proj.git", "git@github.com:me/proj"])
def test_repo_of_reads_github_urls(proj, url):
    origin(proj, url)
    assert github.repo_of(proj) == "me/proj"


@pytest.mark.parametrize("url", ["https://gitlab.com/me/proj.git", "git@example.com:me/proj.git", "/srv/proj.git",
                                 "https://github.com.evil.io/me/proj"])
def test_repo_of_gives_none_for_other_hosts(proj, url):
    origin(proj, url)
    assert github.repo_of(proj) is None


def test_repo_of_gives_none_without_origin(proj):
    assert github.repo_of(proj) is None


def test_path_of_off_github_runs_no_gh(proj, fake_gh):
    origin(proj, "https://gitlab.com/me/proj.git")
    assert github.path_of(proj) == (None, "origin isn't on GitHub")
    assert fake_gh.calls() == []


def test_path_of_logged_out(proj, fake_gh):
    origin(proj, "git@github.com:me/proj.git")
    fake_gh.answer(["auth", "status"], "You are not logged into any GitHub hosts.\n", 1)
    assert github.path_of(proj) == (None, "gh is not logged in")
    assert fake_gh.calls() == [["auth", "status"]]


def test_path_of_without_gh(proj, tmp_path, monkeypatch):
    origin(proj, "git@github.com:me/proj.git")
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(only_git))
    assert github.path_of(proj) == (None, "gh isn't installed")


def test_path_of_on_github(gh_repo, proj):
    assert github.path_of(proj) == ("me/proj", "GitHub me/proj")
    assert gh_repo.calls() == [["auth", "status"]]


def test_the_unplanned_gh_fails_loudly(proj):
    with pytest.raises(github.GhError, match="gh: not in tests"):
        github.whoami()


def test_every_gh_call_sends_its_argv(fake_gh):
    fake_gh.answer(["api", "user"], "me\n")
    fake_gh.answer(["issue", "list"], '[{"number": 3, "title": "t"}]')
    fake_gh.answer(["issue", "view"], "CLOSED\n")
    fake_gh.answer(["pr", "list"], '[{"url": "https://github.com/me/proj/pull/9", "number": 9}]')
    fake_gh.answer(["pr", "create"], "Creating…\nhttps://github.com/me/proj/pull/1\nhttps://github.com/me/proj/pull/2\n")
    fake_gh.answer(["pr", "view"], '{"state": "OPEN", "comments": []}')
    url = "https://github.com/me/proj/pull/2"
    assert github.whoami() == "me"
    assert github.issues("me/proj") == [{"number": 3, "title": "t"}]
    assert github.issue_state("me/proj", 3) == "CLOSED"
    assert github.pr_find("me/proj", "eki/abc")["number"] == 9
    assert github.pr_create("me/proj", "trunk", "eki/abc", "T", "B") == url
    assert github.pr_view(url) == {"state": "OPEN", "comments": []}
    github.pr_comment(url, "hi")
    github.pr_close(url, "bye")
    assert fake_gh.calls() == [
        ["api", "user", "--jq", ".login"],
        ["issue", "list", "--repo", "me/proj", "--label", "eki", "--state", "open", "--limit", "100",
         "--json", "number,title,body,labels,author"],
        ["issue", "view", "3", "--repo", "me/proj", "--json", "state", "--jq", ".state"],
        ["pr", "list", "--repo", "me/proj", "--head", "eki/abc", "--state", "all", "--json", "url,number,state",
         "--limit", "1"],
        ["pr", "create", "--repo", "me/proj", "--base", "trunk", "--head", "eki/abc", "--title", "T", "--body", "B"],
        ["pr", "view", url, "--json", "state,mergedAt,comments,reviews"],
        ["pr", "comment", url, "--body", "hi"],
        ["pr", "close", url, "--comment", "bye"],
    ]


def test_pr_find_none_and_a_failure_carries_the_first_stderr_line(fake_gh):
    fake_gh.answer(["pr", "list"], "[]")
    assert github.pr_find("me/proj", "eki/x") is None
    fake_gh.answer(["pr", "create"], "\npull request create failed: no commits\nmore\n", 1)
    with pytest.raises(github.GhError) as e:
        github.pr_create("me/proj", "trunk", "eki/x", "T", "B")
    assert str(e.value) == "pull request create failed: no commits"


def test_push_lands_an_eki_branch_and_refuses_the_rest(gh_repo, proj):
    workspace.git(proj, "branch", "eki/abc")
    github.push(proj, "eki/abc", "eki/abc")
    assert workspace.git(gh_repo.bare, "rev-parse", "refs/heads/eki/abc") == workspace.head(proj)
    with pytest.raises(ValueError):
        github.push(proj, "trunk", "trunk")
    with pytest.raises(ValueError):
        github.push(proj, "+eki/abc", "eki/abc")
    assert not any("--force" in c or any(a.startswith("+") for a in c) for c in gh_repo.calls())


def test_a_rejected_push_is_a_gh_error(gh_repo, proj):
    workspace.git(proj, "branch", "eki/abc")
    github.push(proj, "eki/abc", "eki/abc")
    workspace.git(proj, "checkout", "-q", "--orphan", "other")
    workspace.git(proj, "commit", "-q", "--allow-empty", "-m", "unrelated")
    with pytest.raises(github.GhError):
        github.push(proj, "HEAD", "eki/abc")               # not a fast-forward, and never forced


def test_fetch_updates_only_the_remote_ref(gh_repo, proj, tmp_path):
    other = tmp_path / "other"
    workspace.git(tmp_path, "clone", "-q", str(gh_repo.bare), str(other))
    (other / "new.txt").write_text("merged on GitHub\n")
    workspace.git(other, "add", "new.txt")
    workspace.git(other, "commit", "-q", "-m", "merged")
    workspace.git(other, "push", "-q", "origin", "trunk")
    before = workspace.git(proj, "rev-parse", "refs/heads/trunk")
    github.fetch(proj, "trunk")
    assert workspace.git(proj, "rev-parse", "refs/remotes/origin/trunk") == workspace.head(other)
    assert workspace.git(proj, "rev-parse", "refs/heads/trunk") == before


def test_due_honours_the_interval():
    t = 1000.0
    assert github.due("issues", t)
    assert not github.due("issues", t + 60)
    assert github.due("prs", t + 60)                      # each step keeps its own time
    assert github.due("issues", t + 600)
    assert not github.due("issues", t + 601)


NEW = [("goals", "issue"), ("standing", "issue"), ("projects", "issues"), ("items", "pr"),
       ("items", "pr_state"), ("items", "pushed"), ("items", "pr_seen"), ("items", "followups")]


def cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_the_new_columns_on_a_fresh_db(conn):
    for table, col in NEW:
        assert col in cols(conn, table)


def test_the_new_columns_on_an_old_db(home):
    paths.db().parent.mkdir(parents=True, exist_ok=True)
    old = sqlite3.connect(paths.db())
    old.executescript(db.SCHEMA)                         # the first release's tables, none of the new columns
    old.close()
    conn = db.connect()
    for table, col in NEW:
        assert col in cols(conn, table)
    pid = store.new_id()
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES (?,?,?,?,?)",
                 (pid, "p", "/nowhere", "main", 0))
    assert conn.execute("SELECT issues FROM projects").fetchone()["issues"] == 0


def test_submit_and_standing_keep_the_issue(conn, proj):
    pid = projects.add(conn, proj)
    gid = selfwork.submit(conn, "fix it", plan=False, project=pid, issue=7)
    assert conn.execute("SELECT issue FROM goals WHERE id=?", (gid,)).fetchone()["issue"] == 7
    sid = standing.add(conn, str(proj), "keep it tidy", issue=7)
    assert conn.execute("SELECT issue FROM standing WHERE id=?", (sid,)).fetchone()["issue"] == 7


def test_settings_default_the_github_knobs():
    got = selfwork.settings()
    assert (got["issues_minutes"], got["pr_followups"], got["pr_mirror"]) == (10, 3, False)


def test_open_prs_counts_only_proposed_and_open(conn, proj):
    pid = projects.add(conn, proj)
    gid = selfwork.submit(conn, "a", plan=False, project=pid)
    self_gid = store.new_id()
    conn.execute("INSERT INTO goals(id, text, created_at) VALUES (?,?,0)", (self_gid, "eki itself"))
    rows = [(gid, "proposed", "open"), (gid, "proposed", "open"), (gid, "proposed", "merged"),
            (gid, "applied", "open"), (gid, "proposed", None), (self_gid, "proposed", "open")]
    with db.tx(conn):
        for g, state, pr in rows:
            iid = selfwork.new_item(conn, g, "t", "s", [], [], False)
            conn.execute("UPDATE items SET state=?, pr_state=? WHERE id=?", (state, pr, iid))
    assert projects.open_prs(conn) == {"proj": 2}


def test_housekeep_runs_the_github_steps_after_standing_and_before_digest():
    names = [n for n, _ in housekeep.STEPS]
    at = names.index("standing")
    assert names[at + 1:at + 6] == ["projects", "issues", "prs", "prfollow", "prmirror"]
    assert names.index("prmirror") < names.index("digest")


def test_with_nothing_to_do_only_the_issue_search_asks_gh(conn, fake_gh, monkeypatch):
    from eki import issues, prfollow, prmirror, prs
    monkeypatch.setattr(github, "due", lambda name, now=None: True)
    fake_gh.answer(["api", "user"], "me")
    fake_gh.answer(["search", "issues"], "[]")
    for mod in (issues, prs, prfollow, prmirror):
        assert mod.tick(conn) == []
    assert [c[:2] for c in fake_gh.calls()] == [["api", "user"], ["search", "issues"]]


# ---- account-wide issues: the shared names ---------------------------------------------------

def test_search_issues_argv_and_parse(fake_gh):
    fake_gh.answer(["search", "issues"], '[{"number": 3, "repository": {"nameWithOwner": "me/proj"}}]')
    got = github.search_issues("me")
    assert got[0]["repository"]["nameWithOwner"] == "me/proj"
    assert fake_gh.calls()[-1] == ["search", "issues", "--label", "eki", "--state", "open", "--owner", "me",
                                   "--limit", "100", "--json", "number,title,body,labels,author,repository"]
    fake_gh.answer(["search", "issues"], "")
    assert github.search_issues("me") == []


def test_soon_makes_due_true_again():
    t = 1_000_000.0
    assert github.due("issues", t)
    assert not github.due("issues", t + 60)
    github.soon("issues")
    github.soon("never-seen")
    assert github.due("issues", t + 60)


def test_the_setup_columns(conn):
    for table, cols in (("projects", {"repo", "state", "setup", "setup_run", "fault", "check_from",
                                      "install_cmd", "ignored", "issues"}), ("items", {"judged_by"})):
        assert cols <= {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_clone_path_and_by_repo(conn, home):
    assert paths.projects() == home / "projects" and paths.projects().is_dir()
    assert projects.clone_path("me/proj") == home / "projects" / "me" / "proj"
    assert projects.by_repo(conn, "me/proj") is None
    for pid, path, state in (("old", "/a", "retired"), ("new", "/b", "dropped")):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, state) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, "me/proj", path, "main", db.now(), "me/proj", state))
    assert projects.by_repo(conn, "me/proj")["id"] == "new"
    conn.execute("DELETE FROM projects WHERE id='new'")
    assert projects.by_repo(conn, "me/proj") is None


def test_not_judged_both_ways(conn, proj):
    from eki import projectwork
    assert projectwork.not_judged(None) == projectwork.NOT_JUDGED
    local = projects.get(conn, projects.add(conn, proj))
    assert projectwork.not_judged(local) == projectwork.NOT_JUDGED
    conn.execute("UPDATE projects SET repo='me/proj' WHERE id=?", (local["id"],))
    assert projectwork.not_judged(projects.get(conn, local["id"])) == \
        "not judged: no check found; set one with `eki project me/proj --check '…'`"


def test_standing_add_on_a_given_project_skips_the_folder(conn):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo) "
                 "VALUES ('p1','me/proj','/x','main',?,'me/proj')", (db.now(),))
    sid = standing.add(conn, "/no/such/folder", "keep it tidy", project="p1")
    assert standing.find(conn, sid)["project"] == "p1"
    with pytest.raises(ValueError):
        standing.add(conn, "/no/such/folder", "keep it tidy")


def test_fake_gh_repo_clones_for_real(fake_gh, proj, tmp_path):
    bare = tmp_path / "bare.git"
    workspace.git(tmp_path, "clone", "-q", "--bare", str(proj), str(bare))
    fake_gh.repo("me/proj", bare)
    dest = tmp_path / "clone"
    github._gh(["repo", "clone", "me/proj", str(dest), "--", "--filter=blob:none"])
    assert (dest / "app.py").exists() and github.repo_of(dest) == "me/proj"
    assert workspace.git(dest, "config", "--get", "remote.origin.pushurl") == str(bare)
    assert workspace.git(dest, "symbolic-ref", "--short", "HEAD") == "trunk"
    assert fake_gh.calls()[-1][:3] == ["repo", "clone", "me/proj"]
    shutil.rmtree(bare)
    with pytest.raises(github.GhError):
        github._gh(["repo", "clone", "me/proj", str(tmp_path / "again")])
