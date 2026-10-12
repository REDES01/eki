"""Hot spots on the goal: eki/selfplan.py writes eki/hotspots.py's warnings when a plan concludes."""
import json

from eki import hotspots, selfview, selfwork, store, workspace
from conftest import run_inline


def _src(tmp_path, monkeypatch, files=None):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    for name, text in (files or {"eki/x.py": "# x\n"}).items():
        (r / name).parent.mkdir(parents=True, exist_ok=True)
        (r / name).write_text(text)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-f", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def _answer(conn, tmp_path, monkeypatch, gid, answer):
    p = tmp_path / "says.txt"
    p.write_text(answer)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))
    run_inline(conn, conn.execute("SELECT plan_run FROM goals WHERE id=?", (gid,)).fetchone()[0])


def _goal(conn, gid):
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def _items(conn, gid):
    rows = conn.execute("SELECT id, title, deps FROM items WHERE goal_id=? ORDER BY rowid", (gid,)).fetchall()
    by_id = {r["id"]: r["title"] for r in rows}
    return [(r["title"], sorted(by_id[d] for d in json.loads(r["deps"] or "[]"))) for r in rows]


def _notes(conn, g):
    return [json.loads(e["data"])["text"] for e in store.events_after(conn, g["plan_run"])
            if e["kind"] == "note"]


FOUR = ('ITEMS:\n[' + ", ".join(f'{{"title": "{n}", "files": ["eki/{n}.py", "q.json"]}}' for n in "abcd")
        + ']\n')


def test_one_file_in_four_items_is_warned_and_split_first(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "four things")
    _answer(conn, tmp_path, monkeypatch, gid, FOUR)
    said = selfwork.tick(conn)
    g = _goal(conn, gid)
    assert g["state"] == "planned"
    spots = json.loads(g["hot"])
    assert [(s["file"], len(s["items"]), s["generated"]) for s in spots] == [("q.json", 4, False)]
    notes = _notes(conn, g)
    assert len(notes) == 1
    assert "q.json is in 4 items — split it first, or they will conflict" in notes[0]
    assert hotspots.SPLIT_TITLE in notes[0].splitlines()[-1]
    got = _items(conn, gid)
    assert got[0] == (hotspots.SPLIT_TITLE, [])
    assert got[1:] == [(n, [hotspots.SPLIT_TITLE]) for n in "abcd"]
    assert json.loads(g["shape"])["items"] == 5
    assert any("1 hot spot" in s for s in said)


def test_a_tracked_ignored_file_is_flagged_as_generated(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch, {"eki/x.py": "# x\n", ".gitignore": "story.gen.json\n",
                                 "story.gen.json": "{}\n"})
    gid = selfwork.submit(conn, "one thing")
    _answer(conn, tmp_path, monkeypatch, gid, 'ITEMS:\n[{"title": "a", "files": ["eki/a.py"]}]\n')
    selfwork.tick(conn)
    spots = json.loads(_goal(conn, gid)["hot"])
    assert {"file": "story.gen.json", "items": [], "generated": True} in spots


def test_a_plan_without_hot_spots_is_unchanged(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "two things")
    _answer(conn, tmp_path, monkeypatch, gid,
            'ITEMS:\n[{"title": "a", "files": ["eki/a.py", "q.json"]},'
            ' {"title": "b", "files": ["eki/b.py", "q.json"], "deps": ["a"]}]\n')
    selfwork.tick(conn)
    g = _goal(conn, gid)
    assert g["state"] == "planned" and g["hot"] is None
    assert _items(conn, gid) == [("a", []), ("b", ["a"])]
    assert _notes(conn, g) == []
    assert selfview._goal(conn, g)["hot"] == []


def test_conclude_waits_for_the_dry_build_and_flags_what_it_rewrote(conn, tmp_path, monkeypatch):
    repo = _src(tmp_path, monkeypatch, {"eki/x.py": "# x\n", "data/story.json": "{}\n"})
    gid = selfwork.submit(conn, "one thing")
    dry = store.create_run(conn, store.create_thread(conn, "dry build", None, by="eki"), "bin/check")
    conn.execute("UPDATE goals SET dry_run=? WHERE id=?", (dry, gid))
    wt = workspace.add(repo, f"dry-{gid}", branch=f"eki/dry-{gid}")
    (wt / "data" / "story.json").write_text('{"built": true}\n')
    _answer(conn, tmp_path, monkeypatch, gid, 'ITEMS:\n[{"title": "a", "files": ["eki/a.py"]}]\n')

    selfwork.tick(conn)
    assert _goal(conn, gid)["state"] == "planning"          # the dry build is still queued

    store.update_run(conn, dry, state="failed")             # a red check still says what it rewrote
    selfwork.tick(conn)
    g = _goal(conn, gid)
    assert g["state"] == "planned"
    assert [(s["file"], s["generated"]) for s in json.loads(g["hot"])] == [("data/story.json", True)]
    assert not workspace.path_for(f"dry-{gid}").exists()


def test_selfview_goal_carries_the_warnings(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "four things")
    _answer(conn, tmp_path, monkeypatch, gid, FOUR)
    selfwork.tick(conn)
    hot = selfview._goal(conn, _goal(conn, gid))["hot"]
    assert hot == ["q.json is in 4 items — split it first, or they will conflict"]
