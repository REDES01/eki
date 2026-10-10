"""Projects you work inside: folders, the listing, adopting chats, making and finding projects."""
import os

import pytest

from eki import builds, db, paths, projectchats, projects, projectsetup, standing, store, workspace


@pytest.fixture
def src(tmp_path, monkeypatch):
    """eki's own source, so a project is never mistaken for it."""
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def norm(p):
    return projectchats.norm(p)


def github_row(conn, repo="me/proj", folder=None, state="on"):
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, folder, branch, created_at, repo, state, setup) "
                     "VALUES (?,?,?,?,'',?,?,?,'ready')",
                     (pid, repo, str(projects.clone_path(repo)), folder, db.now(), repo, state))
    return pid


def thread(conn, cwd):
    with db.tx(conn):
        return store.create_thread(conn, "hi", str(cwd) if cwd is not None else None)


def project_of(conn, tid):
    return conn.execute("SELECT project FROM threads WHERE id=?", (tid,)).fetchone()["project"]


def git_folder(where, origin=None):
    where.mkdir(parents=True)
    workspace.git(where, "init", "-q", "-b", "trunk")
    if origin:
        workspace.git(where, "config", "remote.origin.url", origin)
    return where


# ---- folder_of --------------------------------------------------------------------------------

def test_folder_of_each_kind(conn, src, proj, tmp_path):
    assert projectchats.folder_of(conn, projectchats.SELF) == norm(src)
    local = projects.add(conn, proj)
    assert projectchats.folder_of(conn, local) == norm(proj)
    bare = github_row(conn)
    assert projectchats.folder_of(conn, bare) is None
    mine = tmp_path / "mine"
    mine.mkdir()
    with_folder = github_row(conn, "me/other", folder=str(mine) + "/")
    assert projectchats.folder_of(conn, with_folder) == norm(mine)
    with pytest.raises(KeyError):
        projectchats.folder_of(conn, "nope")


# ---- listing ----------------------------------------------------------------------------------

def test_listing_puts_eki_first_and_hides_dropped_merged_and_shadowed(conn, src, proj, tmp_path):
    live = projectchats.create(conn, "live", tmp_path / "live")
    for state in ("dropped", "merged"):
        pid = projectchats.create(conn, state, tmp_path / state)
        conn.execute("UPDATE projects SET state=? WHERE id=?", (state, pid))
    retired = projects.add(conn, proj)
    tid = thread(conn, proj)
    assert projectchats.adopt(conn) == 1 and project_of(conn, tid) == retired
    conn.execute("UPDATE projects SET repo='me/proj', state='retired' WHERE id=?", (retired,))
    got = [e["id"] for e in projectchats.listing(conn)]
    assert got == [projectchats.SELF, live, retired]

    gh = github_row(conn, folder=str(proj))                # the live row now has the same folder
    got = projectchats.listing(conn)
    assert [e["id"] for e in got] == [projectchats.SELF, live, gh]
    assert project_of(conn, tid) == gh                     # its chats belong to the live row
    assert got[2]["chats"] == 1 and got[2]["kind"] == "github"


def test_listing_entries_and_background_lines(conn, src, proj, tmp_path):
    (src / "CLAUDE.md").write_text("@AGENTS.md\n")
    plain = projectchats.create(conn, "plain", tmp_path / "plain")
    (tmp_path / "plain" / "AGENTS.md").write_text("# how\n")
    worked = projects.add(conn, proj)
    standing.add(conn, str(proj), "keep it tidy")
    gh = github_row(conn)
    by = {e["id"]: e for e in projectchats.listing(conn)}
    eki = by[projectchats.SELF]
    assert (eki["name"], eki["kind"], eki["background"], eki["instructions"]) == \
        ("eki", "eki", "eki's own queue", "CLAUDE.md")
    assert by[plain]["kind"] == "folder" and by[plain]["background"] is None
    assert by[plain]["instructions"] == "AGENTS.md" and by[plain]["folder_ok"]
    assert by[plain]["repo"] is None
    assert by[worked]["background"] == f"worktrees of {norm(proj)}; eki/<id> branches you merge"
    assert by[worked]["goals"] == 1 and by[worked]["chats"] == 0
    assert by[gh]["background"] == f"eki's clone at {projects.clone_path('me/proj')}; changes come as PRs"
    assert by[gh]["folder"] is None and not by[gh]["folder_ok"] and by[gh]["repo"] == "me/proj"
    assert set(eki) == {"id", "name", "folder", "folder_ok", "repo", "kind", "chats", "goals", "prs",
                        "instructions", "background", "description", "last_active"}


