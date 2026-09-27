"""`eki self retry <goal id>`: a failed or left goal runs its plan (or draft) again."""
import pytest

from eki import selfdraft, selfwork, store
from eki.cli import main
import eki.cli.self as self_cmd
from conftest import run_inline
from test_self import PLAN, src, says  # noqa: F401 — the source fixture and what the fake says


@pytest.fixture(autouse=True)
def no_engine(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)


def goal(conn, gid):
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def fail(conn, rid):
    store.update_run(conn, rid, state="failed", error="x")
    selfwork.tick(conn)


def test_a_failed_plan_is_planned_again(conn, src, tmp_path, monkeypatch):
    gid = selfwork.submit(conn, "add two modules")
    old = goal(conn, gid)["plan_run"]
    fail(conn, old)
    assert goal(conn, gid)["state"] == "failed"
    assert selfdraft.retry(conn, gid[:6]) == "planning"
    g = goal(conn, gid)
    assert g["state"] == "planning" and g["error"] is None and g["plan_run"] != old
    run = store.run(conn, g["plan_run"])
    assert run["state"] == "queued" and run["pinned"] and run["provider"] == "fake"
    assert store.cwd_of(conn, run["thread_id"]).endswith(f"plan-{gid}")
    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, run["id"])
    assert any("2 item(s) planned" in s for s in selfwork.tick(conn))
    assert goal(conn, gid)["state"] == "planned"


def test_a_failed_draft_is_drafted_again_from_the_wish(conn, src):
    gid = selfwork.submit(conn, "make eki nicer", draft=True)
    old = goal(conn, gid)["draft_run"]
    fail(conn, old)
    assert goal(conn, gid)["state"] == "failed"
    assert selfdraft.retry(conn, gid) == "drafting"
    g = goal(conn, gid)
    assert g["state"] == "drafting" and g["error"] is None and g["draft_run"] != old and g["plan_run"] is None
    run = store.run(conn, g["draft_run"])
    assert run["state"] == "queued" and "make eki nicer" in run["prompt"]
    assert store.cwd_of(conn, run["thread_id"]).endswith(f"draft-{gid}")


def test_a_left_draft_is_drafted_again(conn, src, tmp_path, monkeypatch):
    gid = selfwork.submit(conn, "hmm", draft=True)
    says(tmp_path, monkeypatch, "I couldn't decide.\n")
    run_inline(conn, goal(conn, gid)["draft_run"])
    selfwork.tick(conn)
    assert goal(conn, gid)["state"] == "left"
    assert selfdraft.retry(conn, gid) == "drafting"
    g = goal(conn, gid)
    assert g["state"] == "drafting" and g["error"] is None
    assert store.run(conn, g["draft_run"])["state"] == "queued"


def test_only_a_failed_or_left_goal_is_retried(conn, src, tmp_path, monkeypatch):
    gid = selfwork.submit(conn, "add two modules")
    with pytest.raises(ValueError, match="is planning"):
        selfdraft.retry(conn, gid)
    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, goal(conn, gid)["plan_run"])
    selfwork.tick(conn)
    with pytest.raises(ValueError, match="is planned"):
        selfdraft.retry(conn, gid)
    with pytest.raises(KeyError, match="no goal nosuch"):
        selfdraft.retry(conn, "nosuch")


def test_the_cli_retries_a_goal_or_an_item(conn, src, tmp_path, monkeypatch, capsys):
    gid = selfwork.submit(conn, "add two modules")
    fail(conn, goal(conn, gid)["plan_run"])
    assert main(["self", "retry", gid]) == 0
    g = goal(conn, gid)
    assert f"goal {gid}: planning again (run {g['plan_run']})" in capsys.readouterr().out
    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, g["plan_run"])
    selfwork.tick(conn)
    it = selfwork.items_in(conn, ("building",))[0]
    selfwork._set(conn, it["id"], state="unfit", error="checks failed")
    assert main(["self", "retry", it["id"]]) == 0
    assert f"queued again: {it['id']}" in capsys.readouterr().out
    assert selfwork.store_item(conn, it["id"])["state"] == "waiting"
