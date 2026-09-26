import json

import pytest

from eki import db, faults, observe, paths, queue, selfwork, store, workspace
from conftest import run_inline

DAY = 86400


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "x.py").write_text("# x\n")
    (r / "bin" / "check").write_text("#!/bin/sh\nexit ${EKI_TEST_CHECK_EXIT:-0}\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def tb(path="eki/x.py", line=12, exc="KeyError"):
    return ("Traceback (most recent call last):\n"
            '  File "/opt/lib/python3/threading.py", line 5, in run\n'
            f'  File "/Users/someone/eki/{path}", line {line}, in go\n'
            "    thing['a']\n"
            f"{exc}: 'a'\n")


def fault(conn, t, text=None, run_id=None):
    jid = observe.fault(conn, "worker", text or tb(), run_id=run_id)
    conn.execute("UPDATE journal SET t=? WHERE id=?", (t, jid))
    return jid


def goals(conn):
    return conn.execute("SELECT * FROM goals WHERE source='fault' ORDER BY created_at").fetchall()


def items_of(conn, gid):
    return conn.execute("SELECT * FROM items WHERE goal_id=?", (gid,)).fetchall()


def request(conn, prompt="please do the thing"):
    tid = store.create_thread(conn, "t", None)
    return store.create_run(conn, tid, prompt)


def test_key():
    e = {"data": {"ours": True, "frame": "eki/x.py:12", "exc": "KeyError"}}
    assert faults.key(e) == "eki/x.py:12 KeyError"
    assert faults.key({"data": {**e["data"], "ours": False}}) is None
    assert faults.test_file("eki/a/b.py") == "tests/test_b.py"


def test_one_fault_opens_nothing(conn, src):
    now = db.now()
    fault(conn, now - 60)
    assert faults.tick(conn, now) == [] and goals(conn) == []


def test_twice_in_a_day_opens_one_goal_and_a_third_does_not_open_another(conn, src):
    now = db.now()
    rid = request(conn, "summarise the logs")
    fault(conn, now - 3600)
    fault(conn, now - 60, run_id=rid)
    said = faults.tick(conn, now)
    gs = goals(conn)
    assert len(gs) == 1 and "fix fault eki/x.py:12 KeyError" in said[0]
    g = gs[0]
    assert g["owner"] == "eki" and g["state"] == "planned"
    assert g["text"].splitlines()[0] == "fix fault eki/x.py:12 KeyError"
    (it,) = items_of(conn, g["id"])
    assert json.loads(it["files"]) == ["eki/x.py", "tests/test_x.py"]
    assert "KeyError: 'a'" in it["spec"] and 'File "/Users/someone/eki/eki/x.py", line 12' in it["spec"]
    assert "summarise the logs" in it["spec"] and "2 times" in it["spec"]

    fault(conn, now - 30)
    assert faults.tick(conn, now) == [] and len(goals(conn)) == 1


def test_old_faults_and_faults_not_ours_open_nothing(conn, src):
    now = db.now()
    fault(conn, now - 2 * DAY)
    fault(conn, now - 60)                                  # the other one is more than a day old
    conflict = "git rebase failed: CONFLICT in a.py\n"
    fault(conn, now - 50, conflict)
    fault(conn, now - 40, conflict)
    assert faults.tick(conn, now) == [] and goals(conn) == []


def test_the_daily_cap_holds(conn, src):
    now = db.now()
    for n in range(4):
        fault(conn, now - 100, tb(line=10 + n))
        fault(conn, now - 50, tb(line=10 + n))
    assert len(faults.tick(conn, now)) == 3 and len(goals(conn)) == 3
    assert faults.tick(conn, now + 60) == [] and len(goals(conn)) == 3
    rp = paths.config("routing")
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "self": {"fault_items_per_day": 4}}))
    faults.tick(conn, now)
    assert len(goals(conn)) == 4


def test_dropped_opens_again_landed_lately_does_not(conn, src):
    now = db.now()
    fault(conn, now - 100)
    fault(conn, now - 50)
    faults.tick(conn, now)
    (it,) = items_of(conn, goals(conn)[0]["id"])
    selfwork.drop(conn, it["id"])
    faults.tick(conn, now)
    gs = goals(conn)
    assert len(gs) == 2
    (again,) = items_of(conn, gs[1]["id"])
    selfwork._set(conn, again["id"], state="landed", landed_at=now - 2 * DAY)
    assert faults.tick(conn, now) == [] and len(goals(conn)) == 2
    selfwork._set(conn, again["id"], landed_at=now - 8 * DAY)     # long ago: it's back, so again
    faults.tick(conn, now)
    assert len(goals(conn)) == 3


def test_the_item_builds_in_the_background_and_is_only_proposed(conn, src, tmp_path, monkeypatch):
    queue.set_autonomy("propose")
    now = db.now()
    fault(conn, now - 100)
    fault(conn, now - 50)
    faults.tick(conn, now)
    (it,) = items_of(conn, goals(conn)[0]["id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "building" and store.run(conn, it["run_id"])["priority"] == "background"

    says = tmp_path / "says.txt"
    says.write_text("SUMMARY: fixed it.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(says))
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "judging" and store.run(conn, it["run_id"])["priority"] == "background"
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    queue.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"
