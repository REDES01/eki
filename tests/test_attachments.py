"""Pictures sent with a request: stored on the run as absolute paths."""
import os
import sqlite3
import urllib.parse

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


# ---- intake and upload -----------------------------------------------------------------

import json
import shutil
import threading
import urllib.error
import urllib.request

from eki import attachments, files, server


def test_save_writes_under_home():
    path = attachments.save(b"\x89PNG\r\n\x1a\n", "Shot.PNG")
    assert path.startswith(str(paths.home() / "attachments"))
    assert path.endswith(".png")
    assert open(path, "rb").read() == b"\x89PNG\r\n\x1a\n"


def test_save_refuses_non_picture_and_big():
    with pytest.raises(ValueError):
        attachments.save(b"MZ", "setup.exe")
    with pytest.raises(ValueError):
        attachments.save(b"", "noext")
    with pytest.raises(ValueError):
        attachments.save(b"0" * (20 * 1024 * 1024 + 1), "big.png")


def test_intake_passes_small_and_non_pictures(png, tmp_path):
    assert attachments.intake(str(png)) == str(png)
    doc = tmp_path / "notes.pdf"
    doc.write_bytes(b"%PDF")
    assert attachments.intake(str(doc)) == str(doc)
    c = db.connect()
    _, rid = asking.submit(c, "read this", attachments=[str(doc)])
    assert store.attachments(store.run(c, rid)) == [str(doc)]


def test_intake_without_sips_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    heic = tmp_path / "p.heic"
    heic.write_bytes(b"x")
    assert attachments.intake(str(heic)) == str(heic)


@pytest.mark.skipif(not shutil.which("sips"), reason="needs macOS sips")
def test_intake_converts_heic(tmp_path):
    import subprocess
    tiny = tmp_path / "t.png"
    tiny.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"))
    heic = tmp_path / "t.heic"
    subprocess.run(["sips", "-s", "format", "heic", str(tiny), "--out", str(heic)], capture_output=True)
    if not heic.is_file():
        pytest.skip("sips can't write HEIC here")
    before = heic.read_bytes()
    out = attachments.intake(str(heic))
    assert out.endswith(".jpg") and out.startswith(str(paths.home() / "attachments"))
    assert heic.read_bytes() == before


@pytest.fixture
def base():
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_upload_through_server(base):
    url = base + "/api/attachments"
    code, _ = _post(url, b"GIF89a", {"X-Filename": "a.gif"})
    assert code == 403
    code, out = _post(url, b"GIF89a", {"X-Eki": "1", "X-Filename": "a.gif"})
    assert code == 200 and os.path.isfile(out["path"])
    code, out = _post(url, b"MZ", {"X-Eki": "1", "X-Filename": "a.exe"})
    assert code == 400 and "error" in out


def test_file_serves_attached_picture_only(base, png, tmp_path):
    c = db.connect()
    tid, _ = asking.submit(c, "what is this", attachments=[str(png)])
    assert files.known(c, str(png))
    with urllib.request.urlopen(base + "/api/file?path=" + urllib.parse.quote(str(png)), timeout=10) as r:
        assert r.read() == png.read_bytes()
    other = tmp_path / "other.png"
    other.write_bytes(b"x")
    assert not files.known(c, str(other))
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base + "/api/file?path=" + urllib.parse.quote(str(other)), timeout=10)
    c.execute("UPDATE runs SET attachments='not json'")
    assert not files.known(c, str(png))


def test_thread_view_carries_attachments(png):
    c = db.connect()
    tid, _ = asking.submit(c, "what is this", attachments=[str(png)])
    view = api.thread(c, tid)
    assert view["runs"][0]["attachments"] == [str(png)]
    json.dumps(view)
