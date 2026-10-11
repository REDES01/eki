import json

import pytest

from eki import builds, db, integration, paths, selfwork, store, train, traincheck, workspace
from conftest import run_inline


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "__init__.py").write_text("")
    (r / "eki" / "engine.py").write_text("# the engine\n")
    (r / "eki" / "candidate.py").write_text("import sys\nprint('candidate ok')\nsys.exit(0)\n")
    (r / "bin" / "check").write_text("#!/bin/sh\necho \"check says $EKI_PYTHON\"\n"
                                     "echo 'boom in the drill'\nexit ${EKI_TEST_CHECK_EXIT:-0}\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    monkeypatch.setattr(train, "_last_check", [0.0])
    return r


def land(conn, name, touched=None):
    """What the queue leaves: a commit on integration main and a landed item carrying it."""
    r = integration.repo()
    path = touched or f"eki/{name}.py"
    (r / path).parent.mkdir(parents=True, exist_ok=True)
    (r / path).write_text(f"# {name}\n")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", f"self: {name}")
    sha = workspace.head(r)
    iid = selfwork.new_item(conn, "g1", name, name, [], [], False)
    tid = store.create_thread(conn, name, str(r))
    rid = store.create_run(conn, tid, "build", provider="fake")
    conn.execute("UPDATE items SET state='landed', rebased=?, commit_sha=?, gate2='green', run_id=?, "
                 "landed_at=?, touched=? WHERE id=?", (sha, sha, rid, db.now(), json.dumps([path]), iid))
    return iid


def item(conn, iid):
    return conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()


def check_run(conn, build):
    return store.run(conn, traincheck.status(build)["run"])


def test_green_check_then_swap(conn, src):
    iid = land(conn, "a")
    said = train.release(conn)
    build = builds.root() / integration.main()[:12]
    assert builds.current() is None and any("checking build" in s for s in said)
    run = check_run(conn, build)
    assert run["provider"] == "command" and run["priority"] == "now"
    tree = store.cwd_of(conn, run["thread_id"])                  # a worktree at the build's commit
    assert tree == str(workspace.path_for(f"train-{build.name}")) and workspace.head(tree) == integration.main()
    assert "--running" in run["prompt"]
    assert store.thread(conn, run["thread_id"])["title"] == f"train check: build {build.name}"
    assert traincheck.label(build) == "checking"
    assert train.release(conn)[0].startswith("train: checking build")   # still queued: waits
    run_inline(conn, run["id"])
    assert "candidate ok" in store.answer(conn, run["id"])
    said = train.release(conn)
    assert builds.current() == build and item(conn, iid)["build"] == build.name
    assert traincheck.status(build)["state"] == "green" and traincheck.label(build) == "checked"
    assert any("going live" in s for s in said)


def test_red_reverts_the_newest_item_and_the_next_train_checks_again(conn, src, monkeypatch):
    old, new = land(conn, "a"), land(conn, "b")
    bad_sha = item(conn, new)["rebased"]
    train.release(conn)
    build = builds.root() / integration.main()[:12]
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, check_run(conn, build)["id"])
    said = train.release(conn)
    assert builds.current() is None
    assert any(f"item {new} reverted" in s for s in said)
    rec = traincheck.status(build)
    assert rec["state"] == "red" and "boom in the drill" in rec["tail"] and traincheck.label(build) == "check red"
    it = item(conn, new)
    assert it["state"] == "unfit" and it["error"] == "the train's check failed"
    assert "boom in the drill" in it["verdict"]
    notes = [json.loads(e["data"])["text"] for e in store.events_after(conn, it["run_id"]) if e["kind"] == "note"]
    assert notes and "the train's check failed" in notes[0]
    assert item(conn, old)["state"] == "landed"
    log = workspace.git(integration.repo(), "log", "--format=%B", "-1", "main")
    assert f"This reverts commit {bad_sha}" in log
    assert not (integration.repo() / "eki" / "b.py").exists()
    assert not (paths.work() / f"revert-{new}").exists()

    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "0")
    said = train.release(conn)                                    # the next train: a new build
    second = builds.root() / integration.main()[:12]
    assert second != build and traincheck.label(second) == "checking"
    run_inline(conn, check_run(conn, second)["id"])
    train.release(conn)
    assert builds.current() == second and item(conn, old)["build"] == second.name


