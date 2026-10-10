"""The project queue (eki/projectqueue.py): a local project's items land on its branch."""
from pathlib import Path

import pytest

from eki import db, projectland, projectqueue, projects, projectwork, queue, resolve, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline


@pytest.fixture
def folder(tmp_path):
    """The person's folder: branch master checked out, a log and an ignored build folder."""
    r = tmp_path / "chapter1"
    r.mkdir()
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    (r / "b.txt").write_text("bee\n")
    (r / "log.txt").write_text("started\n")
    (r / ".gitignore").write_text("build/\n")
    git(r, "init", "-q", "-b", "master")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def project(conn, path, **kw):
    pid = projects.add(conn, path, **kw)
    return projects.get(conn, pid)


def item(conn, p, title, files, gid=None):
    """A proposed item of a goal on project `p`, built in its own worktree: `files` {name: text}."""
    gid = gid or f"g-{p['id']}"
    with db.tx(conn):
        conn.execute("INSERT OR IGNORE INTO goals(id, text, source, owner, state, created_at, project) "
                     "VALUES (?,?,?,?,?,?,?)", (gid, "work on it", "ask", "you", "planned", db.now(), p["id"]))
        iid = selfwork.new_item(conn, gid, title, "do it", list(files), [], False)
    wt, base, branch = projectwork.worktree(p, iid)
    for name, text in files.items():
        (wt / name).write_text(text)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", title)
    with db.tx(conn):
        tid = store.create_thread(conn, title, str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=branch, base=base, thread_id=tid,
                      commit_sha=workspace.head(wt), summary=title, review="ok", state="proposed")
    return iid


def get(conn, iid):
    return selfwork.store_item(conn, iid)


def tip(p):
    return git(p["path"], "rev-parse", "refs/heads/master")


