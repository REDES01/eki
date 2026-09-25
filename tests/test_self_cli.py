"""`eki self`: the board shows the queue, and apply / release / autonomy do what they say."""
import json

import pytest

from eki import db, paths, selfwork, store, train
from eki.cli import main
import eki.cli.self as self_cmd


@pytest.fixture(autouse=True)
def no_engine(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)


def goal_with(conn, **fields):
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, "a goal", "ask", "you", "planned", db.now()))
        iid = selfwork.new_item(conn, gid, "the item", "do it", ["eki/a.py"], [], False)
        selfwork._set(conn, iid, **fields)
    return iid


def test_the_board_lists_a_queued_item_with_its_stage_and_head(conn, capsys):
    iid = goal_with(conn, state="queued", queued_at=db.now(), head="a" * 40, rebased="b" * 40,
                    gate2="green", gate2_on="b" * 40)
    other = goal_with(conn, state="queued", queued_at=db.now() + 1)
    assert main(["self"]) == 0
    out = capsys.readouterr().out
    assert "\nqueue\n" in out
    first = next(l for l in out.splitlines() if l.strip().startswith("1."))
    assert iid in first and "aaaaaaaa" in first and "gate 2 green — waiting for the ones ahead" in first
    second = next(l for l in out.splitlines() if l.strip().startswith("2."))
    assert other in second and "rebasing" in second


def test_the_board_shows_a_running_gate_2_and_a_resolving_item(conn, capsys):
    tid = store.create_thread(conn, "gate 2", "/tmp")
    rid = store.create_run(conn, tid, '["true"]', provider="command")
    iid = goal_with(conn, state="queued", queued_at=db.now(), head="c" * 40, rebased="d" * 40,
                    gate2_run=rid, gate2_on="d" * 40)
    goal_with(conn, state="resolving", run_id="r1", head="c" * 40)
    main(["self"])
    out = capsys.readouterr().out
    assert f"gate 2 (run {rid})" in out and iid in out
    assert "resolving (run r1)" in out


def test_a_locked_item_shows_its_files_and_the_yes_hint(conn, capsys):
    iid = goal_with(conn, state="locked", locked=json.dumps(["bin/check"]))
    main(["self"])
    out = capsys.readouterr().out
    assert "bin/check" in out and f"eki self apply {iid} --yes" in out


def test_landed_live_and_rolled_back_items_say_so(conn, capsys):
    goal_with(conn, state="landed", rebased="e" * 40)
    goal_with(conn, state="live", build="b-123")
    goal_with(conn, state="rolled back", error="rolled back from build b-9: exit 1 after 3s")
    main(["self"])
    out = capsys.readouterr().out
    assert "eeeeeeeeeeee" in out and "live in build b-123" in out and "rolled back from build b-9" in out


def test_apply_queues_a_proposed_item(conn, capsys):
    iid = goal_with(conn, state="proposed", touched=json.dumps(["eki/a.py"]))
    main(["self"])
    assert f"`eki self apply {iid}`" in capsys.readouterr().out
    assert main(["self", "apply", iid]) == 0
    assert f"queued {iid}" in capsys.readouterr().out
    assert selfwork.store_item(conn, iid)["state"] == "queued"


def test_apply_on_a_locked_item_needs_yes(conn, capsys):
    iid = goal_with(conn, state="locked", locked=json.dumps(["eki/quota.py"]))
    assert main(["self", "apply", iid]) == 1
    assert "eki/quota.py" in capsys.readouterr().err
    assert selfwork.store_item(conn, iid)["state"] == "locked"
    assert main(["self", "apply", iid, "--yes"]) == 0
    assert selfwork.store_item(conn, iid)["state"] == "queued"


def test_autonomy_is_written_and_shown(conn, capsys):
    goal_with(conn, state="proposed")
    assert main(["self", "autonomy", "apply"]) == 0
    assert "autonomy apply" in capsys.readouterr().out
    data = json.loads(paths.config("routing").read_text())
    assert data["self"]["autonomy"] == "apply" and data["rows"]          # the rest is kept
    main(["self"])
    out = capsys.readouterr().out
    assert out.startswith("(autonomy apply") and "joins the queue" in out
    assert main(["self", "autonomy", "sometimes"]) == 1


def test_release_with_nothing_to_do(conn, capsys, monkeypatch):
    monkeypatch.setattr(train, "release", lambda c: [])
    assert main(["self", "release"]) == 0
    assert capsys.readouterr().out.strip() == "nothing to release"
    monkeypatch.setattr(train, "release", lambda c: ["train: build x is going live with 1 item(s)"])
    main(["self", "release"])
    assert "going live" in capsys.readouterr().out