def test_a_red_build_with_commits_by_hand_reverts_nothing(conn, src, monkeypatch):
    land(conn, "a")
    new = land(conn, "b")
    train.release(conn)
    build = builds.root() / integration.main()[:12]
    main = integration.main()
    monkeypatch.setattr(traincheck, "_by_hand", lambda c, b: True)
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, check_run(conn, build)["id"])
    said = train.release(conn)
    assert any("made by hand" in s for s in said) and not any(f"item {new} reverted" in s for s in said), said
    assert item(conn, new)["state"] == "landed" and integration.main() == main


def test_a_red_handled_twice_reverts_once(conn, src, monkeypatch):
    land(conn, "a")
    new = land(conn, "b")
    train.release(conn)
    build = builds.root() / integration.main()[:12]
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, check_run(conn, build)["id"])
    carried = conn.execute("SELECT * FROM items WHERE state='landed' ORDER BY landed_at").fetchall()
    traincheck.step(conn, build, carried)
    head = integration.main()
    green, said = traincheck.step(conn, build, carried)          # a restart after it was handled
    assert green is False and integration.main() == head and item(conn, new)["state"] == "unfit"


def test_a_docs_only_train_swaps_without_a_check(conn, src):
    iid = land(conn, "readme", touched="docs/readme.md")
    said = train.release(conn)
    build = builds.current()
    assert build is not None and item(conn, iid)["build"] == build.name
    assert traincheck.status(build) is None and traincheck.label(build) == "-"
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE provider='command'").fetchone()[0] == 0
    assert any("going live" in s for s in said)


def test_a_check_whose_run_disappeared_is_started_again(conn, src):
    land(conn, "a")
    train.release(conn)
    build = builds.root() / integration.main()[:12]
    gone = traincheck.status(build)["run"]
    conn.execute("DELETE FROM runs WHERE id=?", (gone,))
    green, said = traincheck.step(conn, build, [])
    again = traincheck.status(build)["run"]
    assert green is None and again != gone and store.run(conn, again)["state"] == "queued"


def test_while_a_check_runs_a_newer_main_waits_for_it(conn, src):
    land(conn, "a")
    train.release(conn)
    first = builds.root() / integration.main()[:12]
    land(conn, "b")
    said = train.release(conn)
    assert any(first.name in s for s in said)
    assert not (builds.root() / integration.main()[:12]).exists()


def test_a_check_that_ended_while_main_moved_still_ships(conn, src):
    a = land(conn, "a")
    train.release(conn)
    first = builds.root() / integration.main()[:12]
    land(conn, "b")                                   # main moves while the check runs
    run_inline(conn, check_run(conn, first)["id"])
    said = train.release(conn)
    assert builds.current() == first and item(conn, a)["build"] == first.name
    assert any("going live" in s for s in said)


def test_needed_and_command(conn, src):
    a, d = land(conn, "a"), land(conn, "d", touched="notes.md")
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM items")}
    assert traincheck.needed([rows[a], rows[d]]) and not traincheck.needed([rows[d]])
    argv = json.loads(traincheck.command(builds.root() / "x", "full"))
    assert argv[:2] == ["/bin/sh", "-c"] and "bin/check -q && " in argv[2] and "-m eki.candidate" in argv[2]
    assert "EKI_PYTHON=" in argv[2] and "-m drill" not in argv[2]
    argv = json.loads(traincheck.command(builds.root() / "x", "drills"))
    assert "bin/check -q -m drill && " in argv[2] and "bin/check -q && " not in argv[2]
    assert "-m eki.candidate" in argv[2] and "EKI_PYTHON=" in argv[2]
    for scope in ("full", "drills"):
        script = json.loads(traincheck.command(builds.root() / "x", scope))[2]
        assert "EKI_CHECK_WIDE=1" in script and "export EKI_CHECK_WIDE" in script


