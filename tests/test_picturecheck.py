"""The local classifier's look at new pictures, with a fake classifier (never the real nudenet)."""
import json
import sys

import pytest

from eki import paths, picturecheck, sensitive, store

REAL_AVAILABLE = picturecheck.available


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(picturecheck, "_said_missing", [False])
    monkeypatch.setattr(picturecheck, "_seen_upto", [None])
    monkeypatch.setattr(picturecheck, "available", lambda: True)


def draw(conn, n, t0=1000.0):
    """n pictures in one run, oldest first; their paths."""
    tid = store.create_thread(conn, "pictures", None)
    rid = store.create_run(conn, tid, "draw", provider="comfyui", row="image")
    out = []
    for i in range(n):
        pic = paths.home() / "images" / rid / f"eki_{i:05d}_.png"
        pic.parent.mkdir(parents=True, exist_ok=True)
        pic.write_bytes(b"\x89PNG fake")
        conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                     (rid, 1, t0 + i, "tool", json.dumps({"name": "image", "path": str(pic)})))
        out.append(str(pic))
    return out


def scores(monkeypatch, by_name):
    looked = []

    def fake(path):
        looked.append(path)
        v = by_name[path.rsplit("/", 1)[1]]
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(picturecheck, "_classify", fake)
    return looked


def row(conn, path):
    return conn.execute("SELECT * FROM sensitive WHERE path = ?", (sensitive.norm(path),)).fetchone()


def test_on_and_off_at_the_threshold(conn, monkeypatch):
    a, b, c = draw(conn, 3)
    scores(monkeypatch, {"eki_00000_.png": 0.6, "eki_00001_.png": 0.59, "eki_00002_.png": 0.0})
    said = picturecheck.tick(conn)
    assert row(conn, a)["state"] == "on" and row(conn, a)["by"] == "auto" and row(conn, a)["score"] == 0.6
    assert row(conn, b)["state"] == "off" and row(conn, b)["score"] == 0.59
    assert row(conn, c)["state"] == "off"
    assert len(said) == 1 and a in said[0]
    looked = scores(monkeypatch, {})
    assert picturecheck.tick(conn) == [] and looked == []      # each is looked at once


def test_a_persons_mark_is_kept(conn, monkeypatch):
    a, b = draw(conn, 2)
    sensitive.mark(conn, a, False)                              # the person looked first
    sensitive.mark(conn, b, True)
    looked = scores(monkeypatch, {"eki_00000_.png": 0.99, "eki_00001_.png": 0.0})
    picturecheck.tick(conn)
    assert looked == []                                         # already seen: never looked at
    assert row(conn, a)["by"] == "person" and row(conn, a)["state"] == "off"
    assert row(conn, b)["by"] == "person" and row(conn, b)["state"] == "on"


def test_a_persons_mark_wins_even_mid_pass(conn, monkeypatch):
    (a,) = draw(conn, 1)

    def fake(path):
        sensitive.mark(conn, path, False)                       # the person marks it while it is looked at
        return 0.99
    monkeypatch.setattr(picturecheck, "_classify", fake)
    assert picturecheck.tick(conn) == []
    assert row(conn, a)["by"] == "person" and row(conn, a)["state"] == "off"


def test_the_batch_limit(conn, monkeypatch):
    pics = draw(conn, 75)                                       # more than one gallery page
    looked = scores(monkeypatch, {p.rsplit("/", 1)[1]: 0.1 for p in pics})
    picturecheck.tick(conn)
    assert len(looked) == picturecheck.BATCH
    assert looked == list(reversed(pics))[:picturecheck.BATCH]  # newest first
    for _ in range(4):
        picturecheck.tick(conn)
    assert sorted(looked) == sorted(pics)
    assert conn.execute("SELECT COUNT(*) FROM sensitive").fetchone()[0] == 75
    new = draw(conn, 1, t0=5000.0)                              # a new one after everything was seen
    picturecheck.tick(conn)
    assert looked[-1] == new[0] and len(looked) == 76


def test_not_installed_does_nothing_and_says_so_once(conn, monkeypatch):
    draw(conn, 2)
    monkeypatch.setattr(picturecheck, "available", lambda: False)
    looked = scores(monkeypatch, {})
    assert picturecheck.tick(conn) == [] and picturecheck.tick(conn) == []
    notes = conn.execute("SELECT data FROM journal WHERE kind = 'picturecheck'").fetchall()
    assert len(notes) == 1 and "pip install nudenet" in json.loads(notes[0]["data"])["note"]
    assert conn.execute("SELECT COUNT(*) FROM sensitive").fetchone()[0] == 0 and looked == []


def test_not_installed_and_nothing_drawn_says_nothing(conn, monkeypatch):
    monkeypatch.setattr(picturecheck, "available", lambda: False)
    assert picturecheck.tick(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE kind = 'picturecheck'").fetchone()[0] == 0


def test_available_is_false_without_nudenet(monkeypatch):
    monkeypatch.setitem(sys.modules, "nudenet", None)          # importing it raises ImportError
    assert REAL_AVAILABLE() is False


def test_a_failing_picture_is_marked_off_and_journaled(conn, monkeypatch):
    a, b = draw(conn, 2)
    scores(monkeypatch, {"eki_00000_.png": OSError("cannot identify image"), "eki_00001_.png": 0.9})
    picturecheck.tick(conn)
    assert row(conn, a)["state"] == "off" and row(conn, a)["score"] is None and row(conn, a)["by"] == "auto"
    assert row(conn, b)["state"] == "on"
    notes = [json.loads(r["data"]) for r in conn.execute("SELECT data FROM journal WHERE kind = 'picturecheck'")]
    assert len(notes) == 1 and notes[0]["path"] == a and "cannot identify image" in notes[0]["note"]
    looked = scores(monkeypatch, {})
    picturecheck.tick(conn)
    assert looked == []                                         # the queue does not stick on it


def test_classify_takes_the_highest_exposed_score(monkeypatch):
    class Fake:
        def detect(self, path):
            return [{"class": "FACE_FEMALE", "score": 0.99}, {"class": "FEMALE_BREAST_EXPOSED", "score": 0.7},
                    {"class": "BUTTOCKS_EXPOSED", "score": 0.65}, {"class": "FEMALE_BREAST_COVERED", "score": 0.95}]
    monkeypatch.setattr(picturecheck, "_detector", [Fake()])
    assert picturecheck._classify("/x.png") == 0.7
    monkeypatch.setattr(picturecheck, "_detector", [type("None_", (), {"detect": lambda s, p: []})()])
    assert picturecheck._classify("/x.png") == 0.0
