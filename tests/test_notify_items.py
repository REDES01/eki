"""Needs-you pushes for items: locked, and left for a person — once each, and for nothing else."""
import json

import pytest

from eki import db, queue, resolve, selfwork, workspace
from conftest import run_inline
from test_queue import autonomy, get, item as proposed, origin, src  # noqa: F401 — fixtures
from test_resolve import conflicted, repo, says  # noqa: F401 — fixtures

TOPIC = "xz-secret-topic-9f3k"


def set_topic(home, topic=TOPIC):
    path = home / "routing.json"
    data = json.loads(path.read_text())
    data["notify"] = {"topic": topic}
    path.write_text(json.dumps(data))


@pytest.fixture
def lit(home):
    set_topic(home)
    return home


def notices(conn):
    return [dict(r) for r in conn.execute("SELECT key, kind, title, body FROM notices ORDER BY created_at")]


@pytest.fixture
def built(tmp_path, monkeypatch):
    """A source checkout for selfwork.submit (as tests/test_self.py has it)."""
    r = tmp_path / "built"
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


def building(conn):
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.tick(conn)
    return selfwork.items_in(conn, ("building",))[0]


# ---- locked --------------------------------------------------------------------------------

def test_locked_queues_once(conn, src, lit):
    autonomy("apply")
    a = proposed(conn, "Touch the check", "bin/check", "#!/bin/sh\nexit 0\n")
    queue.tick(conn)
    queue.tick(conn)
    assert get(conn, a)["state"] == "locked"
    got = notices(conn)
    assert len(got) == 1
    n = got[0]
    assert n["key"] == f"item:{a}:locked:0" and n["kind"] == "needs_you" and n["title"] == "eki · needs you"
    assert n["body"] == f"Touch the check — locked, touches bin/check: eki self apply {a} --yes"


def test_queued_is_no_notice(conn, src, lit):
    autonomy("apply")
    a = proposed(conn, "A", "a.txt")
    queue.tick(conn)
    assert get(conn, a)["state"] == "queued"
    assert notices(conn) == []


def test_red_gate_two_unfit_is_no_notice(conn, src, lit, monkeypatch):
    autonomy("apply")
    a = proposed(conn, "A", "a.txt")
    queue.tick(conn)
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, get(conn, a)["gate2_run"])
    queue.tick(conn)
    assert get(conn, a)["state"] == "unfit"
    assert notices(conn) == []


def test_nothing_when_the_topic_is_empty(conn, src, home):
    set_topic(home, "")
    autonomy("apply")
    a = proposed(conn, "Touch the check", "bin/check", "#!/bin/sh\nexit 0\n")
    queue.tick(conn)
    assert get(conn, a)["state"] == "locked"
    assert notices(conn) == []


# ---- left by the build ----------------------------------------------------------------------

def test_build_person_queues_once(conn, built, lit, tmp_path, monkeypatch):
    it = building(conn)
    says(tmp_path, monkeypatch, "ITEM: person needs a real trackpad\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "left"
    got = notices(conn)
    assert [n["key"] for n in got] == [f"item:{it['id']}:left:1"]
    assert got[0]["body"] == f"x — left for you: needs a real trackpad: eki self show {it['id']}"


def test_build_body_is_one_line_and_short(conn, built, lit, tmp_path, monkeypatch):
    it = building(conn)
    says(tmp_path, monkeypatch, "ITEM: person " + "why " * 100 + "\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    body = notices(conn)[0]["body"]
    assert len(body) <= 200 and "\n" not in body


def test_a_failed_build_run_is_no_notice(conn, built, lit):
    it = building(conn)
    run_inline(conn, it["run_id"])
    with db.tx(conn):
        conn.execute("UPDATE runs SET state='failed', error='boom' WHERE id=?", (it["run_id"],))
    selfwork.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "failed" in cur["error"]
    assert notices(conn) == []


# ---- left by the resolver -------------------------------------------------------------------

def test_resolver_person_queues_once(conn, conflicted, lit, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: person the two intents contradict\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    resolve.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "left"
    got = notices(conn)
    assert [n["key"] for n in got] == [f"item:{it['id']}:left:0"]
    assert got[0]["body"] == f"Change line two — left for you: the two intents contradict: eki self show {it['id']}"


def test_markers_left_are_no_notice(conn, conflicted, lit, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "SUMMARY: sure.\nITEM: done\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "left"
    assert notices(conn) == []


def test_a_restart_does_not_queue_again(conn, conflicted, lit, tmp_path, monkeypatch):
    """The same transition seen twice (a restart mid-tick) keeps its one row."""
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: person the two intents contradict\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    with db.tx(conn):
        assert not selfwork.needs_you(conn, selfwork.store_item(conn, it["id"]), "left", "again", "x")
    assert len(notices(conn)) == 1
