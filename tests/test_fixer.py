import datetime
import json
import time
from pathlib import Path

import pytest

from eki import db, fixer, fixerbrief, integration, paths, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline


def commit(where, name, text, message):
    p = Path(where) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", message)
    return workspace.head(where)


@pytest.fixture
def src(tmp_path, monkeypatch):
    o = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(o))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(o), str(seed))
    git(seed, "checkout", "-q", "-b", "main")
    commit(seed, "s.txt", "1\n2\n3\n4\n5\n", "first")
    commit(seed, "bin/check", "#!/bin/sh\n", "check")
    git(seed, "push", "-q", "origin", "main")
    s = tmp_path / "src"
    git(tmp_path, "clone", "-q", str(o), str(s))
    monkeypatch.setenv("EKI_SOURCE", str(s))
    return s


def goal(conn, gid, project=None):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project) "
                     "VALUES (?,?,?,?,?,?,?)", (gid, f"goal {gid}", "ask", "you", "planned", db.now(), project))


def item(conn, gid, title, name, text, state="judging"):
    """An item built on integration main, one commit writing `name`."""
    with db.tx(conn):
        iid = selfwork.new_item(conn, gid, title, f"spec of {title}", [name], [], False)
    base = integration.main()
    wt = workspace.add(integration.repo(), iid, base=base, branch=f"self/{iid}")
    sha = commit(wt, name, text, f"self: {title}")
    with db.tx(conn):
        tid = store.create_thread(conn, f"self: {title}", str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=f"self/{iid}", base=base, thread_id=tid,
                      commit_sha=sha, touched=db.dumps([name]), summary=f"{title} built", state=state)
    return iid


def land(conn, gid, title, name, text):
    """An item whose commit is now on integration main."""
    iid = item(conn, gid, title, name, text, state="landed")
    integration.fast_forward(selfwork.store_item(conn, iid)["commit_sha"])
    return iid


def offer(conn, iid, stage="gate1", failure="FAILED tests/test_s.py::test_order - expected the old order"):
    with db.tx(conn):
        return fixer.offer(conn, selfwork.store_item(conn, iid), stage, failure)


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


@pytest.fixture
def stale(conn, src):
    """Item A, judging red, built before B landed on the same file."""
    goal(conn, "g1")
    a = item(conn, "g1", "Change line one", "s.txt", "one\n2\n3\n4\n5\n")
    land(conn, "g1", "Change line five", "s.txt", "1\n2\n3\n4\nfive\n")
    return a


def test_offer_starts_one_fixer_on_main(conn, stale):
    main = integration.main()
    line = offer(conn, stale)
    it = selfwork.store_item(conn, stale)
    assert it["state"] == "fixing" and line == f"item {stale}: gate1 failed — the fixer is on it ({it['run_id'][:8]})"
    run = store.run(conn, it["run_id"])
    assert run["row"] == "code" and run["fresh"] == 1 and run["priority"] == "now"
    assert run["prompt"].startswith(fixerbrief.FIRST_LINE)
    assert "expected the old order" in run["prompt"] and fixerbrief.RULE in run["prompt"]
    assert "Change line five" in run["prompt"] and "spec of Change line five" in run["prompt"]
    assert "eki slot -- bin/check tests/test_x.py" in run["prompt"]
    wt = it["worktree"]
    assert git(wt, "rev-parse", "HEAD^") == main and it["base"] == main
    assert it["commit_sha"] == git(wt, "rev-parse", "HEAD")
    assert (Path(wt) / "s.txt").read_text() == "one\n2\n3\n4\nfive\n"
    note = fixer.of(it)
    assert note["stage"] == "gate1" and note["run"] == it["run_id"] and note["head"] == main
    assert note["summary"] is None and "old order" in note["failure"]
    assert fixer.tried(it) == "the fixer is working (gate1)"
    assert offer(conn, stale) is None                                 # one fixer run per item
    assert len([r for r in store.thread_runs(conn, it["thread_id"])
                if r["prompt"].startswith(fixerbrief.FIRST_LINE)]) == 1
    assert fixer.tick(conn) == []                                     # the run hasn't ended


def test_no_fixer_for_a_project_item_or_when_turned_off(conn, src):
    goal(conn, "gp", project="p1")
    p = item(conn, "gp", "Theirs", "t.txt", "t\n")
    assert offer(conn, p) is None and selfwork.store_item(conn, p)["state"] == "judging"
    goal(conn, "g1")
    a = item(conn, "g1", "Mine", "m.txt", "m\n")
    routing = paths.home() / "routing.json"
    routing.write_text(json.dumps({**json.loads(routing.read_text()), "self": {"fixer": False}}))
    assert offer(conn, a) is None and selfwork.store_item(conn, a)["fixer"] is None


def test_ended_early():
    assert fixer.ended_early("I looked around.")
    assert fixer.ended_early("SUMMARY: started the tests, waiting for them.\nITEM: done")
    assert fixer.ended_early("SUMMARY: half.\nITEM: partial Waiting on the build")
    assert not fixer.ended_early("SUMMARY: did it.\nITEM: done")
    assert not fixer.ended_early("SUMMARY: waiting.\nITEM: person needs a token I can't read")


