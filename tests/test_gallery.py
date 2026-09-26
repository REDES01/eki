import json
import threading
import urllib.request

import pytest

from eki import api, gallery, paths, server, store
from eki.cli import main


def draw(conn, title, prompt, t, row="image"):
    """A run in its own thread that drew one picture at time `t`."""
    tid = store.create_thread(conn, title, None)
    rid = store.create_run(conn, tid, prompt, provider="comfyui", row=row)
    folder = paths.home() / "images" / rid
    folder.mkdir(parents=True)
    pic = folder / "eki_00001_.png"
    pic.write_bytes(b"\x89PNG fake")
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                 (rid, 1, t, "tool", json.dumps({"name": "image", "detail": "Drew 1 picture",
                                                 "path": str(pic)})))
    return tid, rid, pic


@pytest.fixture
def three(conn):
    return [draw(conn, f"thread {i}", f"draw a lighthouse number {i}", 1000.0 + i) for i in range(3)]


def test_newest_first_with_thread_and_prompt(conn, three):
    got = gallery.pictures(conn)
    assert [p["path"] for p in got] == [str(pic) for _, _, pic in reversed(three)]
    first = got[0]
    tid, rid, _ = three[2]
    assert first["thread"] == tid and first["run"] == rid and first["thread_title"] == "thread 2"
    assert first["prompt"] == "draw a lighthouse number 2" and first["row"] == "image"
    assert first["provider"] == "comfyui" and first["created_at"] == 1002.0


def test_other_tool_events_are_not_pictures(conn, three):
    store.add_event(conn, three[0][1], 1, "tool", {"name": "bash", "path": str(three[0][2])})
    assert len(gallery.pictures(conn)) == 3


def test_a_deleted_file_is_skipped_but_the_limit_still_fills(conn, three):
    three[2][2].unlink()
    got = gallery.pictures(conn, limit=2)
    assert [p["path"] for p in got] == [str(three[1][2]), str(three[0][2])]


def test_before_pages(conn, three):
    page = api.pictures(conn, limit=2)
    assert len(page["pictures"]) == 2 and page["more"]
    rest = api.pictures(conn, limit=2, before=page["pictures"][-1]["created_at"])
    assert [p["path"] for p in rest["pictures"]] == [str(three[0][2])] and not rest["more"]


def test_api_pictures_through_the_server(conn, three):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/pictures?limit=1&before=1002", timeout=10) as r:
            got = json.loads(r.read())
    finally:
        srv.shutdown()
    assert [p["path"] for p in got["pictures"]] == [str(three[1][2])] and got["more"]


def test_eki_pictures_prints_the_paths(conn, three, capsys):
    assert main(["pictures", "--limit", "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert out[0].startswith(str(three[2][2])) and "image" in out[0] and "lighthouse number 2" in out[0]


def test_eki_pictures_with_none(conn, capsys):
    assert main(["pictures"]) == 0
    assert "no pictures yet" in capsys.readouterr().out
