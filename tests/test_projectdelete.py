"""A project leaves eki: its work stops, eki's worktrees and clone go, chats archive, your folder stays."""
import json
import os
from pathlib import Path

import pytest

from eki import (api_threads, asks, db, housekeep, integration, paths, projectchats, projectdelete, projects,
                 projectsetup, selfwork, standing, store, workspace)


@pytest.fixture(autouse=True)
def src(tmp_path, monkeypatch):
    """eki's own source, so a project is never mistaken for it."""
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    monkeypatch.setattr(integration, "own_repo", lambda: None)
    return r


def snapshot(where: Path) -> dict:
    """Everything of yours in the folder: its files, status, HEAD, branch and refs outside eki/."""
    files = {}
    for root, dirs, names in os.walk(where):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            p = Path(root) / n
            files[str(p.relative_to(where))] = p.read_bytes()
    refs = [ln for ln in workspace.git(where, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
            if not ln.startswith("refs/heads/eki/")]
    return {"files": files, "status": workspace.git(where, "status", "--porcelain"),
            "head": workspace.git(where, "rev-parse", "HEAD"),
            "branch": workspace.git(where, "symbolic-ref", "--short", "HEAD"), "refs": refs}


def chat(conn, pid, cwd, with_ask=True):
    with db.tx(conn):
        tid = store.create_thread(conn, "hi", str(cwd))
        conn.execute("UPDATE threads SET project=? WHERE id=?", (pid, tid))
        rid = store.create_run(conn, tid, "hello")
        if with_ask:
            asks.create(conn, rid, tid, "question", {"questions": ["which?"]})
    return tid, rid


def with_work(conn, pid, where):
    """A goal planning, a planned goal with a building item in a worktree, a standing goal, a chat."""
    planning = selfwork.submit(conn, "plan me", plan=True, owner="eki", project=pid)
    gid = selfwork.submit(conn, "build me", plan=False, owner="eki", project=pid)
    iid = conn.execute("SELECT id FROM items WHERE goal_id=?", (gid,)).fetchone()[0]
    tid = store.create_thread(conn, "build", str(where), by="eki")
    rid = store.create_run(conn, tid, "build it")
    wt = workspace.add(where, iid)
    with db.tx(conn):
        conn.execute("UPDATE items SET state='building', run_id=?, worktree=? WHERE id=?", (rid, str(wt), iid))
    sid = standing.add(conn, str(where), "keep it tidy", project=pid)
    return {"planning": planning, "goal": gid, "item": iid, "item_run": rid, "worktree": wt, "standing": sid}


def state(conn, table, rid):
    return conn.execute(f"SELECT state FROM {table} WHERE id=?", (rid,)).fetchone()[0]


def test_delete_stops_work_archives_chats_and_never_touches_the_folder(conn, proj):
    pid = projects.add(conn, proj)
    w = with_work(conn, pid, proj)
    tid, crid = chat(conn, pid, proj)
    before = snapshot(proj)
    plan_run = conn.execute("SELECT plan_run FROM goals WHERE id=?", (w["planning"],)).fetchone()[0]

    done = projectdelete.delete(conn, "proj")

    assert done["id"] == pid and done["folder"] == projectchats.norm(proj)
    assert done["runs"] == 3 and done["goals"] == 2 and done["items"] == 1 and done["standing"] == 1
    assert done["asks"] == 1 and done["chats"] == 1 and done["removed"] == [str(w["worktree"])]
    for rid in (plan_run, w["item_run"], crid):
        assert state(conn, "runs", rid) == "cancelled"
    assert state(conn, "goals", w["planning"]) == state(conn, "goals", w["goal"]) == "left"
    assert conn.execute("SELECT error FROM goals WHERE id=?", (w["goal"],)).fetchone()[0] == "project deleted"
    assert state(conn, "items", w["item"]) == "dropped"
    assert state(conn, "standing", w["standing"]) == "dropped"
    assert conn.execute("SELECT state FROM asks").fetchone()[0] == "withdrawn"
    assert not w["worktree"].exists()

    assert snapshot(proj) == before                       # your folder: byte for byte
    assert workspace.git(proj, "branch", "--list", f"eki/{w['item']}")   # eki's branch kept

    row = projects.get(conn, pid)
    assert row["state"] == "deleted" and row["path"] == f"{projectchats.norm(proj)}#deleted-{pid}"
    assert row["folder"] == projectchats.norm(proj)
    assert pid not in [e["id"] for e in projectchats.listing(conn)]
    for key in (pid, "proj"):
        with pytest.raises(KeyError):
            projectchats.find(conn, key)
    assert tid in [t["id"] for t in api_threads.threads(conn, archived=True)]
    assert tid not in [t["id"] for t in api_threads.threads(conn)]
    assert conn.execute("SELECT project FROM threads WHERE id=?", (tid,)).fetchone()[0] == pid


def test_a_github_row_loses_eki_s_clone_only(conn, tmp_path):
    clone = projects.clone_path("me/proj")
    clone.mkdir(parents=True)
    workspace.git(clone, "init", "-q", "-b", "trunk")
    (clone / "a.txt").write_text("a\n")
    workspace.git(clone, "add", "-A")
    workspace.git(clone, "commit", "-q", "-m", "first")
    Path(f"{clone}.part").mkdir()
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "keep.txt").write_text("mine\n")
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, folder, branch, created_at, repo, state, setup) "
                     "VALUES (?,'me/proj',?,?,'trunk',?,'me/proj','on','ready')",
                     (pid, str(clone), str(mine), db.now()))
    gid = selfwork.submit(conn, "build me", plan=False, owner="eki", project=pid, issue=4)
    iid = conn.execute("SELECT id FROM items WHERE goal_id=?", (gid,)).fetchone()[0]
    wt = workspace.add(clone, iid)

    done = projectdelete.delete(conn, "me/proj")
    assert set(done["removed"]) == {str(wt), str(clone)}
    assert not clone.exists() and not Path(f"{clone}.part").exists() and paths.projects().exists()
    assert (mine / "keep.txt").read_text() == "mine\n"
    row = projects.get(conn, pid)
    assert row["state"] == "deleted" and json.loads(row["ignored"]) == [4]
    assert projects.by_repo(conn, "me/proj") is None

    again = projectsetup.ensure(conn, "me/proj")          # the repo again: a fresh project
    assert again["id"] != pid and again["path"] == str(clone)


