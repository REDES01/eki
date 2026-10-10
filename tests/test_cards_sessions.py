"""An agent card's sessions: listing, detail, and files served only from inside the session folder."""
import base64
import json
import os
import shutil

import pytest

from eki import apiroutes, components
from eki.components import journal

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder)))
    conn.commit()


def get(conn, path, q=None):
    fn, m = apiroutes.match("GET", path)
    return fn(conn, m, q or {}, {})


def session(name, sid, texts, calls=None, url=""):
    d = components.sessions_dir() / name / sid
    journal.keep(d, texts, calls, url)
    return d


@pytest.fixture
def agent(conn, proj):
    project(conn, proj)
    shutil.copytree(components.templates_dir() / "agent", proj / "components" / "helper")
    old = session("helper", "20261001-100000-aaaa", ["NOTE: slow start\nFEEL bored: nothing moves\nREVIEW: fine\nmore"])
    new = session("helper", "20261002-100000-bbbb", ["FEEL lost: where\nREVIEW: confusing"],
                  calls=[{"name": "browser_navigate", "url": "https://evil.example/"}], url="http://localhost:3000/")
    return old, new


def test_listing_newest_first(conn, agent):
    got = get(conn, "/api/projects/p1/cards/helper/sessions")["sessions"]
    assert [s["id"] for s in got] == ["20261002-100000-bbbb", "20261001-100000-aaaa"]
    assert got[0]["review"] == "confusing" and got[0]["tainted"] is True
    assert got[1]["review"] == "fine" and got[1]["tainted"] is False
    assert got[0]["when"] - got[1]["when"] == 86400
    json.dumps(got)


def test_not_an_agent_has_none(conn, proj):
    project(conn, proj)
    shutil.copytree(components.templates_dir() / "voice", proj / "components" / "voice")
    assert get(conn, "/api/projects/p1/cards/voice/sessions") == {"sessions": []}
    with pytest.raises(KeyError):
        get(conn, "/api/projects/p1/cards/nope/sessions")


def test_detail_with_notes_emotions_screenshots(conn, agent):
    old, new = agent
    (old / "screenshots").mkdir()
    (old / "screenshots" / "b.png").write_bytes(PNG)
    (old / "screenshots" / "a.png").write_bytes(PNG)
    got = get(conn, "/api/projects/p1/cards/helper/sessions/20261001-100000-aaaa")
    assert got["id"] == "20261001-100000-aaaa" and got["review"] == "fine\nmore"
    assert got["notes"] == ["slow start"]
    assert [(e["emotion"], e["why"]) for e in got["emotions"]] == [("bored", "nothing moves")]
    assert got["emotions"][0]["t"]
    assert got["tainted"] is False and got["taint"] == ""
    assert got["screenshots"] == ["a.png", "b.png"]
    bad = get(conn, "/api/projects/p1/cards/helper/sessions/20261002-100000-bbbb")
    assert bad["tainted"] and bad["taint"] == "opened https://evil.example/" and bad["screenshots"] == []
    with pytest.raises(KeyError):
        get(conn, "/api/projects/p1/cards/helper/sessions/20261003-100000-cccc")


def test_files_as_data_url_or_text(conn, agent):
    old, _ = agent
    (old / "screenshots").mkdir()
    (old / "screenshots" / "a.png").write_bytes(PNG)
    url = "/api/projects/p1/cards/helper/sessions/20261001-100000-aaaa/file"
    for name in ("screenshots/a.png", "a.png"):
        got = get(conn, url, {"name": name})
        assert got["type"] == "image/png"
        assert got["data_url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    t = get(conn, url, {"name": "notes.md"})
    assert t["type"] == "text/markdown" and t["text"] == "- slow start\n"


def test_nothing_outside_the_session_folder(conn, agent, tmp_path):
    old, new = agent
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    os.symlink(secret, old / "link.txt")
    (old / "screenshots").mkdir()
    os.symlink(new / "review.md", old / "screenshots" / "next.md")  # another session is outside too
    url = "/api/projects/p1/cards/helper/sessions/20261001-100000-aaaa/file"
    for name in ("../20261002-100000-bbbb/review.md", "../../../secret.txt", str(secret), "link.txt",
                 "screenshots/next.md", "", "screenshots"):
        with pytest.raises(KeyError):
            get(conn, url, {"name": name})
    for sid in ("..", "...", ".hidden", "a..b/..", "%2e%2e"):
        m = apiroutes.match("GET", f"/api/projects/p1/cards/helper/sessions/{sid}/file")
        if m is None:
            continue
        with pytest.raises(KeyError):
            m[0](conn, m[1], {"name": "review.md"}, {})
    from eki import api_cardsessions
    with pytest.raises(KeyError):
        api_cardsessions._session(conn, "p1", "helper", "../helper/20261001-100000-aaaa")