# ---- scope: only the drills on a head gate 2 passed -------------------------------------------

def scope_of(conn, running):
    """Start a check on a fresh build of main the way the train does; the run's argv and scope."""
    build = builds.make(integration.repo(), "main")
    carried = conn.execute("SELECT * FROM items WHERE state='landed'").fetchall()
    green, said = traincheck.step(conn, build, carried, running)
    assert green is None and any("checking build" in s for s in said)
    return json.loads(check_run(conn, build)["prompt"])[2], traincheck.status(build)["scope"]


def is_drills(script):
    return "bin/check -q -m drill && " in script and "bin/check -q && " not in script


def test_a_head_gate_2_passed_runs_only_the_drills(conn, src):
    base = integration.main()
    land(conn, "a")
    script, scope = scope_of(conn, base)
    assert is_drills(script) and "-m eki.candidate" in script and scope == "drills"


def test_a_chain_of_two_passed_items_runs_only_the_drills(conn, src):
    base = integration.main()
    land(conn, "a"), land(conn, "b")
    assert traincheck.judged(conn, base, integration.main())
    script, scope = scope_of(conn, base)
    assert is_drills(script) and scope == "drills"


def test_a_commit_by_hand_runs_the_full_check(conn, src):
    base = integration.main()
    land(conn, "a")
    r = integration.repo()
    (r / "eki" / "hand.py").write_text("# by hand\n")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "a fix by hand")
    script, scope = scope_of(conn, base)
    assert "bin/check -q && " in script and "-m drill" not in script and scope == "full"


def test_a_revert_on_top_runs_the_full_check(conn, src):
    base = integration.main()
    land(conn, "a")
    iid = land(conn, "b")
    workspace.git(integration.repo(), "revert", "--no-edit", item(conn, iid)["rebased"])
    assert not traincheck.judged(conn, base, integration.main())
    assert scope_of(conn, base)[1] == "full"


def test_no_running_build_or_a_gate_2_not_green_runs_the_full_check(conn, src):
    base = integration.main()
    iid = land(conn, "a")
    assert not traincheck.judged(conn, None, integration.main())
    assert not traincheck.judged(conn, "0" * 40, integration.main())     # git can't answer
    assert not traincheck.judged(conn, integration.main(), integration.main())   # nothing between
    conn.execute("UPDATE items SET gate2=NULL WHERE id=?", (iid,))
    assert not traincheck.judged(conn, base, integration.main())
    assert scope_of(conn, base)[1] == "full"


def test_a_restart_mid_check_keeps_the_run_and_its_scope(conn, src):
    base = integration.main()
    land(conn, "a")
    build = builds.make(integration.repo(), "main")
    carried = conn.execute("SELECT * FROM items WHERE state='landed'").fetchall()
    traincheck.step(conn, build, carried, base)
    first = traincheck.status(build)
    assert first["scope"] == "drills"
    traincheck.step(conn, build, carried, None)                  # would be "full" if decided again
    assert traincheck.status(build)["run"] == first["run"] and traincheck.status(build)["scope"] == "drills"
    conn.execute("DELETE FROM runs WHERE id=?", (first["run"],))  # the run is gone: started again
    traincheck.step(conn, build, carried, None)
    again = traincheck.status(build)
    assert again["run"] != first["run"] and again["scope"] == "drills"
    assert is_drills(json.loads(store.run(conn, again["run"])["prompt"])[2])
    run_inline(conn, again["run"])
    assert traincheck.step(conn, build, carried, None)[0] is True
    assert traincheck.status(build)["state"] == "green" and traincheck.status(build)["scope"] == "drills"