def test_eki_is_refused_with_the_reason(conn):
    for f in (projectdelete.delete, projectdelete.preview):
        with pytest.raises(ValueError, match="eki can't be deleted: it's eki itself — its own source"):
            f(conn, "eki")
    with pytest.raises(KeyError):
        projectdelete.delete(conn, "nothing")


def test_preview_says_what_happens_in_one_line(conn, proj):
    pid = projects.add(conn, proj)
    chat(conn, pid, proj, with_ask=False)
    chat(conn, pid, proj, with_ask=False)
    got = projectdelete.preview(conn, pid)
    where = projectchats.norm(proj)
    assert got == {"id": pid, "name": "proj", "folder": where, "chats": 2,
                   "line": f"Stops its work, archives its 2 chats, removes eki's worktrees. "
                           f"Your folder {where} is not touched."}
    bare = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, state) "
                     "VALUES (?,'me/x',?,'',?,'me/x','on')", (bare, str(projects.clone_path("me/x")), db.now()))
    assert projectdelete.preview(conn, bare)["line"].endswith("Your folder no folder is not touched.")
    assert state(conn, "projects", pid) == "on"


@pytest.mark.parametrize("again", ["create", "add"])
def test_adding_the_folder_again_is_a_fresh_project_and_the_chats_rejoin(conn, proj, again):
    pid = projectchats.create(conn, "proj", folder=proj)
    tid, _ = chat(conn, pid, proj, with_ask=False)
    projectdelete.delete(conn, pid)
    new = projectchats.create(conn, "proj", folder=proj) if again == "create" else projects.add(conn, proj)
    assert new != pid
    projectchats.adopt(conn)
    t = conn.execute("SELECT project, archived_at FROM threads WHERE id=?", (tid,)).fetchone()
    assert t["project"] == new and t["archived_at"] is not None
    assert [e["id"] for e in projectchats.listing(conn)][1:] == [new]


def test_the_repo_again_takes_back_its_chats_by_folder(conn, tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, folder, branch, created_at, repo, state, setup) "
                     "VALUES (?,'me/proj',?,?,'',?,'me/proj','on','ready')",
                     (pid, str(projects.clone_path("me/proj")), str(mine), db.now()))
    tid, _ = chat(conn, pid, mine, with_ask=False)
    projectdelete.delete(conn, pid)
    new = projectsetup.ensure(conn, "me/proj")["id"]
    assert new != pid
    projectchats.set_folder(conn, new, mine)               # found in your home, or picked on the page
    assert conn.execute("SELECT project FROM threads WHERE id=?", (tid,)).fetchone()[0] == new


def test_an_interrupted_delete_is_finished_by_housekeeping(conn, proj):
    pid = projects.add(conn, proj)
    w = with_work(conn, pid, proj)
    tid, crid = chat(conn, pid, proj)
    before = snapshot(proj)
    projectdelete.begin(conn, pid)                         # ... and the engine stops here
    assert state(conn, "projects", pid) == "deleting"
    assert pid not in [e["id"] for e in projectchats.listing(conn)]
    with pytest.raises(ValueError, match="being deleted"):
        projects.add(conn, proj)

    said = housekeep.tick(conn)
    assert any(ln.startswith("proj: deleted") for ln in said)
    assert state(conn, "projects", pid) == "deleted"
    assert state(conn, "runs", crid) == state(conn, "runs", w["item_run"]) == "cancelled"
    assert conn.execute("SELECT archived_at FROM threads WHERE id=?", (tid,)).fetchone()[0] is not None
    assert not w["worktree"].exists() and snapshot(proj) == before

    dump = lambda: [list(r) for t in ("projects", "goals", "items", "standing", "asks", "threads")
                    for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
    rows = dump()
    assert not any("deleted" in ln for ln in projectdelete.tick(conn))
    assert dump() == rows


def test_finish_runs_again_harmlessly(conn, proj):
    pid = projects.add(conn, proj)
    with_work(conn, pid, proj)
    chat(conn, pid, proj)
    projectdelete.delete(conn, pid)
    again = projectdelete.finish(conn, pid)
    assert (again["runs"], again["goals"], again["items"], again["standing"], again["asks"], again["chats"],
            again["removed"]) == (0, 0, 0, 0, 0, 0, [])
    assert projects.get(conn, pid)["path"].count("#deleted-") == 1
