"""The passive card kinds a project's agents share: knowledge (files every agent reads) and
mcp (an MCP server only the project's runs get)."""
import json
import shutil
import threading
import urllib.request

import pytest

from eki import components, mcp, server, store, worker
from eki.components import Call, knowledge, mcpserver


def put(proj, name, spec):
    d = proj / "components" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps(spec))
    return d


def know(**kw):
    return {"kind": "knowledge", "about": "decisions", "files": ["design/DECISIONS.md"], "read": "always", **kw}


def server_spec(**kw):
    return {"kind": "mcp", "about": "the game's own tools",
            "server": {"command": "node", "args": ["srv.js"], "env": {"API_KEY": "sk-secret-99"}}, **kw}


def test_both_templates_load(tmp_path):
    for kind in ("knowledge", "mcp"):
        d = tmp_path / kind
        shutil.copytree(components.templates_dir() / kind, d)
        comp = components.load(d, "project")
        assert comp.kind == kind
        assert "mcp_servers" not in comp.spec
    assert components.load(tmp_path / "knowledge", "project").spec["read"] == "always"


@pytest.mark.parametrize("spec,field", [
    (know(files=[]), "files"),
    (know(files="x.md"), "files"),
    (know(files=["/etc/passwd"]), "files"),
    (know(files=["../out.md"]), "files"),
    (know(read="sometimes"), "read"),
    ({"kind": "knowledge", "about": "x", "files": ["a.md"]}, "read"),
    (server_spec(server="node"), "server"),
    (server_spec(server={"args": []}), "server"),
    (server_spec(server={"command": "x", "env": {"A": 1}}), "server"),
    (server_spec(server={"url": "ftp://x"}), "server"),
])
def test_bad_specs_name_their_field(tmp_path, spec, field):
    d = tmp_path / "c"
    d.mkdir()
    (d / "component.json").write_text(json.dumps(spec))
    with pytest.raises(ValueError) as e:
        components.load(d, "project")
    assert str(e.value).startswith(field), str(e.value)


def test_url_server_is_fine(proj):
    put(proj, "remote", server_spec(server={"url": "https://mcp.example.com/sse"}))
    assert components.find(proj)[0]["remote"].kind == "mcp"


def test_brief_leaves_passive_cards_out_and_calling_them_fails(proj):
    put(proj, "decisions", know())
    put(proj, "tools", server_spec())
    assert components.brief(proj) == ""
    put(proj, "echo", {"kind": "service", "about": "says back", "run": "echo"})
    line = components.brief(proj)
    assert "echo" in line and "decisions" not in line and "tools" not in line
    found, _ = components.find(proj)
    c = Call(args=[], caller_dir=str(proj), caller_run=None, run_id="r", emit=lambda *a: None, extra={})
    assert components.kind_module("knowledge").call(found["decisions"], c).error == \
        "decisions is a knowledge card: it is not called with eki use"
    assert components.kind_module("mcp").call(found["tools"], c).error == \
        "tools is a mcp card: it is not called with eki use"


def test_files_lists_files_folders_globs_and_missing(proj):
    (proj / "design" / "chars").mkdir(parents=True)
    (proj / "design" / "DECISIONS.md").write_text("# decisions\n")
    (proj / "design" / "chars" / "ann.md").write_text("ann")
    (proj / "design" / "chars" / "bo.md").write_text("bo!")
    (proj / "design" / "chars" / ".hidden").write_text("no")
    (proj / "notes").mkdir()
    (proj / "notes" / "a.txt").write_text("a")
    (proj / "notes" / "b.txt").write_text("b")
    put(proj, "lore", know(files=["design/DECISIONS.md", "design/chars", "notes/*.txt", "gone.md"]))
    put(proj, "rules", know(files=["design/DECISIONS.md"], read="when-relevant"))
    found, errors = components.find(proj)
    assert errors == [] and {"lore", "rules"} <= set(found)        # many knowledge cards in one project
    comp = found["lore"]
    assert knowledge.root_of(comp) == proj
    got = knowledge.files(comp)
    assert [f["path"] for f in got] == ["design/DECISIONS.md", "design/chars/ann.md", "design/chars/bo.md",
                                        "notes/a.txt", "notes/b.txt", "gone.md"]
    first = got[0]
    assert first["name"] == "DECISIONS.md" and first["size"] == len("# decisions\n")
    assert first["changed"] and first["author"] == "" and not first["missing"]
    assert first["abs"] == str((proj / "design" / "DECISIONS.md").resolve())
    assert got[-1]["missing"] and got[-1]["size"] is None and got[-1]["pattern"] == "gone.md"
    assert knowledge.notes(comp) == ["5 files, 1 missing: gone.md"]
    assert not knowledge.is_up(comp) and not knowledge.stop(comp)


def test_files_refuses_dotdot_and_links_out(proj, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("no")
    (proj / "out.md").symlink_to(outside)
    d = put(proj, "k", know(files=["out.md"]))
    comp = components.find(proj)[0]["k"]
    assert knowledge.files(comp)[0]["missing"]
    comp.spec["files"] = ["../secret.md"]                 # past validate: still nothing outside
    assert knowledge.files(comp)[0]["missing"]
    assert d.is_dir()


def test_public_masks_env():
    spec = server_spec()
    out = mcpserver.public(spec)
    assert out["server"]["env"]["API_KEY"] == mcp.mask("sk-secret-99") != "sk-secret-99"
    assert spec["server"]["env"]["API_KEY"] == "sk-secret-99"            # the card itself is untouched
    assert "sk-secret" not in json.dumps(out)
    assert mcpserver.public({"kind": "mcp", "server": {"url": "https://x"}})["server"] == {"url": "https://x"}


def test_build_turn_with_no_cards_keeps_the_registry(home, conn, proj):
    mcp.add("fs", command="fs-server", args=["/"])
    tid = store.create_thread(conn, "t", str(proj))
    rid = store.create_run(conn, tid, "hi", row="general")
    turn = worker.build_turn(conn, store.run(conn, rid), "claude")
    assert turn.extra["claude_mcp"] == mcp.claude_config()
    assert turn.extra["codex_config"] == mcp.codex_config()
    assert turn.extra["claude_mcp"] and turn.extra["codex_config"]


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_the_kind_parts_are_served_and_hooked(web):
    for f, kind, title in (("cardknowledge.js", "knowledge", "Files"), ("cardmcp.js", "mcp", "Server")):
        with urllib.request.urlopen(web + "/ui/" + f, timeout=10) as r:
            body = r.read().decode()
        assert r.status == 200 and f'projectCardKinds["{kind}"]' in body and title in body
    with urllib.request.urlopen(web + "/ui/projectcardpanel.js", timeout=10) as r:
        panel = r.read().decode()
    assert "/ui/" in panel and "cardknowledge.js" in panel and "cardmcp.js" in panel and "pp-kind" in panel
