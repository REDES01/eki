"""The shape of a plan (eki/plangraph.py) and where eki keeps it (eki/selfplan.py)."""
import json

from eki import plangraph, selfbrief, selfwork, store, workspace
from conftest import run_inline


def it(title, files, deps=()):
    return {"title": title, "spec": "", "files": list(files), "deps": list(deps), "independent": False}


def test_all_independent_is_one_deep_and_as_wide_as_the_plan():
    s = plangraph.shape([it("a", ["eki/a.py"]), it("b", ["eki/b.py"]), it("c", ["eki/c.py"])])
    assert s == {"items": 3, "depth": 1, "width": 3, "chained": 0}
    assert plangraph.describe(s) == "3 items · 1 deep · 3 wide"


def test_root_plus_fan_out():
    root = it("root", ["eki/db.py"])
    fan = [it(n, [f"eki/{n}.py", "eki/db.py"], ["root"]) for n in "abcd"]
    s = plangraph.shape([root] + fan)
    assert s == {"items": 5, "depth": 2, "width": 4, "chained": 0}
    assert plangraph.describe(s) == "5 items · 2 deep · 4 wide"


def test_a_chain_and_deps_between_disjoint_write_sets():
    s = plangraph.shape([it("a", ["eki/a.py"]), it("b", ["eki/b.py"], ["a"]),
                         it("c", ["eki/*.py"], ["b"])])
    assert (s["depth"], s["width"]) == (3, 1)
    assert s["chained"] == 1                     # b after a with nothing shared; c's glob covers b
    assert plangraph.shape([it("a", []), it("b", ["x"], ["a"])])["chained"] == 0   # empty: overlaps
    assert plangraph.shape([]) == {"items": 0, "depth": 0, "width": 0, "chained": 0}
    assert plangraph.describe({"items": 1, "depth": 1, "width": 1}) == "1 item · 1 deep · 1 wide"


# ---- conclude_plan stores the shape; a standing round may plan nothing --------------------

def _src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "x.py").write_text("# x\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))


def _plan(conn, tmp_path, monkeypatch, gid, answer):
    p = tmp_path / "says.txt"
    p.write_text(answer)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))
    run_inline(conn, conn.execute("SELECT plan_run FROM goals WHERE id=?", (gid,)).fetchone()[0])
    said = selfwork.tick(conn)
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone(), said


def test_a_plan_stores_its_shape(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "two things")
    g, _ = _plan(conn, tmp_path, monkeypatch, gid,
                 'ITEMS:\n[{"title": "a", "files": ["eki/a.py"]},'
                 ' {"title": "b", "files": ["eki/b.py"], "deps": ["a"]}]\n')
    assert g["state"] == "planned" and g["error"] is None
    assert json.loads(g["shape"]) == {"items": 2, "depth": 2, "width": 1, "chained": 1}


def test_an_empty_plan_fails_an_ordinary_goal(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "anything?")
    g, _ = _plan(conn, tmp_path, monkeypatch, gid, "Nothing to do.\nITEMS:\n[]\n")
    assert g["state"] == "failed" and "no items" in g["error"]


def test_a_standing_round_may_plan_nothing_and_says_why(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "keep it tidy", owner="eki", source_kind="standing", standing_id="s1")
    g, said = _plan(conn, tmp_path, monkeypatch, gid,
                    "I looked.\n\nThe last round already covered it.\n\nITEMS:\n[]\n")
    assert g["state"] == "planned" and g["error"] == "The last round already covered it."
    assert json.loads(g["shape"])["items"] == 0
    assert not conn.execute("SELECT 1 FROM items WHERE goal_id=?", (gid,)).fetchall()
    assert any("nothing worth doing" in s for s in said)
    assert selfbrief.items_in("x\nITEMS: []", allow_empty=True) == []
