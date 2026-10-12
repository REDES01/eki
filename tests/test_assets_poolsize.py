"""Pool sizes are measured by housekeeping on a thread of its own and read from a stored file."""
import json

import pytest

from eki import components, housekeep, projects
from eki.components import assets


@pytest.fixture(autouse=True)
def fresh():
    assets._last_measure[0] = 0.0
    yield
    t = assets._thread[0]
    if t is not None:
        t.join(10)
    assets._thread[0] = None


def put(root, name, spec):
    d = root / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps(spec))
    return d


def no_walk(monkeypatch):
    def boom(p):
        raise AssertionError("walked a pool")
    monkeypatch.setattr(assets, "_size", boom)


def test_size_text_and_notes_read_the_stored_value(proj, monkeypatch):
    put(proj, "art", {"kind": "assets", "about": "art", "path": "assets"})
    (proj / ".gitignore").write_text("assets/\n")
    comp = components.get("art", proj)
    key = str((proj / "assets").resolve())
    assets.sizes_file().write_text(json.dumps({key: {"bytes": 1288490189, "at": 1.0}}))
    no_walk(monkeypatch)
    assert assets.stored_size(comp) == 1288490189
    assert assets.size_text(comp) == "1.2 GB"
    assert assets.notes(comp) == [assets.NO_HISTORY, "pool: 1.2 GB"]


def test_missing_or_corrupt_file_gives_nothing(proj, monkeypatch):
    put(proj, "art", {"kind": "assets", "about": "art", "path": "assets"})
    comp = components.get("art", proj)
    no_walk(monkeypatch)
    assert assets.stored_size(comp) is None and assets.size_text(comp) == ""
    for bad in ("{not json", "[1, 2]", json.dumps({str((proj / "assets").resolve()): "x"}),
                json.dumps({str((proj / "assets").resolve()): {"bytes": "big"}})):
        assets.sizes_file().write_text(bad)
        assert assets.stored_size(comp) is None and assets.size_text(comp) == ""
    assert assets.notes(comp)[-1] == "pool: not measured yet"


def test_size_text_is_empty_for_other_kinds(proj):
    put(proj, "voice", {"kind": "service", "about": "speaks", "run": "echo hi"})
    assert assets.size_text(components.get("voice", proj)) == ""


def test_tick_measures_a_projects_card_and_a_pool_one(conn, proj, tmp_path):
    projects.add(conn, proj)
    put(proj, "art", {"kind": "assets", "about": "art", "path": "assets"})
    (proj / "assets").mkdir()
    (proj / "assets" / "a.bin").write_bytes(b"x" * 2048)
    shared = components.pool_dir() / "shared"
    shared.mkdir(parents=True)
    (shared / "component.json").write_text(json.dumps({"kind": "assets", "about": "x", "path": "files"}))
    (shared / "files").mkdir()
    (shared / "files" / "b.bin").write_bytes(b"y" * 5)
    said = assets.tick(conn)
    assert len(said) == 1 and "measuring" in said[0]
    assets._thread[0].join(10)
    got = json.loads(assets.sizes_file().read_text())
    assert got[str((proj / "assets").resolve())]["bytes"] == 2048
    assert got[str((shared / "files").resolve())]["bytes"] == 5
    assert all(isinstance(v["at"], float) for v in got.values())
    assert assets.size_text(components.get("art", proj)) == "2.0 KB"
    assert not list(assets.sizes_file().parent.glob(".poolsizes.*"))   # no temp file left


def test_a_second_tick_within_five_minutes_does_nothing(conn, monkeypatch):
    assert assets.tick(conn)
    assets._thread[0].join(10)
    first = assets._thread[0]
    assert assets.tick(conn) == [] and assets._thread[0] is first
    monkeypatch.setattr(assets, "_last_measure", [assets._last_measure[0] - assets.POOL_EVERY - 1])
    assert assets.tick(conn)


def test_never_two_threads_at_once(conn):
    assert assets._measuring.acquire(blocking=False)           # one still running
    try:
        assert assets.tick(conn) == []
    finally:
        assets._measuring.release()
    assert assets.tick(conn)


def test_registered_in_housekeeping():
    assert ("poolsizes", assets.tick) in housekeep.STEPS
