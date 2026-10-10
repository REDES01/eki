"""The serve kind: eki's static server, one port per served folder, real servers on 127.0.0.1."""
import http.client
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from eki import components, housekeep, workspace
from eki.components import Call, ports, serve, servers, static


@pytest.fixture
def game(proj, home):
    """`proj` as a web project: an index and a module in git, assets/ ignored, a serve and an assets card."""
    (proj / "index.html").write_text("<h1>trunk</h1>\n")
    (proj / "main.mjs").write_text("export const x = 1;\n")
    (proj / ".gitignore").write_text("assets/\n")
    workspace.git(proj, "add", "index.html", "main.mjs", ".gitignore")
    workspace.git(proj, "commit", "-q", "-m", "web")
    (proj / "assets").mkdir()
    (proj / "assets" / "theme.ogg").write_bytes(bytes(range(256)) * 4)
    for name, spec in (("web", {"kind": "serve", "about": "the game", "path": "/chapter1/",
                                "headers": {"Cache-Control": "no-store"}}),
                       ("media", {"kind": "assets", "about": "media", "path": "assets"})):
        (proj / "components" / name).mkdir(parents=True)
        (proj / "components" / name / "component.json").write_text(json.dumps(spec))
    yield proj
    for st in servers.states():                   # never leave a real server behind
        servers.stop_pid(st.get("pid"), Path(st["state"]), st.get("name", ""))


def get(url, path="", method="GET", headers=None):
    u = urlsplit(url)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    try:
        c.request(method, (u.path + path) if not path.startswith("/") else path, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()


def web(folder):
    return components.get("web", folder)


def test_two_worktrees_get_two_ports_and_serve_their_own_files(game):
    a = workspace.add(game, "wa")
    b = workspace.add(game, "wb")
    (a / "index.html").write_text("<h1>A</h1>\n")
    (b / "index.html").write_text("<h1>B</h1>\n")
    ua = serve.ensure(web(a), a, by="r1")
    ub = serve.ensure(web(b), b, by="r2")
    assert ua["up"] and ub["up"], (ua, ub)
    assert ua["url"] != ub["url"] and ua["url"].endswith("/chapter1/")
    assert urlsplit(ua["url"]).port != urlsplit(ub["url"]).port
    code, head, body = get(ua["url"])
    assert code == 200 and body == b"<h1>A</h1>\n"
    assert head["Cache-Control"] == "no-store" and head["Content-Type"].startswith("text/html")
    assert get(ub["url"])[2] == b"<h1>B</h1>\n"
    assert get(ua["url"], "/index.html")[0] == 404                       # outside the prefix
    code, head, _ = get(ua["url"], "/chapter1")
    assert code == 301 and head["Location"] == "/chapter1/"
    assert get(ua["url"], "../app.py")[0] == 404
    assert get(ua["url"], "/chapter1/%2e%2e/app.py")[0] == 404
    assert {x["folder"] for x in serve.links(web(a))} == {ports.norm(a), ports.norm(b)}


def test_assets_link_range_and_types(game):
    a = workspace.add(game, "wa")
    assert (a / "assets").is_symlink()                                   # linked before anything runs
    url = serve.ensure(web(a), a)["url"]
    code, head, body = get(url, "assets/theme.ogg")
    assert code == 200 and len(body) == 1024 and head["Content-Type"] == "audio/ogg"
    assert head["Cache-Control"] == "no-store" and head["Accept-Ranges"] == "bytes"
    code, head, body = get(url, "assets/theme.ogg", headers={"Range": "bytes=10-19"})
    assert code == 206 and body == bytes(range(10, 20)) and head["Content-Range"] == "bytes 10-19/1024"
    code, _, body = get(url, "assets/theme.ogg", headers={"Range": "bytes=-4"})
    assert code == 206 and body == bytes(range(252, 256))
    code, head, _ = get(url, "assets/theme.ogg", headers={"Range": "bytes=5000-"})
    assert code == 416 and head["Content-Range"] == "bytes */1024"
    assert get(url, "main.mjs")[1]["Content-Type"].startswith("text/javascript")
    code, head, body = get(url, "main.mjs", method="HEAD")
    assert code == 200 and body == b"" and head["Content-Length"] == str(len("export const x = 1;\n"))
    assert get(url, "assets/")[0] == 404                                 # no listing
    for name, want in ((".wasm", "application/wasm"), (".glb", "model/gltf-binary"),
                       (".ktx2", "image/ktx2"), (".ogg", "audio/ogg"), (".mjs", "text/javascript")):
        assert static.content_type("x" + name).startswith(want)


def test_same_port_after_a_restart_and_freed_when_the_worktree_goes(game, conn):
    a = workspace.add(game, "wa")
    first = serve.ensure(web(a), a)
    port = ports.get(a)
    assert port and urlsplit(first["url"]).port == port
    assert servers.stop(web(a), ports.norm(a))                         # eki and the server restart
    assert not serve.links(web(a))
    assert ports.get(a) == port
    again = serve.ensure(web(a), a)
    assert again["up"] and again["url"] == first["url"]
    pid = servers.read(web(a), ports.norm(a))["pid"]
    workspace.remove(game, "wa")
    said = housekeep.tick(conn)
    assert any("the folder is gone" in s for s in said) and any(f"freed port {port}" in s for s in said)
    assert not servers.alive(pid) and ports.get(a) is None
    other = game.parent / "other"
    other.mkdir()
    assert ports.allot(other) == port                                    # a freed port is taken again


def test_for_run_and_use(game, tmp_path):
    a = workspace.add(game, "wa")
    (a / "sub").mkdir()
    url = serve.for_run(str(a / "sub"), by="r9")
    assert url == f"http://127.0.0.1:{ports.get(a)}/chapter1/"          # the worktree's top, not sub/
    assert servers.read(web(a), ports.norm(a))["last_run"] == "r9"
    got = serve.call(web(a), Call([], str(a), "caller", "call-1", lambda *x: None, {}))
    assert got.text == url and not got.error
    assert serve.call(web(a), Call(["x"], str(a), None, "c2", lambda *x: None, {})).error
    assert any("→" in n for n in serve.notes(web(a)))
    assert serve.stop(web(a)) and not serve.is_up(web(a))
    bare = tmp_path / "plain"
    bare.mkdir()
    assert serve.for_run(str(bare)) == "" and serve.for_run("") == ""


@pytest.mark.parametrize("spec, bad", [({"path": "chapter1/"}, "path"), ({"path": "/chapter1"}, "path"),
                                       ({"headers": ["no-store"]}, "headers"), ({"index": ""}, "index")])
def test_validate_reports_bad_settings(tmp_path, spec, bad):
    errs = serve.validate({"kind": "serve", "about": "x", **spec}, tmp_path)
    assert errs and bad in errs[0]


def test_template_loads():
    c = components.load(components.templates_dir() / "serve", "pool")
    assert c.kind == "serve" and c.spec["headers"] == {"Cache-Control": "no-store"}
