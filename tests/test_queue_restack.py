"""A resolved item carries only its own commits, and a queue-mate's failure is never
blamed on it (eki/resolve.py, eki/queue.py `_rebase`)."""
from pathlib import Path

from eki import db, integration, queue, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline
from test_queue import autonomy, get, item, no_fixer, origin, src  # noqa: F401  (fixtures)


def conflicting(conn, tmp_path, monkeypatch):
    """A changes x.txt and adds bad.txt; B changes the same line of x.txt, so B stops on A
    and is resolved onto it. The b8ae5910a7 / 5d3a0c543f case."""
    autonomy("apply")
    a = item(conn, "A", "x.txt", "from A\n")
    wt = Path(get(conn, a)["worktree"])
    (wt / "bad.txt").write_text("broken\n")
    with db.tx(conn):
        selfwork._set(conn, a, commit_sha=workspace.commit_all(wt, "self: A, broken"),
                      touched=db.dumps(["x.txt", "bad.txt"]))
    b = item(conn, "B", "x.txt", "from B\n")
    queue.tick(conn)
    A, B = get(conn, a), get(conn, b)
    assert B["state"] == "resolving" and B["head"] == A["rebased"] == B["resolved_on"]
    says = tmp_path / "says.txt"
    says.write_text("SUMMARY: kept both.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(says))
    return a, b


def resolve_b(conn, b, text):
    B = get(conn, b)
    (Path(B["worktree"]) / "x.txt").write_text(text)
    run_inline(conn, B["run_id"])


def gate2_red_on_bad(conn, monkeypatch, rid):
    tree = Path(store.cwd_of(conn, store.run(conn, rid)["thread_id"]))
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1" if (tree / "bad.txt").exists() else "0")
    run_inline(conn, rid)


def test_a_resolved_item_is_never_blamed_for_a_queue_mate(conn, src, tmp_path, monkeypatch):
    m = integration.main()
    no_fixer()                                     # A's red is unfit at once
    a, b = conflicting(conn, tmp_path, monkeypatch)
    A = get(conn, a)
    resolve_b(conn, b, "from A\nfrom B\n")
    said = queue.tick(conn)
    B = get(conn, b)
    assert B["state"] == "queued" and B["head"] == A["rebased"] == B["resolved_on"], said
    assert [x["id"] for x in queue.order(conn)] == [a, b]               # at the back
    assert all(r["provider"] != "command" for r in store.thread_runs(conn, B["thread_id"]))   # no gate 1
    said = []
    for _ in range(12):
        A, B = get(conn, a), get(conn, b)
        assert B["state"] != "unfit", (B["error"], said)
        if B["state"] == "landed":
            break
        if B["state"] == "resolving" and store.run(conn, B["run_id"])["state"] in store.ACTIVE:
            resolve_b(conn, b, "from A\nfrom B\n" if B["head"] == A["rebased"] else "from B\n")
        for x in (A, B):
            if x["state"] == "queued" and x["gate2_run"] and store.run(conn, x["gate2_run"])["state"] in store.ACTIVE:
                gate2_red_on_bad(conn, monkeypatch, x["gate2_run"])
        said += queue.tick(conn)
    A, B = get(conn, a), get(conn, b)
    assert A["state"] == "unfit" and A["error"] == "gate 2 failed", said
    assert B["state"] == "landed", (B["state"], B["error"], said)
    assert any(f"without {a} (left the queue)" in s for s in said), said
    assert not integration.contains(A["rebased"], "main")
    r = integration.repo()
    assert "bad.txt" not in git(r, "ls-tree", "--name-only", "main").split()
    assert git(r, "show", "main:x.txt") == "from B"
    mine = git(r, "cherry", m, B["rebased"]).splitlines()
    assert len(mine) == 1 and git(r, "log", "-1", "--format=%s", mine[0].split()[1]) == "self: B"


def test_a_re_applied_resolved_item_moves_only_its_own_commits(conn, src, tmp_path, monkeypatch):
    m = integration.main()
    a, b = conflicting(conn, tmp_path, monkeypatch)
    A = get(conn, a)
    resolve_b(conn, b, "from A\nfrom B\n")
    queue.tick(conn)
    with db.tx(conn):                                     # B left for a person, A failed gate 2
        selfwork._set(conn, b, state="left", error="by hand")
        selfwork._set(conn, a, state="unfit", error="gate 2 failed")
        selfwork._set(conn, b, state="proposed")
    queue.apply(conn, b)
    B = get(conn, b)
    assert B["head"] is None and B["rebased"] is None and B["resolved_on"] == A["rebased"]
    asked = []
    monkeypatch.setattr(queue.rebase, "onto", lambda wt, h, since=None: asked.append((h, since)) or [])
    sha, said = queue._rebase(conn, B, m)
    assert asked == [(m, A["rebased"])]                   # `rebase --onto m <A>`: A stays behind
    assert said == [f"item {b}: re-stacked on {m[:12]} without {a} (left the queue)"]
    assert get(conn, b)["resolved_on"] == m
