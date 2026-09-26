"""Pictures sent with a request: stored on the run as absolute paths."""
import sqlite3

import pytest

from eki import api, asking, db, paths, store


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


def test_submit_stores_absolute_path(png, monkeypatch):
    monkeypatch.chdir(png.parent)
    c = db.connect()
    _, rid = asking.submit(c, "what is this", attachments=["pic.png"])
    assert store.attachments(store.run(c, rid)) == [str(png)]


def test_missing_file_raises(tmp_path):
    c = db.connect()
    with pytest.raises(ValueError, match="no file"):
        asking.submit(c, "look", attachments=[str(tmp_path / "gone.png")])
    assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_no_attachments_is_empty():
    c = db.connect()
    _, rid = asking.submit(c, "hello")
    assert store.attachments(store.run(c, rid)) == []
    c.execute("UPDATE runs SET attachments=NULL WHERE id=?", (rid,))
    assert store.attachments(store.run(c, rid)) == []
    c.execute("UPDATE runs SET attachments='not json' WHERE id=?", (rid,))
    assert store.attachments(store.run(c, rid)) == []


def test_row_without_column_is_empty():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    assert store.attachments(raw.execute("SELECT 1 AS id").fetchone()) == []


def test_old_database_gains_the_column():
    db.connect().close()
    raw = sqlite3.connect(paths.db())
    raw.execute("ALTER TABLE runs DROP COLUMN attachments")
    raw.commit()
    raw.close()
    c = db.connect()
    cols = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
    assert "attachments" in cols


def test_api_ask_passes_attachments(png):
    c = db.connect()
    out = api.ask(c, {"prompt": "what is this", "attachments": [str(png)]})
    assert store.attachments(store.run(c, out["run"])) == [str(png)]
    with pytest.raises(ValueError):
        api.ask(c, {"prompt": "x", "attachments": [str(png) + ".missing"]})
