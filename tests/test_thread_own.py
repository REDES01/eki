"""Thread names: who made a thread, how it was titled, and the backfill of eki's own."""
import json

import test_queue
import test_traincheck
from eki import (asking, builds, chores, db, housekeep, integration, projects, projectsetup, queue,
                 selfdraft, selfwork, store, threadkeep, train, traincheck)
from conftest import run_inline
from eki.workspace import git
from test_chores import use_local
from test_projectsetup import make_bare
from test_selfdraft import ENTRIES


def by_of(conn, tid):
    return conn.execute("SELECT by FROM threads WHERE id=?", (tid,)).fetchone()["by"]


def test_create_thread_writes_by_title_by_and_seen_at(conn):
    mine = store.create_thread(conn, "hello", None)
    own = store.create_thread(conn, "gate 2: x", None, by="eki")
    row = conn.execute("SELECT * FROM threads WHERE id=?", (mine,)).fetchone()
    assert row["by"] is None and row["title_by"] == "prompt"
    assert row["seen_at"] == row["created_at"] and row["unread"] == 0
    assert by_of(conn, own) == "eki"


def test_an_old_row_reads_title_by_old(conn):
    conn.execute("INSERT INTO threads(id, title, created_at) VALUES ('old1', 't', 1)")
    row = conn.execute("SELECT * FROM threads WHERE id='old1'").fetchone()
    assert row["title_by"] == "old" and row["by"] is None and row["seen_at"] is None
    assert row["pinned_at"] is None and row["archived_at"] is None and row["deleted_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM chat_groups").fetchone()[0] == 0


def old_thread(conn, title="t"):
    """A thread as made before `by` existed."""
    tid = store.new_id()
    conn.execute("INSERT INTO threads(id, title, created_at) VALUES (?,?,?)", (tid, title, db.now()))
    return tid


def test_backfill_marks_eki_threads_and_leaves_yours(conn):
    chore = old_thread(conn)
    conn.execute("INSERT INTO chores(id, kind, subject, thread_id, created_at) VALUES ('c1','review','i',?,1)",
                 (chore,))
    command = old_thread(conn)
    store.create_run(conn, command, "bin/check", provider="command")
    gate2 = old_thread(conn)
    rid = store.create_run(conn, gate2, "check")
    conn.execute("INSERT INTO items(id, goal_id, title, gate2_run, created_at, updated_at) "
                 "VALUES ('i1','g1','x',?,1,1)", (rid,))
    titled = old_thread(conn, "gate 2: x")
    plain = old_thread(conn, "just a chat")
    yours, _ = asking.submit(conn, "hello there")
    assert threadkeep.backfill(conn) == 4
    for tid in (chore, command, gate2, titled):
        assert by_of(conn, tid) == "eki"
    assert by_of(conn, plain) is None and by_of(conn, yours) is None
    assert threadkeep.backfill(conn) == 0


def test_tick_is_a_housekeeping_step(conn):
    assert "threads" in [name for name, _ in housekeep.STEPS]
    old_thread(conn, "train check: build abc")
    assert threadkeep.tick(conn) == ["threads: marked 1 as eki's own"]
    assert threadkeep.tick(conn) == []


# ---- every place eki makes a thread for itself marks it -------------------------------------------

def thread_of(conn, rid):
    return store.run(conn, rid)["thread_id"]


def repo(tmp_path, monkeypatch, name="src", files=None):
    """A source repo for EKI_SOURCE, with a bin/check that says what the tests need."""
    r = tmp_path / name
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    for f, text in {"eki/__init__.py": "", "eki/x.py": "# x\n", **(files or {})}.items():
        (r / f).write_text(text)
    (r / "bin" / "check").write_text("#!/bin/sh\necho 'check'\nexit ${EKI_TEST_CHECK_EXIT:-0}\n")
    (r / "bin" / "check").chmod(0o755)
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def test_a_chore_thread_is_ekis(home, conn, text_fake):
    use_local(home)
    with db.tx(conn):
        rid = chores.start(conn, "review", "it1", "steps=1 look", "background")
    assert by_of(conn, thread_of(conn, rid)) == "eki"


def test_selfdraft_threads_are_ekis(conn, tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch)
    draft = selfwork.submit(conn, "make eki nicer", draft=True)
    plan = selfwork.submit(conn, "exactly this", owner="eki")
    rank = selfdraft.open_rank(conn, ENTRIES, "roadmap: nothing else to do")
    goal = lambda gid: conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    for rid in (goal(draft)["draft_run"], goal(plan)["plan_run"], goal(rank)["draft_run"]):
        assert by_of(conn, thread_of(conn, rid)) == "eki"


def test_a_gate_two_thread_is_ekis(conn, tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    git(seed, "checkout", "-q", "-b", "main")
    test_queue.commit(seed, "a.py")
    git(seed, "push", "-q", "origin", "main")
    git(tmp_path, "clone", "-q", str(origin), str(tmp_path / "src"))
    monkeypatch.setenv("EKI_SOURCE", str(tmp_path / "src"))
    monkeypatch.setattr(queue, "GATE2", test_queue.STUB)
    queue.set_autonomy("apply")
    iid = test_queue.item(conn, "A", "a.txt")
    queue.tick(conn)
    rid = selfwork.store_item(conn, iid)["gate2_run"]
    assert by_of(conn, thread_of(conn, rid)) == "eki"


def test_a_train_check_thread_is_ekis(conn, tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch, files={"eki/engine.py": "# the engine\n",
                                       "eki/candidate.py": "import sys\nsys.exit(0)\n"})
    monkeypatch.setattr(train, "_last_check", [0.0])
    test_traincheck.land(conn, "a")
    train.release(conn)
    build = builds.root() / integration.main()[:12]
    assert by_of(conn, thread_of(conn, traincheck.status(build)["run"])) == "eki"


def test_clone_and_install_threads_are_ekis(conn, tmp_path, fake_gh, monkeypatch):
    repo(tmp_path, monkeypatch)
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], "me")
    fake_gh.repo("me/proj", make_bare(tmp_path, {"package.json": json.dumps({"scripts": {"test": "true"}})}))
    p = projectsetup.ensure(conn, "me/proj")
    assert by_of(conn, thread_of(conn, p["setup_run"])) == "eki"
    run_inline(conn, p["setup_run"])
    projectsetup.tick(conn)
    p = projects.get(conn, p["id"])
    assert p["setup"] == "installing"
    assert by_of(conn, thread_of(conn, p["setup_run"])) == "eki"


def test_a_chat_of_yours_keeps_by_null(conn):
    tid, _ = asking.submit(conn, "write me a haiku")
    assert by_of(conn, tid) is None
