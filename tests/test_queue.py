import json
import sys
from pathlib import Path

import pytest

from eki import db, integration, paths, queue, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline

REAL_GATE2 = queue.GATE2
STUB = '["/bin/sh", "-c", "exit ${EKI_TEST_CHECK_EXIT:-0}"]'


def commit(where, name, text="x\n", message=None):
    p = Path(where) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", message or f"add {name}")
    return workspace.head(where)


@pytest.fixture
def origin(tmp_path):
    o = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(o))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(o), str(seed))
    git(seed, "checkout", "-q", "-b", "main")
    commit(seed, "a.py", message="first")
    commit(seed, "bin/check", "#!/bin/sh\n", message="check")
    git(seed, "push", "-q", "origin", "main")
    return o


@pytest.fixture
def src(tmp_path, origin, monkeypatch):
    s = tmp_path / "src"
    git(tmp_path, "clone", "-q", str(origin), str(s))
    monkeypatch.setenv("EKI_SOURCE", str(s))
    monkeypatch.setattr(queue, "GATE2", STUB)
    return s


def autonomy(mode):
    queue.set_autonomy(mode)


def item(conn, title, name, text="mine\n"):
    """A proposed item built in the integration repo, one commit changing `name`."""
    r = integration.repo()
    with db.tx(conn):
        if not conn.execute("SELECT 1 FROM goals WHERE id='g1'").fetchone():
            conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                         ("g1", "make things", "ask", "you", "planned", db.now()))
        iid = selfwork.new_item(conn, "g1", title, "spec", [name], [], False)
    base = integration.main()
    wt = workspace.add(r, iid, base=base, branch=f"self/{iid}")
    (wt / name).parent.mkdir(parents=True, exist_ok=True)
    (wt / name).write_text(text)
    sha = workspace.commit_all(wt, f"self: {title}")
    with db.tx(conn):
        tid = store.create_thread(conn, f"self: {title}", str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=f"self/{iid}", base=base, thread_id=tid,
                      commit_sha=sha, touched=db.dumps([name]), state="proposed")
    return iid


def get(conn, iid):
    return selfwork.store_item(conn, iid)


def test_no_items_no_repo(conn, src):
    assert queue.tick(conn) == []
    assert not (paths.home() / "self" / "repo").exists()


def test_propose_queues_nothing_until_applied(conn, src):
    autonomy("propose")
    a = item(conn, "A", "a.txt")
    queue.tick(conn)
    assert get(conn, a)["state"] == "proposed"
    assert queue.apply(conn, a) == a
    assert get(conn, a)["state"] == "queued" and get(conn, a)["queued_at"]
    with pytest.raises(ValueError):
        queue.apply(conn, a)


def test_a_locked_file_needs_a_person(conn, src):
    autonomy("apply")
    a = item(conn, "Touch the check", "bin/check", "#!/bin/sh\nexit 0\n")
    said = queue.tick(conn)
    it = get(conn, a)
    assert it["state"] == "locked" and json.loads(it["locked"]) == ["bin/check"], said
    with pytest.raises(ValueError, match="locked files: bin/check"):
        queue.apply(conn, a)
    queue.apply(conn, a, yes=True)
    assert get(conn, a)["state"] == "queued"


def test_three_in_a_row_are_judged_at_once_and_land(conn, src, origin):
    autonomy("apply")
    m = integration.main()
    a, b, c = item(conn, "A", "a.txt"), item(conn, "B", "b.txt"), item(conn, "C", "c.txt")
    said = queue.tick(conn)
    A, B, C = get(conn, a), get(conn, b), get(conn, c)
    assert [x["state"] for x in (A, B, C)] == ["queued"] * 3, said
    assert [x["id"] for x in queue.order(conn)] == [a, b, c]
    assert A["head"] == m and B["head"] == A["rebased"] and C["head"] == B["rebased"]
    runs = [store.run(conn, x["gate2_run"]) for x in (A, B, C)]
    assert all(r["state"] == "queued" and r["provider"] == "command" and r["prompt"] == STUB for r in runs)
    assert all(x["gate2_on"] == x["rebased"] for x in (A, B, C))
    assert queue.tick(conn) == []                                 # nothing new while they run
    assert [get(conn, x)["gate2_run"] for x in (a, b, c)] == [x["gate2_run"] for x in (A, B, C)]
    for r in runs:
        run_inline(conn, r["id"])
    said = queue.tick(conn)
    A, B, C = get(conn, a), get(conn, b), get(conn, c)
    assert [x["state"] for x in (A, B, C)] == ["landed"] * 3, said
    assert A["landed_at"] <= B["landed_at"] <= C["landed_at"]
    assert integration.main() == C["rebased"]
    assert git(origin, "rev-parse", "main") == C["rebased"]
    assert workspace.head(src) == C["rebased"]                   # clean and on main: fast-forwarded
    assert not (paths.work() / f"gate2-{a}").exists()
    assert queue.tick(conn) == []


def test_a_conflict_goes_aside_and_the_rest_go_on(conn, src):
    autonomy("apply")
    m = integration.main()
    a = item(conn, "A", "x.txt", "from A\n")
    b = item(conn, "B", "x.txt", "from B\n")
    c = item(conn, "C", "c.txt")
    said = queue.tick(conn)
    A, B, C = get(conn, a), get(conn, b), get(conn, c)
    assert B["state"] == "resolving" and B["head"] == A["rebased"], said
    assert any("resolving" in s for s in said)
    assert A["head"] == m and C["head"] == A["rebased"]
    assert [x["id"] for x in queue.order(conn)] == [a, c]


