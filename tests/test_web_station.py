"""The Station page's API: the self board, builds, journal and digests, and the self actions."""
import json
import os
import urllib.error

import pytest

from eki import asks, builds, db, digest, observe, paths, selfwork, store, traincheck
from test_self import src  # noqa: F401  (the source fixture)
from test_self_cli import goal_with
from test_web import get, post, web  # noqa: F401  (the fixture)


def _get(web, path):
    code, body = get(web + path)
    assert code == 200
    return json.loads(body)


def _code(web, path):
    try:
        return get(web + path)[0]
    except urllib.error.HTTPError as e:
        return e.code


def test_station_lists_a_goal_its_items_the_queue_and_their_asks(web, conn):
    first = goal_with(conn, state="queued", queued_at=db.now(), head="a" * 40, rebased="b" * 40, gate2="green")
    second = goal_with(conn, state="queued", queued_at=db.now() + 1)
    tid = store.create_thread(conn, "build", "/tmp")
    rid = store.create_run(conn, tid, "x", provider="fake")
    locked = goal_with(conn, state="locked", locked=json.dumps(["bin/check"]), run_id=rid)
    with db.tx(conn):
        asks.create(conn, rid, tid, "question", {"questions": [{"question": "ok?"}]})
        other = store.create_thread(conn, "chat", "/tmp")
        asks.create(conn, store.create_run(conn, other, "y", provider="fake"), other, "question",
                    {"questions": [{"question": "not a self one"}]})
    st = _get(web, "/api/station")
    s = st["self"]
    assert s["autonomy"] in ("apply", "propose") and "parallel" in s and s["source"]
    assert [q["id"] for q in s["queue"]] == [first, second] and [q["pos"] for q in s["queue"]] == [1, 2]
    assert s["queue"][0]["stage"] == "gate 2 green — waiting for the ones ahead"
    assert s["queue"][1]["stage"] == "rebasing"
    items = {it["id"]: it for g in s["goals"] for it in g["items"]}
    assert set(items) >= {first, second, locked}
    assert "bin/check" in items[locked]["note"] and items[locked]["thread"] == tid
    assert [a["questions"][0]["question"] for a in st["asks"]] == ["ok?"]


def test_builds_reflect_the_builds_root(web, conn):
    for bid, commit in (("aaaa", "a" * 40), ("bbbb", "b" * 40)):
        d = builds.root() / bid
        d.mkdir()
        (d / ".eki-build.json").write_text(json.dumps({"id": bid, "commit": commit, "made_at": db.now()}))
    (builds.root() / "bbbb" / ".healthy").write_text("")
    (builds.root() / "bbbb" / traincheck.CHECKED).write_text(json.dumps({"state": "green"}))
    os.symlink(str(builds.root() / "bbbb"), builds.root() / "current")
    os.symlink(str(builds.root() / "aaaa"), builds.root() / "previous")
    with db.tx(conn):
        conn.execute("INSERT INTO build_scores(build, healthy_at, verdict) VALUES ('bbbb', 1, 'worse')")
    got = _get(web, "/api/builds")
    assert got["running"] == builds.running_id() and got["swap"] is None
    b = {x["id"]: x for x in got["builds"]}
    assert b["bbbb"]["current"] and not b["bbbb"]["previous"] and b["aaaa"]["previous"]
    assert b["bbbb"]["healthy"] and not b["aaaa"]["healthy"]
    assert b["bbbb"]["check"] == "checked" and b["aaaa"]["check"] == "-"
    assert b["bbbb"]["verdict"] == "worse" and b["aaaa"]["verdict"] == "-"
    assert b["aaaa"]["commit"] == "a" * 40


