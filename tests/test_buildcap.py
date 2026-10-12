"""The build cap in force for a project: the machine's agents at once against the
project's own workflow card, other projects' items counted against the machine."""
import json
import types

from eki import buildcap, components, db, machinecaps, projects, selfwork, store, workflowgates, workspace


def _repo(r, parallel=None):
    r.mkdir(parents=True)
    (r / "a.txt").write_text("a\n")
    if parallel is not None:
        where = r / "components" / "workflow"
        where.mkdir(parents=True)
        base = json.loads((components.templates_dir() / "workflow" / "component.json").read_text())
        (where / "component.json").write_text(json.dumps({**base, "parallel": parallel}))
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def building(conn, project, n, state="building"):
    """A goal in `project` (None: eki) with n items in `state`."""
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, state, project, created_at) VALUES (?,?,?,?,?)",
                     (gid, "a goal", "planned", project, db.now()))
        for i in range(n):
            conn.execute("INSERT INTO items(id, goal_id, title, state, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?)", (store.new_id(), gid, f"item {i}", state, db.now(), db.now()))


def test_the_machine_is_smaller(conn, tmp_path):
    machinecaps.save(agents=6)
    pid = projects.add(conn, _repo(tmp_path / "p", parallel=24))
    building(conn, pid, 5)
    building(conn, pid, 3, state="judging")          # only building items count
    got = buildcap.of(conn, pid)
    assert got == {"building": 5, "others": 0, "machine": 6, "project": 24, "now": 6, "by": "machine",
                   "line": "5 building · up to 6 now (machine) · this project allows 24"}


def test_the_project_is_smaller(conn, tmp_path):
    machinecaps.save(agents=12)
    pid = projects.add(conn, _repo(tmp_path / "p", parallel=4))
    building(conn, pid, 3)
    got = buildcap.of(conn, pid)
    assert (got["by"], got["now"], got["project"]) == ("project", 4, 4)
    assert got["line"] == "3 building · up to 4 now (this project) · the machine allows 12"
    other = projects.add(conn, _repo(tmp_path / "q"))
    building(conn, other, 2)
    assert buildcap.of(conn, pid)["line"] == (
        "3 building · up to 4 now (this project) · the machine allows 12 (2 in other projects)")


def test_other_projects_use_some_of_the_machine(conn, tmp_path):
    machinecaps.save(agents=6)
    pid = projects.add(conn, _repo(tmp_path / "p", parallel=24))
    other = projects.add(conn, _repo(tmp_path / "q"))
    building(conn, pid, 5)
    building(conn, other, 1)
    building(conn, None, 1)                          # eki's own count as another project's
    got = buildcap.of(conn, pid)
    assert (got["building"], got["others"], got["now"], got["by"]) == (5, 2, 4, "machine")
    assert got["line"] == "5 building · up to 4 now (machine: 6, 2 in other projects) · this project allows 24"


def test_a_tie_is_the_machine(conn, tmp_path):
    machinecaps.save(agents=6)
    pid = projects.add(conn, _repo(tmp_path / "p", parallel=6))
    assert buildcap.of(conn, pid)["by"] == "machine"


def test_a_project_with_no_card(conn, tmp_path):
    machinecaps.save(agents=6)
    pid = projects.add(conn, _repo(tmp_path / "p"))
    building(conn, pid, 2)
    got = buildcap.of(conn, pid)
    assert (got["project"], got["now"], got["by"]) == (None, 6, "machine")
    assert got["line"] == "2 building · up to 6 now (machine) · this project has no cap of its own"
    assert buildcap.project_cap(conn, "no-such-project") is None


def test_eki_itself(conn, tmp_path, monkeypatch):
    machinecaps.save(agents=6)
    pid = projects.add(conn, _repo(tmp_path / "p"))
    building(conn, None, 2)
    building(conn, pid, 1)
    assert buildcap.project_cap(conn, None) == int(selfwork.settings()["parallel"])
    assert buildcap.of(conn, None) == buildcap.of(conn, "eki")
    monkeypatch.setattr(workflowgates, "own", lambda: types.SimpleNamespace(parallel=3))
    got = buildcap.of(conn, "eki")
    assert got == buildcap.of(conn, None)
    assert (got["building"], got["others"], got["project"], got["now"], got["by"]) == (2, 1, 3, 3, "project")
    assert got["line"] == "2 building · up to 3 now (this project) · the machine allows 6 (1 in other projects)"