def test_a_red_gate_two_rejudges_those_behind(conn, src, monkeypatch):
    autonomy("apply")
    m = integration.main()
    a, b = item(conn, "A", "a.txt"), item(conn, "B", "b.txt")
    queue.tick(conn)
    A, B = get(conn, a), get(conn, b)
    old = B["gate2_run"]
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, A["gate2_run"])
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "0")
    run_inline(conn, old)                                      # green, but on a head with A: thrown away
    said = queue.tick(conn)
    A, B = get(conn, a), get(conn, b)
    assert A["state"] == "unfit" and A["error"] == "gate 2 failed" and A["gate2"] == "red", said
    assert B["state"] == "queued" and B["head"] == m and B["gate2_run"] != old and B["gate2"] is None
    assert not (Path(B["worktree"]) / "a.txt").exists()          # A's commit left behind
    assert git(B["worktree"], "rev-parse", "HEAD^") == m
    run_inline(conn, B["gate2_run"])
    queue.tick(conn)
    assert get(conn, b)["state"] == "landed" and integration.main() == get(conn, b)["rebased"]
    assert not integration.contains(A["rebased"], "main")


def test_merged_by_hand_shows_landed(conn, src):
    autonomy("propose")
    a = item(conn, "A", "a.txt")
    git(integration.repo(), "merge", "-q", "--ff-only", get(conn, a)["commit_sha"])
    said = queue.tick(conn)
    it = get(conn, a)
    assert it["state"] == "landed" and it["gate2"] == "green" and it["landed_at"], said


def test_items_from_before_the_queue_are_left_alone(conn, src, tmp_path):
    autonomy("apply")
    a = item(conn, "A", "a.txt")
    old = workspace.add(src, "old", base="HEAD", branch="self/old")
    with db.tx(conn):
        selfwork._set(conn, a, worktree=str(old))
    queue.tick(conn)
    assert get(conn, a)["state"] == "proposed"


def test_set_autonomy_keeps_other_keys(conn):
    path = paths.config("routing")
    data = json.loads(path.read_text())
    data["self"] = {"parallel": 2}
    path.write_text(json.dumps(data))
    queue.set_autonomy("apply")
    got = json.loads(path.read_text())
    assert got["self"] == {"parallel": 2, "autonomy": "apply"} and got["rows"] == data["rows"]
    assert selfwork.settings()["autonomy"] == "apply"
    with pytest.raises(ValueError):
        queue.set_autonomy("yolo")


def test_a_failed_push_is_retried_later(conn, src, origin, tmp_path):
    autonomy("apply")
    a = item(conn, "A", "a.txt")
    r = integration.repo()
    git(r, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    queue.tick(conn)
    run_inline(conn, get(conn, a)["gate2_run"])
    queue.tick(conn)
    A = get(conn, a)
    assert A["state"] == "landed" and integration.main() == A["rebased"]
    assert git(origin, "rev-parse", "main") != A["rebased"]
    git(r, "remote", "set-url", "origin", str(origin))
    queue.tick(conn)                                              # main is ahead of origin: pushed now
    assert git(origin, "rev-parse", "main") == A["rebased"]


def test_gate_two_runs_the_fast_tests_without_the_candidate_checks(conn, src, monkeypatch):
    monkeypatch.setattr(queue, "GATE2", REAL_GATE2)
    autonomy("apply")
    a = item(conn, "A", "a.py")
    queue.tick(conn)
    prompt = store.run(conn, get(conn, a)["gate2_run"])["prompt"]
    assert "EKI_CHECK_FAST=1" in prompt and "bin/check" in prompt
    assert "eki.candidate" not in prompt


def test_a_docs_only_item_gets_the_doccheck(conn, src):
    autonomy("apply")
    a = item(conn, "A", "docs/a.md")
    said = queue.tick(conn)
    A = get(conn, a)
    prompt = store.run(conn, A["gate2_run"])["prompt"]
    assert "eki.doccheck" in prompt and f"{A['head']}..{A['rebased']}" in prompt
    assert "bin/check" not in prompt
    assert any("docs only" in line for line in said), said


def test_a_green_doccheck_lands_the_item(conn, tmp_path, origin, monkeypatch):
    seed = tmp_path / "seed"                      # the doccheck runs from the gate tree: put it there
    (seed / "eki").mkdir(exist_ok=True)
    (seed / "eki" / "__init__.py").write_text("")
    commit(seed, "eki/doccheck.py", (Path(__file__).parents[1] / "eki" / "doccheck.py").read_text())
    git(seed, "push", "-q", "origin", "main")
    s = tmp_path / "src"
    git(tmp_path, "clone", "-q", str(origin), str(s))
    monkeypatch.setenv("EKI_SOURCE", str(s))
    monkeypatch.setenv("EKI_PYTHON", sys.executable)
    autonomy("apply")
    a = item(conn, "A", "notes.md", "# notes\n")
    queue.tick(conn)
    run = run_inline(conn, get(conn, a)["gate2_run"])
    assert run["state"] == "done", store.answer(conn, run["id"])
    said = queue.tick(conn)
    A = get(conn, a)
    assert A["state"] == "landed" and integration.main() == A["rebased"], said
