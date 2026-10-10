"""A knowledge card on the project page: its files listed, one read and written over the API,
nothing off the card or outside the project reachable, and its web part served."""
import json
import os
import threading
import urllib.request

import pytest

from eki import apiroutes, components, server


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder) if folder else None))
    conn.commit()


def call(conn, method, path, body=None, q=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, q or {}, body or {})


def knowledge_card(base, name, files, read="always"):
    (base / name).mkdir(parents=True)
    (base / name / "component.json").write_text(json.dumps(
        {"kind": "knowledge", "about": "decisions", "files": files, "read": read}))


@pytest.fixture
def cards(conn, proj):
    project(conn, proj)
    (proj / "design").mkdir()
    (proj / "design" / "DECISIONS.md").write_text("# Decisions\n- keep it small\n")
    (proj / "notes").mkdir()
    (proj / "notes" / "a.md").write_text("a\n")
    (proj / "notes" / "b.md").write_text("b\n")
    base = proj / "components"
    knowledge_card(base, "decisions", ["design/DECISIONS.md", "design/GONE.md"])
    knowledge_card(base, "notes", ["notes/*.md"], read="when-relevant")
    return proj


URL = "/api/projects/p1/cards/decisions"


def test_routes_are_not_shadowed_by_card_edit():
    assert apiroutes.match("GET", URL + "/files")[0].__module__ == "eki.api_cardknowledge"
    assert apiroutes.match("GET", URL + "/file")[0].__module__ == "eki.api_cardknowledge"
    assert apiroutes.match("POST", URL + "/file")[0].__module__ == "eki.api_cardknowledge"
    assert apiroutes.match("POST", URL)[0].__module__ == "eki.api_cardedit"


def test_files_lists_real_and_missing(conn, cards):
    got = call(conn, "GET", URL + "/files")["files"]
    by = {f["path"]: f for f in got}
    assert set(by) == {"design/DECISIONS.md", "design/GONE.md"}
    real, gone = by["design/DECISIONS.md"], by["design/GONE.md"]
    assert not real["missing"] and real["size"] == len("# Decisions\n- keep it small\n") and real["changed"]
    assert real["name"] == "DECISIONS.md" and set(real) >= {"pattern", "abs", "author"}
    assert gone["missing"] and gone["size"] is None
    globbed = [f["path"] for f in call(conn, "GET", "/api/projects/p1/cards/notes/files")["files"]]
    assert globbed == ["notes/a.md", "notes/b.md"]


def test_read_and_write_a_listed_file(conn, cards):
    got = call(conn, "GET", URL + "/file", q={"path": "design/DECISIONS.md"})
    assert got["text"].startswith("# Decisions") and got["path"] == "design/DECISIONS.md"
    assert set(got) == {"path", "text", "size", "changed", "author"}
    out = call(conn, "POST", URL + "/file", {"path": "design/DECISIONS.md", "text": "# New\n"})
    assert (cards / "design" / "DECISIONS.md").read_text() == "# New\n"
    assert out["path"] == "design/DECISIONS.md" and out["size"] == 6 and not out["missing"]
    assert not list((cards / "design").glob(".*.tmp"))
    # a missing file the card names is written into place
    out = call(conn, "POST", URL + "/file", {"path": "design/GONE.md", "text": "back\n"})
    assert (cards / "design" / "GONE.md").read_text() == "back\n" and not out["missing"]
    # a file matched by a glob
    call(conn, "POST", "/api/projects/p1/cards/notes/file", {"path": "notes/b.md", "text": "bb\n"})
    assert (cards / "notes" / "b.md").read_text() == "bb\n"


def test_paths_off_the_card_or_outside_are_refused(conn, cards, tmp_path):
    for bad in ("app.py", "../outside.md", "design/../app.py", "/etc/passwd", ""):
        with pytest.raises(ValueError):
            call(conn, "GET", URL + "/file", q={"path": bad})
        with pytest.raises(ValueError):
            call(conn, "POST", URL + "/file", {"path": bad, "text": "x"})
    assert (cards / "app.py").read_text() == "print('hi')\n"
    secret = tmp_path / "secret.md"
    secret.write_text("secret\n")
    (cards / "design" / "LINK.md").symlink_to(secret)
    knowledge_card(cards / "components", "linked", ["design/LINK.md"])
    with pytest.raises(ValueError, match="outside the project"):
        call(conn, "GET", "/api/projects/p1/cards/linked/file", q={"path": "design/LINK.md"})
    with pytest.raises(ValueError, match="outside the project"):
        call(conn, "POST", "/api/projects/p1/cards/linked/file", {"path": "design/LINK.md", "text": "x"})
    assert secret.read_text() == "secret\n"


def test_not_text_and_wrong_kind_and_unknown(conn, cards):
    (cards / "design" / "DECISIONS.md").write_bytes(b"\xff\xfe\x00bin")
    with pytest.raises(ValueError, match="not UTF-8 text"):
        call(conn, "GET", URL + "/file", q={"path": "design/DECISIONS.md"})
    with pytest.raises(ValueError, match="not UTF-8 text"):
        call(conn, "POST", URL + "/file", {"path": "design/DECISIONS.md", "text": "x"})
    with pytest.raises(ValueError, match="text must be text"):
        call(conn, "POST", URL + "/file", {"path": "design/GONE.md", "text": 3})
    (cards / "components" / "svc").mkdir()
    (cards / "components" / "svc" / "component.json").write_text(
        json.dumps({"kind": "service", "about": "x", "run": "echo"}))
    with pytest.raises(ValueError, match="not a knowledge card"):
        call(conn, "GET", "/api/projects/p1/cards/svc/files")
    with pytest.raises(KeyError):
        call(conn, "GET", "/api/projects/p1/cards/nope/files")


def test_a_pool_reference_card_reads_but_cant_write(conn, cards):
    pool = components.pool_dir() / "rules"
    pool.mkdir(parents=True)
    (pool / "component.json").write_text(json.dumps(
        {"kind": "knowledge", "about": "rules", "files": ["design/DECISIONS.md"], "read": "always"}))
    (cards / "components" / "rules").mkdir()
    (cards / "components" / "rules" / "component.json").write_text('{"pool": "rules"}')
    got = call(conn, "GET", "/api/projects/p1/cards/rules/file", q={"path": "design/DECISIONS.md"})
    assert got["text"].startswith("# Decisions")
    with pytest.raises(ValueError, match="edit it in the pool"):
        call(conn, "POST", "/api/projects/p1/cards/rules/file", {"path": "design/DECISIONS.md", "text": "x"})
    assert (cards / "design" / "DECISIONS.md").read_text().startswith("# Decisions")


def test_a_project_without_folder_cant_write(conn):
    project(conn, None)
    with pytest.raises(ValueError, match="pick or clone a folder first"):
        call(conn, "POST", URL + "/file", {"path": "design/DECISIONS.md", "text": "x"})


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_the_web_part_is_served_and_names_its_routes(web):
    with urllib.request.urlopen(web + "/ui/cardknowledge.js", timeout=10) as r:
        js = r.read().decode()
    assert "projectCardKinds" in js and '"/files"' in js and '"/file"' in js and "/file?path=" in js
    assert "md.render" in js and "/ui/cardknowledge.css" in js
    with urllib.request.urlopen(web + "/ui/cardknowledge.css", timeout=10) as r:
        assert r.status == 200
    here = os.path.dirname(__file__)
    for f in ("cardknowledge.js", "cardknowledge.css"):
        with open(os.path.join(here, "..", "eki", "web", f)) as fh:
            assert len(fh.read().splitlines()) < 400
