"""A turn that ends with card calls still running waits for them and is woken with their answers."""
import json

import pytest

from eki import callwait, components, db, resolve, store, selfwork
from eki.cli import use
from conftest import run_inline
from test_resolve import conflicted, repo  # noqa: F401 — fixtures
from test_self import says, src  # noqa: F401 — fixtures

WAITING = "waiting on calls"


def item(conn, iid):
    return selfwork.store_item(conn, iid)


@pytest.fixture
def built(conn, src, tmp_path, monkeypatch):  # noqa: F811
    """A self item whose build run is under way, its answer `ITEM: done` with a change."""
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.tick(conn)
    it = selfwork.items_in(conn, ("building",))[0]
    says(tmp_path, monkeypatch, "SUMMARY: made x.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    return it


def call(conn, it, card="playtester", state="running"):
    rid = use.create(conn, card, [], it["worktree"], caller_run=it["run_id"])
    store.update_run(conn, rid, state=state)
    return rid


def finish(conn, rid, text, path=None, state="done", error=None):
    store.update_run(conn, rid, state=state, error=error, ended_at=db.now())
    store.add_event(conn, rid, 1, "text", {"text": text})
    if path:
        store.add_event(conn, rid, 1, "tool", {"name": "eki use playtester", "path": path})


def runs_of(conn, it):
    return store.thread_runs(conn, it["thread_id"])


def test_a_call_running_when_the_turn_ends_makes_the_item_wait(conn, built):
    c = call(conn, built)
    run_inline(conn, built["run_id"])
    said = selfwork.tick(conn)
    cur = item(conn, built["id"])
    assert cur["state"] == WAITING, said
    w = callwait.waits_of(cur)
    assert w["stage"] == "building" and w["calls"] == [c] and w["run"] == built["run_id"]
    assert cur["commit_sha"] is None and cur["tries"] == built["tries"]
    assert len(runs_of(conn, cur)) == 1                                 # no judge run
    assert f"item {cur['id']}: waiting on calls — playtester ({c[:8]})" in said
    assert selfwork.tick(conn) == [] and item(conn, cur["id"])["state"] == WAITING


def test_the_call_finishing_wakes_the_same_session(conn, built, tmp_path):
    c = call(conn, built)
    ended = run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    finish(conn, c, "PLAYTEST: ok", path=str(tmp_path / "shot.png"))
    said = selfwork.tick(conn)
    cur = item(conn, built["id"])
    new = store.run(conn, cur["run_id"])
    assert cur["state"] == "building" and cur["waits"] is None and new["id"] != ended["id"], said
    assert new["thread_id"] == ended["thread_id"] and new["provider"] == ended["provider"] and new["pinned"]
    assert new["parent"] == ended["id"] and not new["fresh"] and new["row"] == ended["row"]
    p = new["prompt"]
    assert p.startswith(callwait.FIRST_LINE)
    for want in ("playtester", c, "done", "PLAYTEST: ok", str(tmp_path / "shot.png")):
        assert want in p
    assert p.rstrip().endswith("SUMMARY: and ITEM: lines.")
    run_inline(conn, new["id"])
    selfwork.tick(conn)
    cur = item(conn, built["id"])
    assert cur["state"] == "judging" and cur["commit_sha"] and cur["tries"] == 1


def test_no_calls_concludes_as_before(conn, built):
    gone = call(conn, built, state="done")                              # already back
    hold = use.create(conn, "playtester", [], built["worktree"], caller_run=built["run_id"])
    conn.execute("UPDATE runs SET prompt=? WHERE id=?",
                 (json.dumps({"component": "playtester", "args": [], "caller_dir": built["worktree"],
                              "caller_run": built["run_id"], "hold": True}), hold))
    store.update_run(conn, hold, state="done")                          # an MCP hold ends at once
    assert callwait.outstanding(conn, built["run_id"]) == [] and gone
    run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    assert item(conn, built["id"])["state"] == "judging"


def test_a_run_that_changed_nothing_is_still_left(conn, built, monkeypatch):
    monkeypatch.delenv("EKI_FAKE_TOUCH")
    run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    cur = item(conn, built["id"])
    assert cur["state"] == "left" and "the agent changed nothing" in cur["error"]


def test_a_failed_turn_is_left_even_with_calls_out(conn, built):
    call(conn, built)
    store.update_run(conn, built["run_id"], state="failed", error="boom", ended_at=db.now())
    selfwork.tick(conn)
    assert item(conn, built["id"])["state"] == "left"


def test_the_cap_wakes_it_anyway_and_the_call_keeps_running(conn, built):
    c = call(conn, built)
    run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    w = callwait.waits_of(item(conn, built["id"]))
    conn.execute("UPDATE items SET waits=? WHERE id=?",
                 (json.dumps({**w, "since": db.now() - 61 * 60}), built["id"]))
    selfwork.tick(conn)
    cur = item(conn, built["id"])
    assert cur["state"] == "building"
    p = store.run(conn, cur["run_id"])["prompt"]
    assert f"playtester run {c}: still running — not waited for any more; `eki show {c}` later" in p
    assert store.run(conn, c)["state"] == "running" and not store.run(conn, c)["cancel"]
    run_inline(conn, cur["run_id"])
    selfwork.tick(conn)
    assert item(conn, built["id"])["state"] == "judging"               # the old turn's call: not waited for


def test_a_cards_wait_minutes_raises_the_cap(conn, built):
    d = components.pool_dir() / "slowcard"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": "slow", "run": "echo hi",
                                                  "wait_minutes": 90}))
    c = call(conn, built, card="slowcard")
    assert callwait.cap_seconds(conn, [store.run(conn, c)]) == 90 * 60
    run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    w = callwait.waits_of(item(conn, built["id"]))
    conn.execute("UPDATE items SET waits=? WHERE id=?",
                 (json.dumps({**w, "since": db.now() - 61 * 60}), built["id"]))
    selfwork.tick(conn)
    assert item(conn, built["id"])["state"] == WAITING
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": "slow", "run": "echo hi",
                                                  "wait_minutes": True}))
    assert callwait.cap_seconds(conn, [store.run(conn, c)]) == 60 * 60