def test_a_folder_on_github_shows_its_repo(conn, src, tmp_path):
    f = git_folder(tmp_path / "tb", "https://github.com/me/tokenbar.git")
    pid = projectchats.create(conn, "tb", f)
    e = next(e for e in projectchats.listing(conn) if e["id"] == pid)
    assert e["repo"] == "me/tokenbar" and e["kind"] == "folder"


def test_scan_finds_a_github_rows_folder_in_your_home(conn, src):
    gh = github_row(conn, "me/tokenbar")
    git_folder(paths.user_home() / "tokenbar", "git@github.com:me/tokenbar.git")
    assert projectchats.listing(conn)[1]["folder"] is None      # the poll never scans
    got = projectchats.listing(conn, scan=True)[1]
    assert got["id"] == gh and got["folder"] == norm(paths.user_home() / "tokenbar")
    assert projects.get(conn, gh)["folder"] == got["folder"]


# ---- home_clone -------------------------------------------------------------------------------

def test_home_clone_takes_the_first_folder_whose_origin_is_the_repo(src):
    home = paths.user_home()
    git_folder(home / "a-other", "https://github.com/me/other.git")
    (home / "b-plain").mkdir()
    git_folder(home / "c-proj", "https://github.com/me/proj.git")
    git_folder(home / "d-proj", "https://github.com/me/proj.git")
    assert projectchats.home_clone("me/proj") == norm(home / "c-proj")
    assert projectchats.home_clone("me/none") is None


# ---- adopt ------------------------------------------------------------------------------------

def test_adopt_joins_by_realpath_and_leaves_the_rest(conn, src, tmp_path, home):
    pid = projectchats.create(conn, "app", tmp_path / "app")
    other = projectchats.create(conn, "other", tmp_path / "other")
    link = tmp_path / "link"
    os.symlink(tmp_path / "app", link)
    slash = thread(conn, str(tmp_path / "app") + "/")
    via_link = thread(conn, link)
    kept = thread(conn, tmp_path / "app")
    conn.execute("UPDATE threads SET project=? WHERE id=?", (other, kept))
    engine = thread(conn, paths.work() / "x")
    nowhere = thread(conn, None)
    eki = thread(conn, src)
    assert projectchats.adopt(conn) == 3
    assert [project_of(conn, t) for t in (slash, via_link, kept, engine, nowhere, eki)] == \
        [pid, pid, other, None, None, projectchats.SELF]
    assert projectchats.adopt(conn) == 0


def test_engine_threads_never_join_even_on_a_matching_folder(conn, src, tmp_path):
    pid = github_row(conn)
    conn.execute("UPDATE projects SET folder=? WHERE id=?", (str(projects.clone_path("me/proj")), pid))
    t = thread(conn, projects.clone_path("me/proj"))
    projectchats.adopt(conn)
    assert project_of(conn, t) is None


# ---- create / set_folder / find ---------------------------------------------------------------

def test_create_makes_your_folder_with_git(conn, src):
    pid = projectchats.create(conn, "tokenbar")
    where = paths.user_home() / "tokenbar"
    assert (where / ".git").is_dir()
    row = projects.get(conn, pid)
    assert row["path"] == row["folder"] == norm(where) and row["branch"] == "" and row["repo"] is None
    assert projectchats.create(conn, "again", where) == pid


def test_create_takes_an_existing_folder_as_it_is(conn, src, tmp_path, proj):
    plain = tmp_path / "plain"
    plain.mkdir()
    pid = projectchats.create(conn, "plain", plain)
    assert not (plain / ".git").exists() and projects.get(conn, pid)["branch"] == ""
    assert projects.get(conn, projectchats.create(conn, "proj", proj))["branch"] == "trunk"