def test_journal_by_span_and_kind(web, conn):
    observe.record(conn, "handoff", data={"reason": "too big"})
    observe.record(conn, "run", run_id="r1", data={"state": "done", "seconds": 3})
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        observe.fault(conn, "a test")
    got = _get(web, "/api/journal?since=7d&kind=fault")["entries"]
    assert [e["kind"] for e in got] == ["fault"] and "RuntimeError" in got[0]["summary"]
    assert "traceback" in got[0]["data"]
    got = _get(web, "/api/journal")["entries"]
    assert [e["kind"] for e in got] == ["fault", "handoff"]                 # newest first, no runs
    assert [e["kind"] for e in _get(web, "/api/journal?kind=run")["entries"]] == ["run"]
    assert _code(web, "/api/journal?since=bogus") == 400
    assert _code(web, "/api/journal?kind=wizard") == 400


def test_digests_newest_first_and_write_now(web, conn):
    assert _get(web, "/api/digests") == {"pages": [], "latest": None}
    (digest.folder() / "2026-09-24.md").write_text("# older\n")
    (digest.folder() / "2026-09-25.md").write_text("# newer\n")
    got = _get(web, "/api/digests")
    assert got["pages"] == [str(digest.folder() / "2026-09-25.md"), str(digest.folder() / "2026-09-24.md")]
    assert got["latest"] == "# newer\n"
    code, out = post(web + "/api/digests/write", {})
    assert code == 200 and out["path"] in _get(web, "/api/digests")["pages"]


def test_a_wish_needs_the_header_and_drafts_or_plans(web, conn, src):
    assert post(web + "/api/self", {"text": "make eki nicer"}, header=False)[0] == 403
    code, out = post(web + "/api/self", {"text": "make eki nicer"})
    assert code == 200 and out["state"] == "drafting"
    code, out = post(web + "/api/self", {"text": "exactly this", "as_is": True})
    assert code == 200 and out["state"] == "planning"
    assert post(web + "/api/self", {"text": "  "})[0] == 400


def test_apply_queues_and_a_locked_item_needs_yes(web, conn):
    iid = goal_with(conn, state="proposed")
    code, out = post(f"{web}/api/self/items/{iid}/apply", {})
    assert code == 200 and out["result"] == iid and selfwork.store_item(conn, iid)["state"] == "queued"
    lk = goal_with(conn, state="locked", locked=json.dumps(["eki/quota.py"]))
    code, out = post(f"{web}/api/self/items/{lk}/apply", {})
    assert code == 400 and "eki/quota.py" in out["error"]
    assert selfwork.store_item(conn, lk)["state"] == "locked"
    assert post(f"{web}/api/self/items/{lk}/apply", {"yes": True})[0] == 200
    assert selfwork.store_item(conn, lk)["state"] == "queued"


def test_drop_retry_and_an_unknown_item(web, conn):
    iid = goal_with(conn, state="left", error="went wrong")
    assert post(f"{web}/api/self/items/{iid}/retry", {})[0] == 200
    assert selfwork.store_item(conn, iid)["state"] == "waiting"
    assert post(f"{web}/api/self/items/{iid}/drop", {})[0] == 200
    assert selfwork.store_item(conn, iid)["state"] == "dropped"
    for action in ("apply", "drop", "retry"):
        assert post(f"{web}/api/self/items/nosuch/{action}", {})[0] == 404


def test_autonomy_release_and_undo(web, conn, monkeypatch):
    code, out = post(web + "/api/self/autonomy", {"mode": "apply"})
    assert code == 200 and out == {"autonomy": "apply"}
    data = json.loads(paths.config("routing").read_text())
    assert data["self"]["autonomy"] == "apply" and data["rows"]
    assert post(web + "/api/self/autonomy", {"mode": "sometimes"})[0] == 400
    from eki import train
    monkeypatch.setattr(train, "tick", lambda c, force=False: ["released"] if force else [])
    assert post(web + "/api/self/release", {}) == (200, {"lines": ["released"]})
    code, out = post(web + "/api/builds/abc123/undo", {})
    assert code == 400 and "carried no items" in out["error"]


@pytest.mark.parametrize("path", ["/api/self/items/x/explode", "/api/self/nothing"])
def test_unknown_self_paths_are_not_found(web, path):
    assert post(web + path, {})[0] == 404
