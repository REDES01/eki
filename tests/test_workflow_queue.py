"""The queue, the merge and the scheduler follow a project's workflow card (eki/workflow.py)."""
import json
import shutil

import pytest

from eki import builds, components, paths, projectqueue, projects, queue, scheduler, selfwork, store, workflow
from conftest import run_inline
from test_projectqueue import folder, get, item, project, tip  # noqa: F401  (folder is a fixture)
from test_scheduler import _repo


def card(root, **spec):
    where = root / "components" / "workflow"
    where.mkdir(parents=True, exist_ok=True)
    base = json.loads((components.templates_dir() / "workflow" / "component.json").read_text())
    (where / "component.json").write_text(json.dumps({**base, **spec}))
    return where


def test_batch_checks_the_stack_in_company_and_bisects_on_red(conn, folder):
    p = project(conn, folder)
    card(folder, checks=["self", "command", "batch"], command="test ! -f bad.txt")
    good = item(conn, p, "Good", {"b.txt": "bee two\n"})
    bad = item(conn, p, "Bad", {"bad.txt": "nope\n"})
    said = projectqueue.tick(conn)
    G, B = get(conn, good), get(conn, bad)
    assert B["gate2_run"] and not G["gate2_run"], said            # one run, on the tip, judging both
    run = run_inline(conn, B["gate2_run"])
    assert run["prompt"] == json.dumps(["/bin/sh", "-c", "test ! -f bad.txt", "eki-judge"])
    assert store.thread(conn, run["thread_id"])["title"] == "gate 2: batch of 2, up to Bad"
    projectqueue.tick(conn)                                         # red: the middle is checked next
    run_inline(conn, get(conn, good)["gate2_run"])
    projectqueue.tick(conn)
    assert get(conn, good)["state"] == "applied"
    projectqueue.tick(conn)
    it = get(conn, bad)
    assert it["state"] == "unfit" and it["gate2"] == "red"
    assert not (folder / "bad.txt").exists() and (folder / "b.txt").read_text() == "bee two\n"


def test_without_batch_items_land_with_no_gate_two_run(conn, folder):
    p = project(conn, folder, check="false")                       # the old setting: the card says otherwise
    card(folder)
    a = item(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    b = item(conn, p, "Bee", {"b.txt": "bee two\n"})
    said = projectqueue.tick(conn)
    A, B = get(conn, a), get(conn, b)
    assert A["state"] == B["state"] == "applied", said
    assert A["gate2_run"] is None and B["gate2_run"] is None
    assert A["verdict"] == "not batched: checks: self only"
    assert sum("gate 2 skipped" in s for s in said) == 2           # said once per item
    assert not any("gate 2 on" in s for s in said)
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE provider='command'").fetchone()[0] == 0
    assert git_log(folder)[:2] == ["Bee", "Line two"]               # in order


def git_log(r):
    from eki.workspace import git
    return git(r, "log", "--format=%s", "master").splitlines()


def test_merge_person_leaves_a_fit_item_proposed(conn, folder):
    p = project(conn, folder)
    card(folder, merge="person")
    iid = item(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    before = tip(p)
    assert projectqueue.tick(conn) == []
    assert get(conn, iid)["state"] == "proposed" and tip(p) == before


def test_parallel_caps_a_project_s_live_items(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("EKI_SOURCE", str(_repo(tmp_path / "src")))
    proj = _repo(tmp_path / "proj")
    pid = projects.add(conn, proj)
    card(proj, parallel=1)
    gid = selfwork.submit(conn, "two things", plan=False, files=["eki/x.py"], project=pid)
    first = conn.execute("SELECT id FROM items WHERE goal_id=?", (gid,)).fetchone()["id"]
    second = selfwork.new_item(conn, gid, "the other", "do it", ["eki/y.py"], [], False)
    scheduler.start_ready(conn)
    states = {i: conn.execute("SELECT state, why FROM items WHERE id=?", (i,)).fetchone() for i in (first, second)}
    assert states[first]["state"] == "building"
    assert states[second]["state"] == "waiting"
    assert states[second]["why"] == "1 live in proj (its workflow)"
    selfwork._set(conn, first, state="judging")                     # judging is live too
    scheduler.start_ready(conn)
    assert conn.execute("SELECT state FROM items WHERE id=?", (second,)).fetchone()[0] == "waiting"
    selfwork._set(conn, first, state="proposed")
    scheduler.start_ready(conn)
    assert conn.execute("SELECT state FROM items WHERE id=?", (second,)).fetchone()[0] == "building"


def test_no_card_no_extra_cap(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("EKI_SOURCE", str(_repo(tmp_path / "src")))
    proj = _repo(tmp_path / "proj")
    pid = projects.add(conn, proj)
    gid = selfwork.submit(conn, "two things", plan=False, files=["eki/x.py"], project=pid)
    selfwork.new_item(conn, gid, "the other", "do it", ["eki/y.py"], [], False)
    scheduler.start_ready(conn)
    assert {r[0] for r in conn.execute("SELECT state FROM items WHERE goal_id=?", (gid,))} == {"building"}


@pytest.mark.parametrize("mode, merge", [("propose", "person"), ("apply", "auto")])
def test_set_autonomy_writes_eki_s_card(tmp_path, monkeypatch, mode, merge):
    monkeypatch.setenv("EKI_SOURCE", str(_eki_copy(tmp_path).parent.parent.parent))
    queue.set_autonomy(mode)
    assert workflow.of_project(None, None).merge == merge
    assert json.loads(paths.config("routing").read_text())["self"]["autonomy"] == mode
    assert selfwork.settings()["autonomy"] == mode


def _eki_copy(tmp_path):
    eki = tmp_path / "eki"
    shutil.copytree(builds.source() / "components" / "workflow", eki / "components" / "workflow")
    return eki / "components" / "workflow" / "component.json"


@pytest.mark.parametrize("where", ["checkout", "foreign build"])
def test_set_autonomy_never_rewrites_an_unnamed_source(tmp_path, monkeypatch, where):
    """No EKI_SOURCE and no build of this home: the card stays as committed. The source is a copy,
    so the test itself never reaches the real checkout, wherever the suite runs."""
    committed = _eki_copy(tmp_path)
    monkeypatch.delenv("EKI_SOURCE", raising=False)
    monkeypatch.setattr(builds, "source", lambda: committed.parent.parent.parent)
    here = tmp_path / "elsewhere" / "builds" / "b1"                 # a builds/ folder, not this home's
    here.mkdir(parents=True)
    if where == "foreign build":
        (here / ".eki-build.json").write_text(json.dumps({"id": "b1", "source": str(committed.parent)}))
    monkeypatch.setattr(queue, "_here", lambda: here)
    before = committed.read_text()
    queue.set_autonomy("propose")
    assert committed.read_text() == before
    assert selfwork.settings()["autonomy"] == "propose"


def test_set_autonomy_writes_from_a_build_of_this_home(tmp_path, monkeypatch):
    committed = _eki_copy(tmp_path)
    monkeypatch.delenv("EKI_SOURCE", raising=False)
    monkeypatch.setattr(builds, "source", lambda: committed.parent.parent.parent)
    here = paths.home() / "builds" / "b1"
    here.mkdir(parents=True)
    (here / ".eki-build.json").write_text("{}")
    monkeypatch.setattr(queue, "_here", lambda: here)
    queue.set_autonomy("propose")
    assert workflow.of_project(None, None).merge == "person"