def test_create_refuses_eki_and_its_home(conn, src):
    with pytest.raises(ValueError):
        projectchats.create(conn, "eki")
    with pytest.raises(ValueError):
        projectchats.create(conn, "inside", paths.home() / "mine")
    with pytest.raises(ValueError, match="that's eki itself"):
        projectchats.create(conn, "src", src)


def test_set_folder_refuses_eki_and_its_home(conn, src, tmp_path):
    pid = github_row(conn)
    with pytest.raises(ValueError, match="that's eki itself"):
        projectchats.set_folder(conn, pid, src)
    with pytest.raises(ValueError):
        projectchats.set_folder(conn, pid, paths.home())
    with pytest.raises(ValueError):
        projectchats.set_folder(conn, pid, tmp_path / "missing")
    mine = tmp_path / "mine"
    mine.mkdir()
    t = thread(conn, mine)
    assert projectchats.set_folder(conn, pid, mine) == norm(mine)
    assert project_of(conn, t) == pid


def test_find_by_id_name_repo_and_eki(conn, src, tmp_path):
    a = projectchats.create(conn, "app", tmp_path / "a")
    gh = github_row(conn, "me/tokenbar")
    assert projectchats.find(conn, "eki") == projectchats.SELF
    assert projectchats.find(conn, a) == a
    assert projectchats.find(conn, "app") == a
    assert projectchats.find(conn, "me/tokenbar") == gh
    with pytest.raises(KeyError):
        projectchats.find(conn, "nope")
    b = projectchats.create(conn, "app", tmp_path / "b")
    with pytest.raises(ValueError) as e:
        projectchats.find(conn, "app")
    assert a in str(e.value) and b in str(e.value)


# ---- projects.add and _retire ---------------------------------------------------------------

def test_goal_add_on_a_chat_project_fills_its_branch(conn, src, proj):
    pid = projectchats.create(conn, "proj", proj)
    with db.tx(conn):
        conn.execute("UPDATE projects SET branch='' WHERE id=?", (pid,))   # as if made before its first commit
    assert projects.add(conn, proj) == pid
    assert projects.get(conn, pid)["branch"] == "trunk"


def test_housekeeping_never_retires_a_chat_only_project_on_github(conn, src, proj, gh_repo):
    pid = projectchats.create(conn, "proj", proj)
    projectsetup.tick(conn)
    row = projects.get(conn, pid)
    assert row["state"] in (None, "on") and row["repo"] is None
    assert projects.by_repo(conn, "me/proj") is None


# ---- eki's own repo ---------------------------------------------------------------------------

@pytest.fixture
def own(monkeypatch):
    from eki import integration
    monkeypatch.setattr(integration, "own_repo", lambda: "me/eki")
    return "me/eki"


@pytest.mark.parametrize("state", ["on", "retired", None])
def test_eki_is_listed_once_whatever_its_own_rows_state(conn, src, own, state):
    other = github_row(conn, "me/proj")
    if state is not None:
        github_row(conn, own, state=state)
    listed = projectchats.listing(conn)
    assert [e["id"] for e in listed if e["repo"] == own or e["name"] == own] == []
    assert listed[0]["id"] == projectchats.SELF
    assert [e["id"] for e in listed[1:]] == [other]
    assert projectchats.find(conn, own) == projectchats.SELF
    assert projectchats.find(conn, "me/proj") == other


def test_eki_entry_carries_its_own_repo_once(conn, src, own):
    workspace.git(src, "config", "remote.origin.url", "https://github.com/me/eki.git")
    github_row(conn, own, folder=str(src))
    listed = projectchats.listing(conn)
    assert [e["id"] for e in listed if e["repo"] == own] == [projectchats.SELF]
    assert projectchats.find(conn, own) == projectchats.SELF


def test_own_rows_folder_never_adopts_chats(conn, src, own, tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    github_row(conn, own, folder=str(mine))
    tid = thread(conn, mine)
    projectchats.adopt(conn)
    assert project_of(conn, tid) is None