def test_a_resolve_waits_and_wakes_back_to_resolving(conn, conflicted, tmp_path, monkeypatch):  # noqa: F811
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    it = item(conn, it["id"])
    says(tmp_path, monkeypatch, "SUMMARY: kept both.\nITEM: done\n")
    c = call(conn, it)
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = item(conn, it["id"])
    assert cur["state"] == WAITING and callwait.waits_of(cur)["stage"] == "resolving"
    finish(conn, c, "PLAYTEST: ok")
    selfwork.tick(conn)
    cur = item(conn, it["id"])
    assert cur["state"] == "resolving" and cur["run_id"] != rid and cur["waits"] is None
    assert store.run(conn, cur["run_id"])["parent"] == rid


def test_after_max_waits_the_item_is_concluded(conn, built):
    cur = built
    for n in range(callwait.MAX_WAITS):
        call(conn, cur)
        run_inline(conn, cur["run_id"])
        selfwork.tick(conn)
        assert item(conn, built["id"])["state"] == WAITING, n
        w = callwait.waits_of(item(conn, built["id"]))
        for c in w["calls"]:
            finish(conn, c, "ok")
        selfwork.tick(conn)
        cur = item(conn, built["id"])
        assert cur["state"] == "building"
    call(conn, cur)
    run_inline(conn, cur["run_id"])
    said = selfwork.tick(conn)
    assert item(conn, built["id"])["state"] == "judging"
    assert f"item {built['id']}: stopped waiting for calls after 4 wakes" in said


def test_drop_cancels_the_calls(conn, built):
    c = call(conn, built)
    run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    selfwork.drop(conn, built["id"])
    assert store.run(conn, c)["cancel"] == 1 and item(conn, built["id"])["state"] == "dropped"


def test_a_restart_carries_on(conn, built):
    c = call(conn, built)
    run_inline(conn, built["run_id"])
    selfwork.tick(conn)
    conn.close()
    fresh = db.connect()
    finish(fresh, c, "PLAYTEST: ok")
    selfwork.tick(fresh)
    cur = item(fresh, built["id"])
    assert cur["state"] == "building" and store.run(fresh, cur["run_id"])["prompt"].startswith(callwait.FIRST_LINE)


def test_a_long_answer_is_cut(conn, built):
    c = call(conn, built)
    finish(conn, c, "x" * 3000)
    p = callwait.wake_prompt(conn, [store.run(conn, c)], False)
    assert "x" * 2000 in p and "x" * 2001 not in p and f"… the whole answer: eki show {c}" in p


def test_a_failed_call_shows_its_error(conn, built):
    c = call(conn, built)
    finish(conn, c, "", state="failed", error="the game wouldn't start")
    p = callwait.wake_prompt(conn, [store.run(conn, c)], False)
    assert "failed" in p and "the game wouldn't start" in p