def test_an_item_lands_and_the_folder_fast_forwards(conn, folder):
    p = project(conn, folder)
    (folder / "build").mkdir()
    (folder / "build" / "out.bin").write_text("ignored\n")
    (folder / "scratch.txt").write_text("untracked\n")
    iid = item(conn, p, "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    said = projectqueue.tick(conn)
    it = get(conn, iid)
    assert it["state"] == "applied", (it["error"], said)
    assert it["landed_on"] == "master" and it["landed_at"] and it["why"] is None
    assert tip(p) == it["rebased"]
    assert (folder / "a.txt").read_text() == "one\nTWO\nthree\n"
    assert git(folder, "status", "--porcelain", "--untracked-files=no") == ""
    assert (folder / "build" / "out.bin").read_text() == "ignored\n"
    assert (folder / "scratch.txt").read_text() == "untracked\n"
    assert f"item {iid}: landed on master ({it['rebased'][:12]})" in said
    assert it["verdict"] == projectwork.NOT_JUDGED
    assert projectqueue.tick(conn) == []                       # done once


def test_a_dirty_file_the_change_touches_makes_it_wait(conn, folder):
    p = project(conn, folder)
    iid = item(conn, p, "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    (folder / "a.txt").write_text("one\nmine, not saved\nthree\n")
    before = tip(p)
    said = projectqueue.tick(conn)
    it = get(conn, iid)
    assert it["state"] == "queued" and it["why"] == "waits: chapter1 has local changes to a.txt", said
    assert tip(p) == before and (folder / "a.txt").read_text() == "one\nmine, not saved\nthree\n"
    assert projectqueue.tick(conn) == []                       # says it once
    git(folder, "checkout", "--", "a.txt")
    projectqueue.tick(conn)
    it = get(conn, iid)
    assert it["state"] == "applied" and it["why"] is None
    assert (folder / "a.txt").read_text() == "one\nTWO\nthree\n"


def test_a_dirty_file_it_does_not_touch_does_not_block(conn, folder):
    p = project(conn, folder)
    iid = item(conn, p, "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    (folder / "log.txt").write_text("started\nmore\n")
    projectqueue.tick(conn)
    assert get(conn, iid)["state"] == "applied"
    assert (folder / "log.txt").read_text() == "started\nmore\n"
    assert (folder / "a.txt").read_text() == "one\nTWO\nthree\n"


def test_conflicting_items_are_resolved_and_both_land(conn, folder, tmp_path, monkeypatch):
    p = project(conn, folder)
    a = item(conn, p, "Line two says A", {"a.txt": "one\nA\nthree\n"})
    b = item(conn, p, "Line two says B", {"a.txt": "one\nB\nthree\n"})
    said = projectqueue.tick(conn)
    assert get(conn, a)["state"] == "applied", said
    B = get(conn, b)
    assert B["state"] == "resolving", said
    wt = B["worktree"]
    (tmp_path / "says.txt").write_text("SUMMARY: kept both.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(tmp_path / "says.txt"))
    (Path(wt) / "a.txt").write_text("one\nA and B\nthree\n")
    run_inline(conn, B["run_id"])
    resolve.tick(conn)
    assert get(conn, b)["state"] == "queued", get(conn, b)["error"]
    projectqueue.tick(conn)
    assert get(conn, b)["state"] == "applied", get(conn, b)["error"]
    assert (folder / "a.txt").read_text() == "one\nA and B\nthree\n"
    log = git(folder, "log", "--format=%s", "master").splitlines()
    assert log[:2] == ["Line two says B", "Line two says A"]


def test_two_projects_never_share_a_batch(conn, folder, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.txt").write_text("x\n")
    git(other, "init", "-q", "-b", "master")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "first")
    p = project(conn, folder, check="true")
    q = project(conn, other, check="true")
    a = item(conn, p, "In chapter1", {"b.txt": "bee two\n"})
    b = item(conn, q, "In other", {"x.txt": "x two\n"})
    said = projectqueue.tick(conn)
    A, B = get(conn, a), get(conn, b)
    assert A["gate2_run"] and B["gate2_run"] and A["gate2_run"] != B["gate2_run"], said
    assert store.thread(conn, store.run(conn, A["gate2_run"])["thread_id"])["title"] == "gate 2: In chapter1"
    assert sum("gate 2 on" in s for s in said) == 2


def test_the_check_is_gate_two_and_red_is_unfit(conn, folder):
    p = project(conn, folder, check="test ! -f bad.txt")
    good = item(conn, p, "Good", {"b.txt": "bee two\n"})
    projectqueue.tick(conn)
    run = run_inline(conn, get(conn, good)["gate2_run"])
    assert run["provider"] == "command" and run["prompt"] == projects.check_argv(p)
    projectqueue.tick(conn)
    assert get(conn, good)["state"] == "applied"
    bad = item(conn, p, "Bad", {"bad.txt": "nope\n"})
    before = tip(p)
    projectqueue.tick(conn)
    run_inline(conn, get(conn, bad)["gate2_run"])
    projectqueue.tick(conn)
    it = get(conn, bad)
    assert it["state"] == "unfit" and it["error"] == "gate 2 failed" and it["gate2"] == "red"
    assert tip(p) == before and not (folder / "bad.txt").exists()


def test_propose_leaves_it_for_the_person(conn, folder):
    p = project(conn, folder)
    projects.set_autonomy(conn, p["id"], "propose")
    iid = item(conn, p, "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    before = tip(p)
    assert projectqueue.tick(conn) == []
    assert get(conn, iid)["state"] == "proposed" and tip(p) == before


def test_a_github_row_is_never_touched(conn, folder):
    p = project(conn, folder)
    conn.execute("UPDATE projects SET repo='me/chapter1' WHERE id=?", (p["id"],))
    iid = item(conn, projects.get(conn, p["id"]), "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    before = tip(p)
    assert projectqueue.tick(conn) == []
    assert get(conn, iid)["state"] == "proposed" and tip(p) == before


def test_a_restart_mid_landing_loses_nothing(conn, folder, monkeypatch):
    p = project(conn, folder)
    iid = item(conn, p, "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    monkeypatch.setattr(projectland, "land", lambda *a: "is busy")   # admitted, rebased, not landed
    projectqueue.tick(conn)
    monkeypatch.undo()
    it = get(conn, iid)
    assert it["state"] == "queued" and it["rebased"]
    assert projectland.land(folder, "master", it["rebased"]) is None   # the ref moved, then a kill
    moved = tip(p)
    calls = []
    monkeypatch.setattr(projectland, "land", lambda *a: calls.append(a))
    said = projectqueue.tick(conn)
    it = get(conn, iid)
    assert it["state"] == "applied" and it["landed_on"] == "master", said
    assert calls == [] and tip(p) == moved


def test_eki_s_own_queue_never_lists_a_project_item(conn, folder):
    p = project(conn, folder)
    iid = item(conn, p, "Better line two", {"a.txt": "one\nTWO\nthree\n"})
    selfwork._set(conn, iid, state="queued")
    assert [r["id"] for r in queue.order(conn)] == []
    assert [r["id"] for r in projectqueue.items(conn, p["id"], "queued")] == [iid]