def test_a_done_fixer_goes_through_gate_one(conn, stale, tmp_path, monkeypatch):
    offer(conn, stale)
    it = selfwork.store_item(conn, stale)
    says(tmp_path, monkeypatch, "SUMMARY: the test expected the old order; updated it.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "tests/test_s.py")
    run_inline(conn, it["run_id"])
    said = fixer.tick(conn)
    cur = selfwork.store_item(conn, stale)
    assert cur["state"] == "judging", (cur["error"], said)
    gate = store.run(conn, cur["run_id"])
    assert gate["provider"] == "command" and cur["run_id"] != it["run_id"]
    assert "tests/test_s.py" in json.loads(cur["touched"]) and "s.txt" in json.loads(cur["touched"])
    assert cur["commit_sha"] == git(cur["worktree"], "rev-parse", "HEAD") != it["commit_sha"]
    assert cur["summary"] == "Change line one built"                # the builder's stays
    assert fixer.of(cur)["summary"] == "the test expected the old order; updated it."
    assert fixer.tried(cur) == "the fixer tried (gate1): the test expected the old order; updated it."
    assert fixer.tick(conn) == []


def test_a_failed_fixer_leaves_the_item(conn, stale):
    offer(conn, stale)
    it = selfwork.store_item(conn, stale)
    store.update_run(conn, it["run_id"], state="failed", error="boom")
    said = fixer.tick(conn)
    cur = selfwork.store_item(conn, stale)
    assert cur["state"] == "left" and cur["error"] == "the fixer run ended failed", said
    assert fixer.tried(cur) == "the fixer tried (gate1): (the fixer run ended failed)"
    assert offer(conn, stale) is None


def test_a_fixer_that_asks_for_a_person_leaves_it_with_its_summary(conn, stale, tmp_path, monkeypatch):
    offer(conn, stale, failure="AssertionError: 2 + 2 == 5")
    it = selfwork.store_item(conn, stale)
    says(tmp_path, monkeypatch, "SUMMARY: a real bug in the intent.\nITEM: person the spec contradicts main\n")
    run_inline(conn, it["run_id"])
    fixer.tick(conn)
    cur = selfwork.store_item(conn, stale)
    assert cur["state"] == "left" and cur["error"] == "the spec contradicts main"
    assert fixer.tried(cur) == "the fixer tried (gate1): a real bug in the intent."


def test_a_conflict_is_left_mid_rebase_for_the_fixer(conn, src, tmp_path, monkeypatch):
    goal(conn, "g1")
    a = item(conn, "g1", "Mine", "s.txt", "mine\n2\n3\n4\n5\n")
    land(conn, "g1", "Theirs", "s.txt", "theirs\n2\n3\n4\n5\n")
    offer(conn, a, stage="resolve", failure="still conflicted")
    it = selfwork.store_item(conn, a)
    run = store.run(conn, it["run_id"])
    assert it["state"] == "fixing" and "conflicts in: s.txt" in run["prompt"]
    wt = Path(it["worktree"])
    (wt / "s.txt").write_text("mine and theirs\n2\n3\n4\n5\n")       # the fixer's resolution
    says(tmp_path, monkeypatch, "SUMMARY: kept both.\nITEM: done\n")
    run_inline(conn, it["run_id"])
    said = fixer.tick(conn)
    cur = selfwork.store_item(conn, a)
    assert cur["state"] == "judging", (cur["error"], said)
    assert git(wt, "rev-parse", "HEAD^") == integration.main()


def test_brief_for_nothing():
    text = fixerbrief.brief(stage="nothing", goal="g", title="t", spec="s", summary="", failure="",
                            head="a" * 40, landed="", conflicted=[])
    assert "ended its turn early without changing anything" in text and "Do the item" in text


def test_daily_counts_fixed_and_left_by_day(conn, src):
    goal(conn, "g1")
    goal(conn, "gp", project="p1")
    today = datetime.date.today()
    noon = time.mktime(datetime.datetime.combine(today, datetime.time(12)).timetuple())
    day = 86400
    rows = [("g1", "proposed", noon, noon),                  # fixed today
            ("g1", "landed", noon - day, noon),              # fixed yesterday
            ("g1", "left", noon - day, noon - day),          # left yesterday after its fixer
            ("g1", "unfit", None, noon),                     # left today
            ("gp", "left", None, noon),                      # a project's: not counted
            ("gp", "proposed", noon, noon)]
    with db.tx(conn):
        for gid, state, at, upd in rows:
            iid = selfwork.new_item(conn, gid, "t", "", [], [], False)
            note = db.dumps({"stage": "gate1", "at": at, "summary": "x"}) if at else None
            conn.execute("UPDATE items SET state=?, fixer=?, updated_at=? WHERE id=?", (state, note, upd, iid))
    got = fixer.daily(conn, 3)
    assert [d["day"] for d in got] == [(today - datetime.timedelta(days=n)).isoformat() for n in range(3)]
    assert (got[0]["fixed"], got[0]["left"]) == (1, 1)
    assert (got[1]["fixed"], got[1]["left"]) == (1, 1)
    assert (got[2]["fixed"], got[2]["left"]) == (0, 0)
