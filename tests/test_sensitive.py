"""Sensitive picture marks: kept in the store, shown as a flag, never synced."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from eki import artifacts, gallery, paths, sensitive, server, skillout, store, syncstate


def picture(conn, title="a lighthouse", t=1000.0, name="eki_00001_.png"):
    """A run eki drew one picture in, as gallery and artifacts read it."""
    tid = store.create_thread(conn, title, None)
    rid = store.create_run(conn, tid, "draw " + title, provider="comfyui", row="image")
    pic = paths.home() / "images" / rid / name
    pic.parent.mkdir(parents=True)
    pic.write_bytes(b"\x89PNG fake")
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                 (rid, 1, t, "tool", json.dumps({"name": "image", "path": str(pic)})))
    return str(pic)


def test_mark_and_unmark(conn, tmp_path):
    p = str(tmp_path / "a" / ".." / "x.png")
    assert not sensitive.seen(conn, p) and not sensitive.is_sensitive(conn, p)
    assert sensitive.mark(conn, p, True)
    assert sensitive.is_sensitive(conn, str(tmp_path / "x.png"))
    assert sensitive.paths(conn) == {os.path.normpath(str(tmp_path / "x.png"))}
    assert sensitive.mark(conn, p, False)
    assert sensitive.seen(conn, p) and not sensitive.is_sensitive(conn, p)
    assert sensitive.paths(conn) == set()
    with pytest.raises(ValueError):
        sensitive.mark(conn, p, True, by="robot")


def test_an_auto_mark_never_overrides_a_person(conn, tmp_path):
    p = str(tmp_path / "x.png")
    assert sensitive.mark(conn, p, True, by="auto", score=0.9)
    assert sensitive.mark(conn, p, False)                      # the person says it's fine
    assert not sensitive.mark(conn, p, True, by="auto", score=0.95)
    assert not sensitive.is_sensitive(conn, p)
    row = conn.execute("SELECT * FROM sensitive").fetchone()
    assert row["by"] == "person" and row["state"] == "off"


def test_the_mark_survives_a_new_connection(conn, tmp_path):
    from eki import db
    sensitive.mark(conn, str(tmp_path / "x.png"), True)
    assert sensitive.is_sensitive(db.connect(), str(tmp_path / "x.png"))


def test_api_round_trip_and_refusal(conn, tmp_path):
    pic = picture(conn)
    page = tmp_path / "a.html"
    page.write_text("x")
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body):
        req = urllib.request.Request(base + "/api/sensitive", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Eki": "1"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    try:
        assert post({"path": pic, "sensitive": True}) == {"path": pic, "sensitive": True}
        with urllib.request.urlopen(base + "/api/sensitive", timeout=10) as r:
            assert json.load(r) == {"paths": [pic]}
        assert conn.execute("SELECT by FROM sensitive").fetchone()["by"] == "person"
        assert post({"path": pic, "sensitive": False}) == {"path": pic, "sensitive": False}
        with urllib.request.urlopen(base + "/api/sensitive", timeout=10) as r:
            assert json.load(r) == {"paths": []}
        for bad in ({"path": str(page), "sensitive": True},
                    {"path": str(tmp_path / "gone.png"), "sensitive": True},
                    {"path": pic, "sensitive": "yes"}):
            with pytest.raises(urllib.error.HTTPError) as e:
                post(bad)
            assert e.value.code == 400
    finally:
        srv.shutdown()


def test_artifacts_flag_and_hide_sensitive(conn):
    a = picture(conn, "one", 1000.0)
    b = picture(conn, "two", 1001.0)
    sensitive.mark(conn, a, True)
    got = artifacts.listing(conn)["items"]
    assert [(i["path"], i["sensitive"]) for i in got] == [(b, False), (a, True)]
    assert [i["path"] for i in artifacts.listing(conn, hide_sensitive=True)["items"]] == [b]
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/artifacts?hide_sensitive=1", timeout=10) as r:
            assert [i["path"] for i in json.load(r)["items"]] == [b]
        with urllib.request.urlopen(base + "/api/artifacts?hide_sensitive=0", timeout=10) as r:
            assert len(json.load(r)["items"]) == 2
    finally:
        srv.shutdown()


def test_gallery_flag(conn):
    a = picture(conn, "one", 1000.0)
    b = picture(conn, "two", 1001.0)
    sensitive.mark(conn, b, True, by="auto", score=0.8)
    assert [(p["path"], p["sensitive"]) for p in gallery.pictures(conn)] == [(b, True), (a, False)]


def test_skillout_leaves_a_sensitive_picture_out(home, conn):
    d = paths.skills() / "drawn"
    (d / "art").mkdir(parents=True)
    (d / "SKILL.md").write_text("# drawn\n")
    (d / "art" / "ok.png").write_bytes(b"ok")
    (d / "art" / "no.png").write_bytes(b"no")
    paths.config("skill-sources").write_text(json.dumps({"drawn": "learned"}))
    sensitive.mark(conn, str(d / "art" / "no.png"), True)

    said = skillout.tick(conn)
    out = syncstate.outbox() / "skills" / "drawn"
    assert (out / "art" / "ok.png").is_file() and not (out / "art" / "no.png").exists()
    assert any("sensitive" in line and "art/no.png" in line for line in said)
    assert skillout.tick(conn) == []                          # nothing new: not rewritten every pass

    sensitive.mark(conn, str(d / "art" / "no.png"), False)
    assert len(skillout.tick(conn)) == 1
    assert (out / "art" / "no.png").is_file()
