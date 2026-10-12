"""Gate 2 red and failed resolves get the item's one fixer run before unfit or left (eki/fixer.py)."""
import json
from pathlib import Path

import pytest

from eki import db, fixer, fixerbrief, integration, paths, queue, rebase, resolve, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline

#: the repo's own bin/check: every file in expect/ must match order.txt
CHECK_SH = ('#!/bin/sh\nfor f in expect/*; do [ -e "$f" ] || continue\n'
            '  cmp -s "$f" order.txt || { echo "FAILED $f - expected the old order"; exit 1; }\ndone\n')
RUN_CHECK = '["/bin/sh", "-c", "sh bin/check"]'
GREEN = '["/bin/sh", "-c", "exit 0"]'


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
    commit(seed, "order.txt", "a b\n", "first")
    commit(seed, "x.txt", "x\n", "x")
    commit(seed, "bin/check", CHECK_SH, "check")
    git(seed, "push", "-q", "origin", "main")
    s = tmp_path / "src"
    git(tmp_path, "clone", "-q", str(o), str(s))
    monkeypatch.setenv("EKI_SOURCE", str(s))
    monkeypatch.setattr(queue, "GATE2", RUN_CHECK)
    monkeypatch.setattr(selfwork, "CHECK", RUN_CHECK)
    queue.set_autonomy("apply")
    path = paths.config("routing")
    data = json.loads(path.read_text())
    data["self"] = {**data["self"], "review": False}
    path.write_text(json.dumps(data))
    return s


def item(conn, title, files):
    """A proposed item built on integration main, one commit writing `files` {name: text}."""
    with db.tx(conn):
        if not conn.execute("SELECT 1 FROM goals WHERE id='g1'").fetchone():
            conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                         ("g1", "make things", "ask", "you", "planned", db.now()))
        iid = selfwork.new_item(conn, "g1", title, f"spec of {title}", list(files), [], False)
    base = integration.main()
    wt = workspace.add(integration.repo(), iid, base=base, branch=f"self/{iid}")
    for name, text in files.items():
        (wt / name).parent.mkdir(parents=True, exist_ok=True)
        (wt / name).write_text(text)
    sha = workspace.commit_all(wt, f"self: {title}")
    with db.tx(conn):
        tid = store.create_thread(conn, f"self: {title}", str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=f"self/{iid}", base=base, thread_id=tid,
                      commit_sha=sha, touched=db.dumps(list(files)), summary=f"{title} built", state="proposed")
    return iid


def get(conn, iid):
    return selfwork.store_item(conn, iid)


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


def gate2(conn, iid):
    """Run the item's gate 2 and tick the queue."""
    run_inline(conn, get(conn, iid)["gate2_run"])
    return queue.tick(conn)


@pytest.fixture
def stale(conn, src):
    """A swaps the order (and its own expectation); B expects the old order. B's gate 2 is red
    on top of A; A lands; B, at the front, gets its fixer on the new main."""
    a = item(conn, "Swap the order", {"order.txt": "b a\n", "expect/a": "b a\n"})
    b = item(conn, "Add b", {"b.txt": "b\n", "expect/b": "a b\n"})
    queue.tick(conn)
    assert get(conn, b)["head"] == get(conn, a)["rebased"] and get(conn, b)["gate2_run"]
    gate2(conn, b)                                               # the batch is red: A alone next
    said = gate2(conn, a)                                        # A alone is green: it lands, B is the bad one
    assert get(conn, a)["state"] == "landed" and integration.main() == get(conn, a)["rebased"], said
    return a, b, said


def test_a_stale_test_is_fixed_and_lands(conn, stale, tmp_path, monkeypatch):
    a, b, said = stale
    it = get(conn, b)
    assert it["state"] == "fixing" and f"item {b}: gate2 failed — the fixer is on it ({it['run_id'][:8]})" in said
    main = integration.main()
    wt = Path(it["worktree"])
    assert it["base"] == main and git(wt, "rev-parse", "HEAD^") == main   # on top of the new main
    assert (wt / "order.txt").read_text() == "b a\n"
    prompt = store.run(conn, it["run_id"])["prompt"]
    assert prompt.startswith(fixerbrief.FIRST_LINE) and "expected the old order" in prompt
    assert fixer.of(it)["stage"] == "gate2" and b not in [x["id"] for x in queue.order(conn)]
    (wt / "expect" / "b").write_text("b a\n")                    # the fixer: A made B's expectation wrong
    says(tmp_path, monkeypatch, "SUMMARY: A swapped the order; B's expectation follows it.\nITEM: done\n")
    run_inline(conn, it["run_id"])
    said = fixer.tick(conn)
    cur = get(conn, b)
    assert cur["state"] == "judging", (cur["error"], said)        # gate 1 again
    run_inline(conn, cur["run_id"])
    selfwork.tick(conn)
    assert get(conn, b)["state"] == "proposed", get(conn, b)["error"]
    queue.tick(conn)                                             # apply: queued, gate 2
    assert get(conn, b)["state"] == "queued" and get(conn, b)["gate2_run"]
    said = gate2(conn, b)
    cur = get(conn, b)
    assert cur["state"] == "landed" and integration.main() == cur["rebased"], said
    assert git(integration.repo(), "show", "main:expect/b") == "b a"
    assert fixer.daily(conn, 1)[0]["fixed"] == 1


