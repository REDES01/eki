"""Items held for a broken setup (eki/itemhold.py): waiting, no attempt spent, one ping per fix."""
import json

from eki import db, itemhold, selfview, selfwork, stopkind, store
from eki.cli import self as selfcli
from conftest import run_inline
from test_notify_items import notices, set_topic
from test_self import says, src  # noqa: F401 — fixtures

SIGNIN = "Claude Code isn't signed in — run `claude` and /login"


def stop(kind, fix="", retry_at=None, n=1):
    return {"kind": kind, "retry_at": retry_at, "fix": fix, "since": db.now(), "n": n}


def ends(conn, rid, rec, state="failed"):
    """The run ended `state` with this `runs.stop` (run handling writes it in real runs)."""
    with conn:
        conn.execute("UPDATE runs SET state=?, error=?, stop=?, ended_at=? WHERE id=?",
                     (state, rec.get("fix") or "boom", db.dumps(rec), db.now(), rid))


def building(conn):
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.tick(conn)
    return selfwork.items_in(conn, ("building",))[0]


def recheck_due(conn, iid):
    """An hour on: the hold's recheck time has come."""
    it = selfwork.store_item(conn, iid)
    hold = {**json.loads(it["hold"]), "retry_at": db.now() - 1}
    with conn:
        conn.execute("UPDATE items SET hold=? WHERE id=?", (db.dumps(hold), iid))


def env_pings(conn):
    return [n for n in notices(conn) if n["kind"] == "needs_you"]


def test_a_broken_setup_holds_the_item_and_pings_once_per_fix(conn, src, home):
    set_topic(home)
    it = building(conn)
    assert it["tries"] == 1
    ends(conn, it["run_id"], stop("environment", SIGNIN))
    said = selfwork.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "waiting" and cur["tries"] == 1
    hold = json.loads(cur["hold"])
    assert hold["kind"] == "environment" and hold["run"] == it["run_id"]
    assert hold["retry_at"] > db.now() + stopkind.ENV_RECHECK - 60
    assert any("held" in s and "signed in" in s for s in said)
    assert cur["error"].startswith(SIGNIN)
    assert it["id"] not in [r["id"] for r in selfwork.items_in(conn, ("waiting",))]
    selfwork.tick(conn)                                       # not started before its recheck
    assert selfwork.store_item(conn, it["id"])["state"] == "waiting"
    assert len(env_pings(conn)) == 1

    for _ in range(3):                                        # three rechecks fail the same way
        recheck_due(conn, it["id"])
        selfwork.tick(conn)
        cur = selfwork.store_item(conn, it["id"])
        assert cur["state"] == "building" and cur["tries"] == 1 and cur["hold"] is None
        ends(conn, cur["run_id"], stop("environment", SIGNIN))
        selfwork.tick(conn)
        assert selfwork.store_item(conn, it["id"])["state"] == "waiting"
    assert len(env_pings(conn)) == 1
    assert env_pings(conn)[0]["key"].startswith(f"item:{it['id']}:env:")

    recheck_due(conn, it["id"])                               # a different breakage: a second ping
    selfwork.tick(conn)
    ends(conn, selfwork.store_item(conn, it["id"])["run_id"], stop("environment", "folder /x/y is missing"))
    selfwork.tick(conn)
    assert len(env_pings(conn)) == 2 and selfwork.store_item(conn, it["id"])["tries"] == 1


def test_retry_clears_the_hold_and_starts_it_now(conn, src, home):
    it = building(conn)
    ends(conn, it["run_id"], stop("environment", SIGNIN))
    selfwork.tick(conn)
    selfwork.retry(conn, it["id"])
    cur = selfwork.store_item(conn, it["id"])
    assert cur["hold"] is None and cur["state"] == "waiting"
    assert it["id"] in [r["id"] for r in selfwork.items_in(conn, ("waiting",))]


def test_a_judgment_failure_takes_the_usual_path(conn, src, home, tmp_path, monkeypatch):
    set_topic(home)
    it = building(conn)
    says(tmp_path, monkeypatch, "SUMMARY: tried.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")

    def gate1_fails():
        cur = selfwork.store_item(conn, it["id"])
        run_inline(conn, cur["run_id"])
        selfwork.tick(conn)
        cur = selfwork.store_item(conn, it["id"])
        assert cur["state"] == "judging", cur["error"]
        run_inline(conn, cur["run_id"])
        ends(conn, cur["run_id"], stop("judgment"))
        selfwork.tick(conn)
        return selfwork.store_item(conn, it["id"])

    cur = gate1_fails()
    assert cur["state"] == "building" and cur["tries"] == 2 and cur["hold"] is None   # one more try
    cur = gate1_fails()
    assert cur["state"] == "fixing" and cur["hold"] is None                         # then the fixer
    assert env_pings(conn) == []


def test_a_build_ending_failed_for_judgment_is_left(conn, src, home):
    it = building(conn)
    ends(conn, it["run_id"], stop("judgment"))
    selfwork.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and cur["hold"] is None


def test_needs_you_pings_only_for_a_persons_kind(conn, src, home):
    set_topic(home)
    it = building(conn)
    with db.tx(conn):
        for kind in ("quota", "transient", "lost"):
            assert not selfwork.needs_you(conn, it, "left", "x", "y", kind)
        assert notices(conn) == []
        assert selfwork.needs_you(conn, it, "left", "x", "y", "judgment")
    assert len(notices(conn)) == 1


def test_show_and_the_board_say_why_it_stopped(conn, src, home, capsys):
    it = building(conn)
    ends(conn, it["run_id"], stop("environment", SIGNIN))
    selfwork.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    selfcli.show(conn, it["id"])
    out = capsys.readouterr().out
    assert "stopped:" in out and "environment — fix: Claude Code isn't signed in" in out and "rechecks" in out
    note = selfview.note(conn, cur)
    assert "stopped: environment — fix:" in note and "rechecks" in note

    recheck_due(conn, it["id"])                              # building again; its run hits a limit
    selfwork.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    rec = {**stop("quota", "session limit resets 4:30am (Asia/Tokyo)", retry_at=db.now() + 1800),
           "provider": "claude"}
    with conn:
        conn.execute("UPDATE runs SET state='queued', stop=? WHERE id=?", (db.dumps(rec), cur["run_id"]))
    note = selfview.note(conn, cur)
    assert "stopped: quota — waits for claude, retries" in note and "session limit" in note
    selfcli.show(conn, it["id"])
    assert "stopped:  quota" in capsys.readouterr().out
    assert itemhold.line(conn, cur) == stopkind.describe(rec, db.now())
    assert store.run(conn, cur["run_id"])["state"] == "queued"
