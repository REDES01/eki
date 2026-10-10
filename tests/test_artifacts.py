"""Artifacts: the files your chats wrote that are still on disk, newest first."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from eki import artifacts, paths, server, store


def wrote(conn, rid, path, t, name="Write"):
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                 (rid, 1, t, "tool", json.dumps({"name": name, "path": str(path)})))


@pytest.fixture
def made(conn, tmp_path):
    """One chat that wrote a.html, b.md, c.pdf, d.svg, and a picture eki drew."""
    folder = tmp_path / "work"
    folder.mkdir()
    tid = store.create_thread(conn, "landing page", str(folder))
    rid = store.create_run(conn, tid, "make me a site", provider="fake")
    files = []
    for i, n in enumerate(["a.html", "b.md", "c.pdf", "d.svg"]):
        f = folder / n
        f.write_text("x")
        wrote(conn, rid, f, 1000.0 + i)
        files.append(str(f))
    drawn = store.create_thread(conn, "a lighthouse", None)
    drun = store.create_run(conn, drawn, "draw a lighthouse", provider="comfyui")
    pic = paths.home() / "images" / drun / "eki_00001_.png"
    pic.parent.mkdir(parents=True)
    pic.write_bytes(b"\x89PNG")
    wrote(conn, drun, pic, 1010.0, name="image")
    return {"folder": folder, "tid": tid, "rid": rid, "files": files, "pic": str(pic)}


def paths_of(got):
    return [i["path"] for i in got["items"]]


def test_all_list_newest_first_with_their_details(conn, made):
    got = artifacts.listing(conn)
    assert paths_of(got) == [made["pic"]] + list(reversed(made["files"])) and not got["more"]
    first = got["items"][1]
    assert first["name"] == "d.svg" and first["kind"] == "picture" and first["ext"] == "svg"
    assert first["thread"] == made["tid"] and first["run"] == made["rid"]
    assert first["thread_title"] == "landing page" and first["prompt"] == "make me a site"
    assert first["provider"] == "fake" and first["created_at"] == 1003.0 and "project" in first


def test_kind_filter(conn, made):
    a, b, c, d = made["files"]
    assert paths_of(artifacts.listing(conn, "picture")) == [made["pic"], d]
    assert paths_of(artifacts.listing(conn, "page")) == [a]
    assert paths_of(artifacts.listing(conn, "document")) == [c, b]
    with pytest.raises(ValueError):
        artifacts.listing(conn, "video")


def test_removed_files_eki_threads_and_other_files_are_left_out(conn, made, tmp_path):

    os.remove(made["files"][0])
    mine = store.create_thread(conn, "eki's own", str(made["folder"]), by="eki")
    rid = store.create_run(conn, mine, "self work", provider="fake")
    other = made["folder"] / "e.html"
    other.write_text("x")
    wrote(conn, rid, other, 2000.0)
    py = made["folder"] / "f.py"
    py.write_text("x")
    wrote(conn, made["rid"], py, 2001.0)
    own = paths.home() / "notes.md"            # eki's own file, not a picture
    own.write_text("x")
    wrote(conn, made["rid"], own, 2002.0)
    got = paths_of(artifacts.listing(conn))
    assert made["files"][0] not in got and str(other) not in got and str(py) not in got
    assert str(own) not in got and len(got) == 4


def test_a_relative_path_resolves_against_the_thread_folder(conn, made):
    (made["folder"] / "docs").mkdir()
    (made["folder"] / "docs" / "plan.md").write_text("x")
    wrote(conn, made["rid"], "docs/plan.md", 3000.0, name="fileChange")
    first = artifacts.listing(conn)["items"][0]
    assert first["path"] == str(made["folder"] / "docs" / "plan.md") and first["kind"] == "document"


def test_each_path_once_at_its_newest_event(conn, made):
    wrote(conn, made["rid"], made["files"][0], 4000.0, name="Edit")
    got = artifacts.listing(conn)["items"]
    assert [i["path"] for i in got].count(made["files"][0]) == 1
    assert got[0]["path"] == made["files"][0] and got[0]["created_at"] == 4000.0
    # paging past the newest event doesn't bring it back
    rest = artifacts.listing(conn, before=got[0]["created_at"])
    assert made["files"][0] not in paths_of(rest)


def test_q_matches_name_title_or_prompt(conn, made):
    assert paths_of(artifacts.listing(conn, q="B.MD")) == [made["files"][1]]
    assert paths_of(artifacts.listing(conn, q="lighthouse")) == [made["pic"]]
    assert len(artifacts.listing(conn, q="site")["items"]) == 4
    assert artifacts.listing(conn, q="nothing like it")["items"] == []


def test_before_pages_and_more(conn, made):
    page = artifacts.listing(conn, limit=2)
    assert len(page["items"]) == 2 and page["more"]
    rest = artifacts.listing(conn, limit=2, before=page["items"][-1]["created_at"])
    assert paths_of(rest) == list(reversed(made["files"]))[1:3] and rest["more"]
    last = artifacts.listing(conn, limit=2, before=rest["items"][-1]["created_at"])
    assert paths_of(last) == [made["files"][0]] and not last["more"]


def test_get_api_artifacts_through_the_server(conn, made):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/artifacts?kind=document&limit=1", timeout=10) as r:
            got = json.loads(r.read())
        with urllib.request.urlopen(base + "/api/artifacts?limit=0&before=1003", timeout=10) as r:
            one = json.loads(r.read())
        try:
            urllib.request.urlopen(base + "/api/artifacts?kind=video", timeout=10)
            bad = 200
        except urllib.error.HTTPError as e:
            bad = e.code
    finally:
        srv.shutdown()
    assert paths_of(got) == [made["files"][2]] and got["more"]
    assert paths_of(one) == [made["files"][2]] and one["more"]      # limit clamps to 1
    assert bad == 400
