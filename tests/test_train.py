import json
import time
from pathlib import Path

import pytest

integration = pytest.importorskip("eki.integration")     # the train rides on the integration repo

from eki import builds, db, paths, selfwork, store, train, workspace  # noqa: E402

QUEUE_COLUMNS = [("rebased", "TEXT"), ("gate2", "TEXT"), ("landed_at", "REAL"), ("build", "TEXT")]


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "engine.py").write_text("# the engine\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    monkeypatch.setattr(train, "_last_check", [0.0])
    return r


@pytest.fixture
def conn(conn):
    have = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for col, decl in QUEUE_COLUMNS:                   # until the queue's columns are in db.py
        if col not in have:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")
    return conn


def autonomy(value, **more):
    p = paths.config("routing")
    got = json.loads(p.read_text())
    got["self"] = {"autonomy": value, **more}
    p.write_text(json.dumps(got))


def land(conn, name, gate2="green"):
    """What the queue leaves: a commit on integration main and a landed item carrying it."""
    r = integration.repo()
    (r / "eki" / f"{name}.py").write_text(f"# {name}\n")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", f"self: {name}")
    sha = workspace.head(r)
    iid = selfwork.new_item(conn, "g1", name, name, [], [], False)
    tid = store.create_thread(conn, name, str(r))
    rid = store.create_run(conn, tid, "check", provider="fake")
    conn.execute("UPDATE items SET state='landed', rebased=?, commit_sha=?, gate2=?, run_id=?, "
                 "landed_at=? WHERE id=?", (sha, sha, gate2, rid, db.now(), iid))
    return iid


def item(conn, iid):
    return conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()


def notes(conn, iid):
    return [json.loads(e["data"])["text"] for e in store.events_after(conn, item(conn, iid)["run_id"])
            if e["kind"] == "note"]


def test_under_propose_the_train_does_nothing_and_makes_no_repo(conn, src):
    autonomy("propose", release_minutes=0)
    assert train.tick(conn) == []
    assert not (paths.home() / "self" / "repo").exists()
    assert builds.current() is None


def test_release_builds_main_and_swaps_to_it_then_rests(conn, src):
    iid = land(conn, "a")
    said = train.release(conn)
    main = integration.main()
    assert builds.current() is not None and builds.current().name == main[:12]
    assert json.loads((builds.current() / ".eki-build.json").read_text())["commit"] == main
    assert builds.status()["swap"]["why"] == "train: 1 item(s)"
    assert item(conn, iid)["build"] == main[:12] and item(conn, iid)["state"] == "landed"
    assert any("going live with 1 item(s)" in s for s in said)
    assert train.release(conn) == []                                  # nothing new: nothing goes


def test_an_item_not_green_in_gate_2_holds_the_train(conn, src):
    land(conn, "a")
    bad = land(conn, "b", gate2="red")
    said = train.release(conn)
    assert builds.current() is None and any(bad in s for s in said)


def test_a_swap_under_way_holds_the_next_train(conn, src):
    land(conn, "a")
    train.release(conn)
    land(conn, "b")
    said = train.release(conn)                        # no engine took the first swap up yet
    assert any("still under way" in s for s in said)
    assert builds.current().name != integration.main()[:12]


def test_a_healthy_swap_makes_its_items_live(conn, src):
    iid = land(conn, "a")
    train.release(conn)
    rec = builds.root() / "swap.json"
    data = json.loads(rec.read_text())
    data.update(state="healthy")
    rec.write_text(json.dumps(data))
    said = train.settle(conn)
    assert item(conn, iid)["state"] == "live" and any("live in build" in s for s in said)
    assert notes(conn, iid) == [f"live in build {item(conn, iid)['build']}"]
    assert train.settle(conn) == [] and len(notes(conn, iid)) == 1  # said once


def test_a_rollback_from_the_build_rolls_its_items_back(conn, src):
    iid = land(conn, "a")
    train.release(conn)
    swap = json.loads((builds.root() / "swap.json").read_text())
    (builds.root() / "rollback.json").write_text(json.dumps(
        {"state": "rolled back", "from": swap["target"], "to": "", "exit": 1, "after": 3,
         "at": int(swap["at"]) + 1}))
    train.settle(conn)
    it = item(conn, iid)
    assert it["state"] == "rolled back" and "exit 1 after 3s" in it["error"]
    assert "rolled back from build" in notes(conn, iid)[0]


def test_an_older_rollback_is_not_this_builds(conn, src):
    iid = land(conn, "a")
    train.release(conn)
    swap = json.loads((builds.root() / "swap.json").read_text())
    (builds.root() / "rollback.json").write_text(json.dumps(
        {"from": swap["target"], "exit": 1, "after": 3, "at": int(swap["at"]) - 60}))
    assert train.settle(conn) == [] and item(conn, iid)["state"] == "landed"


def test_under_apply_the_tick_runs_the_train_when_it_is_time(conn, src):
    iid = land(conn, "a")
    autonomy("apply", release_minutes=60)
    train._last_check[0] = time.time()
    assert train.tick(conn) == [] and builds.current() is None       # not yet
    autonomy("apply", release_minutes=0)
    train.tick(conn)
    assert item(conn, iid)["build"] and builds.current() is not None
