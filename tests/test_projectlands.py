"""Where a project's work lands: the gh calls, projects.lands, the card's merge, the tick and the routes."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from eki import api_projectlands, apiroutes, db, github, projectlands, projects, projectsetup, server, store, workspace


def origin(repo, url):
    workspace.git(repo, "remote", "add", "origin", url)


def signed_in(fake_gh, login="me"):
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], login)


def row(conn, pid):
    return projects.get(conn, pid)


def bare_folder(tmp_path, name="fresh"):
    """A git folder with no commit yet."""
    r = tmp_path / name
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    return r


def chat_row(conn, folder):
    """A project row on a folder, as the New project dialog makes one before it has a commit."""
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES (?,?,?,'',?)",
                     (pid, folder.name, str(folder), db.now()))
    return pid


def workflow_card(folder, **spec):
    d = folder / "components" / "workflow"
    d.mkdir(parents=True)
    full = {"kind": "workflow", "about": "how work runs", "router": "on", "plan": "split", "parallel": 2,
            "checks": ["self"], **spec}
    (d / "component.json").write_text(json.dumps(full, indent=2) + "\n")
    return d / "component.json", full


# ---- github.py -------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "me/proj", " me/proj ", "me/proj.git", "https://github.com/me/proj", "https://github.com/me/proj.git",
    "https://github.com/me/proj/", "http://www.github.com/me/proj", "github.com/me/proj",
    "https://github.com/me/proj/tree/main/src", "https://github.com/me/proj/pull/12",
    "https://github.com/me/proj?tab=readme", "ssh://git@github.com/me/proj.git", "git@github.com:me/proj.git",
    "git@github.com:me/proj"])
def test_named_takes_every_form(text):
    assert github.named(text) == "me/proj"


@pytest.mark.parametrize("text", ["", "proj", "me/proj/extra", "https://gitlab.com/me/proj",
                                  "https://github.com.evil.io/me/proj", "https://github.com/me", "me /proj",
                                  "-me/proj", "me/.."])
def test_named_refuses_the_rest(text):
    assert github.named(text) is None


def test_repo_list_argv_and_order(fake_gh):
    fake_gh.answer(["repo", "list"], json.dumps([{"nameWithOwner": "me/b"}, {"nameWithOwner": "me/a"}]))
    assert github.repo_list() == ["me/b", "me/a"]
    assert github.repo_list(5) == ["me/b", "me/a"]
    assert fake_gh.calls() == [["repo", "list", "--limit", "100", "--json", "nameWithOwner"],
                               ["repo", "list", "--limit", "5", "--json", "nameWithOwner"]]


def test_repo_create_is_private_and_pushes_only_with_a_commit(fake_gh, proj, tmp_path):
    fresh = bare_folder(tmp_path)
    origin(fresh, "https://github.com/me/fresh.git")      # what gh would leave
    assert github.repo_create("fresh", fresh) == "me/fresh"
    origin(proj, "git@github.com:me/proj.git")
    assert github.repo_create("proj", proj, private=False) == "me/proj"
    assert fake_gh.calls() == [
        ["repo", "create", "fresh", "--private", "--source", str(fresh), "--remote", "origin"],
        ["repo", "create", "proj", "--public", "--source", str(proj), "--remote", "origin", "--push"]]


def test_repo_create_with_no_origin_after_is_a_gh_error(fake_gh, proj):
    with pytest.raises(github.GhError, match="no GitHub origin"):
        github.repo_create("proj", proj)


def test_remote_add(proj):
    github.remote_add(proj, "me/proj")
    assert workspace.git(proj, "config", "--get", "remote.origin.url") == "https://github.com/me/proj.git"
    assert github.repo_of(proj) == "me/proj"


# ---- status ----------------------------------------------------------------------------------

def test_status_signed_in_lists_repos_and_the_folders_remote(fake_gh, proj, tmp_path, monkeypatch):
    signed_in(fake_gh)
    fake_gh.answer(["repo", "list"], json.dumps([{"nameWithOwner": "me/proj"}]))
    origin(proj, "https://github.com/me/proj.git")
    got = projectlands.status(str(proj))
    assert got == {"signed_in": True, "login": "me", "why": "", "repos": ["me/proj"], "remote": "me/proj"}
    monkeypatch.setenv("EKI_USER_HOME", str(tmp_path))
    assert projectlands.status("~/proj")["remote"] == "me/proj"
    assert projectlands.status("~/nowhere")["remote"] is None
    assert projectlands.status()["remote"] is None


def test_status_signed_out_and_a_failed_list(fake_gh, proj):
    fake_gh.answer(["auth", "status"], "You are not logged into any GitHub hosts", 1)
    got = projectlands.status(str(proj))
    assert got == {"signed_in": False, "login": "", "why": "gh is not logged in: run `gh auth login`",
                   "repos": [], "remote": None}
    signed_in(fake_gh)
    fake_gh.answer(["repo", "list"], "HTTP 502", 1)
    got = projectlands.status(str(proj))
    assert got["signed_in"] and got["repos"] == [] and "HTTP 502" in got["why"]


# ---- set_lands -------------------------------------------------------------------------------

def test_github_through_the_existing_origin(conn, gh_repo, proj):
    pid = projects.add(conn, proj)
    got = projectlands.set_lands(conn, pid, "github")
    assert got == {"lands": "github", "repo": "me/proj", "remote": "me/proj", "signed_in": True,
                   "can_github": True, "why": ""}
    r = row(conn, pid)
    assert (r["lands"], r["repo"], r["setup"], r["branch"], r["folder"]) == ("github", "me/proj", "ready", "trunk",
                                                                            str(proj))
    assert not any(c[:2] == ["repo", "create"] for c in gh_repo.calls())


def test_github_through_a_named_repo(conn, fake_gh, proj):
    signed_in(fake_gh)
    pid = projects.add(conn, proj)
    got = projectlands.set_lands(conn, pid, "github", repo="https://github.com/me/other/tree/main")
    assert got["lands"] == "github" and got["repo"] == "me/other" and got["remote"] == "me/other"
    assert workspace.git(proj, "config", "--get", "remote.origin.url") == "https://github.com/me/other.git"


def test_github_through_create(conn, fake_gh, proj, monkeypatch):
    signed_in(fake_gh)
    real = github._gh

    def gh(args, cwd=None):                              # gh repo create leaves the origin behind
        out = real(args, cwd)
        if args[:2] == ["repo", "create"]:
            origin(proj, f"https://github.com/me/{args[2]}.git")
        return out
    monkeypatch.setattr(github, "_gh", gh)
    pid = projects.add(conn, proj)
    got = projectlands.set_lands(conn, pid, "github", create=True)
    assert got["repo"] == "me/proj"
    assert ["repo", "create", "proj", "--private", "--source", str(proj), "--remote", "origin", "--push"] \
        in fake_gh.calls()


def test_every_refusal(conn, fake_gh, proj, tmp_path):
    pid = projects.add(conn, proj)
    fake_gh.answer(["auth", "status"], "not logged in", 1)
    with pytest.raises(ValueError, match="gh auth login"):
        projectlands.set_lands(conn, pid, "github", repo="me/proj")
    signed_in(fake_gh)
    with pytest.raises(ValueError, match="no GitHub remote"):
        projectlands.set_lands(conn, pid, "github")
    with pytest.raises(ValueError, match="isn't OWNER/REPO"):
        projectlands.set_lands(conn, pid, "github", repo="not a repo")
    with pytest.raises(ValueError, match="not 'gitlab'"):
        projectlands.set_lands(conn, pid, "gitlab")
    folder = bare_folder(tmp_path, "other")
    workspace.git(folder, "commit", "-q", "--allow-empty", "-m", "x")
    other = projects.add(conn, folder)
    projectlands.mark(conn, other, "github", "me/taken")
    with pytest.raises(ValueError, match="already the project other"):
        projectlands.set_lands(conn, pid, "github", repo="me/taken")
    assert workspace.git(proj, "config", "--get", "remote.origin.url", check=False) == ""   # nothing added
    origin(proj, "https://gitlab.com/me/proj.git")
    with pytest.raises(ValueError, match="isn't on GitHub"):
        projectlands.set_lands(conn, pid, "github")
    assert row(conn, pid)["repo"] is None and row(conn, pid)["lands"] is None


def test_here_clears_the_repo_and_refuses_ekis_own_clone(conn, gh_repo, proj):
    pid = projects.add(conn, proj)
    projectlands.set_lands(conn, pid, "github")
    got = projectlands.set_lands(conn, pid, "here")
    assert got["lands"] == "here" and got["repo"] is None and got["remote"] == "me/proj"
    assert (row(conn, pid)["lands"], row(conn, pid)["repo"]) == ("here", None)
    clone = projects.clone_path("me/clone")
    clone.mkdir(parents=True)
    workspace.git(clone, "init", "-q", "-b", "main")
    cid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, setup) "
                     "VALUES (?,?,?,'main',?,'me/clone','ready')", (cid, "me/clone", str(clone), db.now()))
    with pytest.raises(ValueError, match="eki's own clone"):
        projectlands.set_lands(conn, cid, "here")
    assert row(conn, cid)["repo"] == "me/clone"


def test_a_here_project_on_github_isnt_retired(conn, gh_repo, proj):
    pid = projects.add(conn, proj)
    projectlands.set_lands(conn, pid, "here")
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, state, created_at, project) VALUES ('g1','x','done',0,?)", (pid,))
    projectsetup.tick(conn)
    assert row(conn, pid)["state"] in (None, "on") and row(conn, pid)["repo"] is None


def test_lands_of_says_whats_missing(conn, proj):
    pid = projects.add(conn, proj)
    got = projectlands.lands_of(conn, pid)
    assert got["lands"] == "here" and not got["can_github"] and "gh auth login" in got["why"]


# ---- the card --------------------------------------------------------------------------------

def test_the_cards_merge_is_rewritten_and_nothing_else(conn, gh_repo, proj):
    f, spec = workflow_card(proj, merge="auto", command="make test")
    pid = projects.add(conn, proj)
    projectlands.set_lands(conn, pid, "github")
    assert json.loads(f.read_text()) == {**spec, "merge": "pr"}
    projectlands.set_lands(conn, pid, "here")
    assert json.loads(f.read_text()) == {**spec, "merge": "auto"}


def test_here_keeps_a_person_merge(conn, gh_repo, proj):
    f, spec = workflow_card(proj, merge="person")
    projectlands.mark(conn, projects.add(conn, proj), "here")
    assert json.loads(f.read_text()) == spec


# ---- tick ------------------------------------------------------------------------------------

def test_tick_fills_the_branch_once_a_commit_appears(conn, fake_gh, tmp_path):
    signed_in(fake_gh)
    folder = bare_folder(tmp_path)
    pid = chat_row(conn, folder)
    projectlands.set_lands(conn, pid, "github", repo="me/fresh")
    assert (row(conn, pid)["branch"], row(conn, pid)["setup"]) == ("", None)
    assert projectlands.tick(conn) == []
    (folder / "a.txt").write_text("a\n")
    workspace.git(folder, "add", "a.txt")
    workspace.git(folder, "commit", "-q", "-m", "first")
    assert projectlands.tick(conn) == ["fresh: first commit — branch main, ready for me/fresh"]
    assert (row(conn, pid)["branch"], row(conn, pid)["setup"]) == ("main", "ready")
    assert projectlands.tick(conn) == []


def test_tick_runs_in_housekeeping_after_projects():
    from eki import housekeep
    names = [n for n, _ in housekeep.STEPS]
    assert names[names.index("projects") + 1] == "lands"


# ---- the routes ------------------------------------------------------------------------------

@pytest.fixture
def srv(home):
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


def call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Eki": "1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_routes(conn, gh_repo, proj, srv):
    assert all(r in apiroutes.routes() for r in api_projectlands.ROUTES)
    gh_repo.answer(["repo", "list"], json.dumps([{"nameWithOwner": "me/proj"}]))
    code, got = call(f"{srv}/api/github?folder={proj}")
    assert code == 200 and got["repos"] == ["me/proj"] and got["remote"] == "me/proj"
    pid = projects.add(conn, proj)
    assert call(f"{srv}/api/projects/{pid}/lands")[1]["lands"] == "here"
    code, got = call(f"{srv}/api/projects/{pid}/lands", {"lands": "github"})
    assert code == 200 and got["lands"] == "github" and got["repo"] == "me/proj"
    code, got = call(f"{srv}/api/projects/{pid}/lands", {"lands": "elsewhere"})
    assert code == 400 and "elsewhere" in got["error"]
    code, got = call(f"{srv}/api/projects/eki/lands")
    assert code == 400 and "eki" in got["error"]
    assert call(f"{srv}/api/projects/nope/lands")[0] == 404
