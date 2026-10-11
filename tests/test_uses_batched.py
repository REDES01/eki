"""uses.grid: every card's state and last use in at most two SQL statements, agreeing with
last_use() and waiting() name by name."""
import json

from eki import store
from eki.components import uses


def call(conn, name, folder, state="done", parent=None, t=None):
    prompt = json.dumps({"component": name, "args": "x", "caller_dir": str(folder), "caller_run": parent})
    rid = store.create_run(conn, store.create_thread(conn, f"use {name}", None), prompt,
                           provider="component", parent=parent)
    fields = {"state": state}
    if t is not None:
        conn.execute("UPDATE runs SET created_at=? WHERE id=?", (t, rid))
    if state in ("starting", "running", "done", "failed"):
        fields["started_at"] = (t or 0) + 1
    if state in ("done", "failed"):
        fields["ended_at"] = (t or 0) + 2
    store.update_run(conn, rid, **fields)
    return rid


def caller(conn, state="running", provider="fake"):
    rid = store.create_run(conn, store.create_thread(conn, "caller", None), "do it", provider=provider)
    store.update_run(conn, rid, state=state)
    return rid


def counted(conn, fn):
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        return fn(), seen
    finally:
        conn.set_trace_callback(None)


def test_grid_matches_last_use_and_waiting_in_two_statements(conn, tmp_path):
    a, b, clone = tmp_path / "a", tmp_path / "b", tmp_path / "clone"
    for d in (a, b, clone):
        d.mkdir()
    # a clone eki keeps whose person's folder is `a`: its calls count as a's
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 ("p1", "clone", str(clone.resolve()), "main", 1.0, str(a)))
    names = [f"c{i}" for i in range(16)]
    t = 1000.0
    for i, n in enumerate(names):
        mix = i % 5
        if mix == 0:
            continue                                        # never called
        call(conn, n, a, "done", t=t); t += 10
        if mix >= 2:
            call(conn, n, b, "done", t=t); t += 10              # newest from b, not a
        if mix == 3:
            call(conn, n, a, "queued", t=t); t += 10
        if mix == 4:
            call(conn, n, clone, "running", t=t); t += 10
    specs = {"c3": {"provider": "comfyui"}}
    root = a.resolve()
    got, sql = counted(conn, lambda: uses.grid(conn, names, root, specs=specs))
    assert len([s for s in sql if s.lstrip().upper().startswith(("SELECT", "WITH"))]) <= 2, sql
    assert set(got) == set(names)
    for n in names:
        assert got[n]["last_use"] == uses.last_use(conn, n, root), n
        w = uses.waiting(conn, n)
        running = int(n[1:]) % 5 == 4
        want = "in use" if running else "waiting" if w else "idle"
        assert got[n]["state"] == want, n
    assert got["c0"] == {"state": "idle", "last_use": None}
    assert got["c3"]["state"] == "waiting"
    caller(conn, provider="comfyui")
    assert uses.grid(conn, ["c3"], root, specs=specs)["c3"]["state"] == "busy"


def test_exclusive_held_by_an_active_caller(conn, tmp_path):
    held_by = caller(conn, "running")
    call(conn, "voice", tmp_path, "done", parent=held_by, t=5.0)
    gone = caller(conn, "done")
    call(conn, "other", tmp_path, "done", parent=gone, t=6.0)
    specs = {"voice": {"access": "exclusive"}, "other": {"access": "exclusive"}}
    got = uses.grid(conn, ["voice", "other", "plain"], None, specs=specs)
    assert got["voice"]["state"] == "held"
    assert got["other"]["state"] == "idle"
    assert got["plain"] == {"state": "idle", "last_use": None}
    # without the spec saying exclusive, a finished call doesn't hold it
    assert uses.grid(conn, ["voice"], None)["voice"]["state"] == "idle"


def test_one_query_with_no_root_and_no_providers(conn, tmp_path):
    call(conn, "x", tmp_path, "queued", t=1.0)
    got, sql = counted(conn, lambda: uses.grid(conn, ["x"] * 3 + ["y"], None))
    assert len(sql) == 1
    assert got["x"]["state"] == "waiting" and got["x"]["last_use"] == 1.0
