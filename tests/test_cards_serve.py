"""The project page's cards show a serve card's links that are up and an assets card's pool size."""
import json

from eki import apiroutes
from eki.components import serve


def put(base, name, spec):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps(spec))
    return d


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder)))
    conn.commit()


def cards(conn):
    fn, m = apiroutes.match("GET", "/api/projects/p1/cards")
    return {c["name"]: c for c in fn(conn, m, {}, {})["cards"]}


def test_serve_card_lists_its_links(conn, proj, monkeypatch):
    project(conn, proj)
    put(proj / "components", "site", {"kind": "serve", "about": "the game", "path": "/chapter1/"})
    link = {"folder": str(proj), "url": "http://127.0.0.1:47213/chapter1/"}
    monkeypatch.setattr(serve, "links", lambda comp: [link])
    got = cards(conn)["site"]
    assert got["links"] == [link] and got["size"] == ""


def test_serve_links_that_raise_show_none(conn, proj, monkeypatch):
    project(conn, proj)
    put(proj / "components", "site", {"kind": "serve", "about": "the game", "path": "/chapter1/"})

    def boom(comp):
        raise RuntimeError("no")
    monkeypatch.setattr(serve, "links", boom)
    assert cards(conn)["site"]["links"] == []


def test_assets_card_shows_its_pool_size(conn, proj):
    project(conn, proj)
    put(proj / "components", "media", {"kind": "assets", "about": "media", "path": "assets"})
    (proj / "assets").mkdir()
    (proj / "assets" / "a.bin").write_bytes(b"12345")
    got = cards(conn)["media"]
    assert got["size"] == "5 B" and got["links"] == []


def test_missing_pool_and_other_kinds_show_nothing(conn, proj):
    project(conn, proj)
    put(proj / "components", "media", {"kind": "assets", "about": "media", "path": "assets"})
    put(proj / "components", "voice", {"kind": "service", "about": "speaks", "run": "echo hi"})
    got = cards(conn)
    assert got["media"]["size"] == ""
    assert got["voice"]["links"] == [] and got["voice"]["size"] == ""