def test_a_second_gate_two_red_after_the_fixer_is_unfit_with_its_summary(conn, stale, tmp_path, monkeypatch):
    _, b, _ = stale
    monkeypatch.setattr(selfwork, "CHECK", GREEN)                # gate 1 lets it by; gate 2 is still red
    it = get(conn, b)
    says(tmp_path, monkeypatch, "SUMMARY: I could not see why the order matters.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "notes.txt")
    run_inline(conn, it["run_id"])
    fixer.tick(conn)
    run_inline(conn, get(conn, b)["run_id"])
    selfwork.tick(conn)
    queue.tick(conn)
    said = gate2(conn, b)
    cur = get(conn, b)
    assert cur["state"] == "unfit" and cur["gate2"] == "red", said
    assert cur["error"] == "gate 2 failed — the fixer tried (gate2): I could not see why the order matters."
    assert len([r for r in store.thread_runs(conn, cur["thread_id"])
                if r["prompt"].startswith(fixerbrief.FIRST_LINE)]) == 1      # one fixer run per item


@pytest.fixture
def resolving(conn, src):
    """A and B change the same line; A lands, B is resolving onto it."""
    a = item(conn, "A", {"x.txt": "from A\n"})
    b = item(conn, "B", {"x.txt": "from B\n"})
    queue.tick(conn)
    assert get(conn, b)["state"] == "resolving"
    gate2(conn, a)
    assert get(conn, a)["state"] == "landed"
    return b


def test_a_failed_resolve_gets_one_fixer_not_left(conn, resolving, tmp_path, monkeypatch):
    b = resolving
    says(tmp_path, monkeypatch, "Kept both.\n")                  # no ITEM line, twice
    monkeypatch.setenv("EKI_FAKE_TOUCH", "x.txt")
    run_inline(conn, get(conn, b)["run_id"])
    resolve.tick(conn)
    assert get(conn, b)["state"] == "resolving"                  # the resolver's second try
    run_inline(conn, get(conn, b)["run_id"])
    said = resolve.tick(conn)
    cur = get(conn, b)
    assert cur["state"] == "fixing", (cur["error"], said)
    assert said == [f"item {b}: resolve failed — the fixer is on it ({cur['run_id'][:8]})"]
    note = fixer.of(cur)
    assert note["stage"] == "resolve" and note["failure"] == "no ITEM line after a second resolve run"
    assert rebase.in_progress(cur["worktree"])                   # mid-rebase onto main, for the fixer
    assert "conflicts in: x.txt" in store.run(conn, cur["run_id"])["prompt"]


def test_a_resolver_asking_for_a_person_gets_no_fixer(conn, resolving, tmp_path, monkeypatch):
    b = resolving
    says(tmp_path, monkeypatch, "SUMMARY: they contradict.\nITEM: person the two intents contradict\n")
    run_inline(conn, get(conn, b)["run_id"])
    resolve.tick(conn)
    cur = get(conn, b)
    assert cur["state"] == "left" and cur["error"] == "the two intents contradict"
    assert cur["fixer"] is None and not rebase.in_progress(cur["worktree"])


def test_a_left_item_that_had_its_fixer_says_what_it_tried(conn, resolving, tmp_path, monkeypatch):
    b = resolving
    with db.tx(conn):
        selfwork._set(conn, b, fixer=db.dumps({"stage": "gate2", "summary": "updated the test", "at": db.now()}))
    store.update_run(conn, get(conn, b)["run_id"], state="failed", error="boom")
    resolve.tick(conn)
    cur = get(conn, b)
    assert cur["state"] == "left"
    assert cur["error"] == "the resolve run ended failed: boom — the fixer tried (gate2): updated the test"
