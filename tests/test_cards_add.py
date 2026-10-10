"""Cards: add from a template under a free name, attach a pool card, remove to the Trash."""
import json

import pytest

from eki import apiroutes, components, paths, workspace
from eki.components import cards


def svc(about="does a thing"):
    return {"kind": "service", "about": about, "run": "echo hi"}


def put(base, name, spec):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps(spec))
    return d


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder or f"/nowhere/{pid}"), "trunk", 0, str(folder) if folder else None))
    conn.commit()


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def test_add_picks_a_free_name_and_never_overwrites(tmp_path):
    base = tmp_path / "components"
    put(base, "agent-2", {"mine": True})
    a, _ = cards.add_template(base, "agent")
    b, _ = cards.add_template(base, "agent")
    c, _ = cards.add_template(base, "agent")
    assert [a.name, b.name, c.name] == ["agent", "agent-3", "agent-4"]
    assert json.loads((base / "agent-2" / "component.json").read_text()) == {"mine": True}
    assert components.load(a, "project").kind == "agent"
    with pytest.raises(ValueError, match="already exists"):
        cards.add_template(base, "voice", name="agent")
    with pytest.raises(ValueError, match="no template"):
        cards.add_template(base, "nope")
    with pytest.raises(ValueError, match="bad name"):
        cards.add_template(base, "voice", name="Bad Name")
    assert sorted(p.name for p in base.iterdir()) == ["agent", "agent-2", "agent-3", "agent-4"]


def test_add_three_in_a_row_names_them_agent_2_3(tmp_path):
    base = tmp_path / "components"
    names = [cards.add_template(base, "agent")[0].name for _ in range(3)]
    assert names == ["agent", "agent-2", "agent-3"]


def test_attach_writes_exactly_the_reference(tmp_path):
    put(components.pool_dir(), "images", svc("pool images"))
    put(components.pool_dir(), "broken", {"kind": "nope"})
    base = tmp_path / "components"
    out = cards.attach(base, "images")
    assert json.loads((out / "component.json").read_text()) == {"pool": "images"}
    with pytest.raises(ValueError, match="already exists"):
        cards.attach(base, "images")
    with pytest.raises(ValueError, match="no component"):
        cards.attach(base, "missing")
    with pytest.raises(ValueError, match="broken"):
        cards.attach(base, "broken")
    assert sorted(p.name for p in base.iterdir()) == ["images"]


def test_remove_moves_to_the_trash_and_never_the_pool(tmp_path):
    pool = put(components.pool_dir(), "images", svc())
    base = tmp_path / "components"
    cards.attach(base, "images")
    own = put(base, "voice", svc())
    (paths.user_home() / ".Trash").mkdir()
    (paths.user_home() / ".Trash" / "voice").mkdir()          # a name taken in the Trash
    where = cards.remove(base, "voice")
    assert not own.exists() and where.parent == paths.user_home() / ".Trash" and where.name != "voice"
    assert json.loads((where / "component.json").read_text())["run"] == "echo hi"
    ref = cards.remove(base, "images")
    assert not (base / "images").exists() and (pool / "component.json").is_file()
    assert json.loads((ref / "component.json").read_text()) == {"pool": "images"}
    with pytest.raises(KeyError):
        cards.remove(base, "voice")
    with pytest.raises(KeyError):
        cards.remove(base, "..")


def test_api_add_attach_and_remove(conn, proj):
    project(conn, proj)
    put(components.pool_dir(), "images", svc("pool images"))
    got = call(conn, "POST", "/api/projects/p1/cards", {"template": "voice"})
    assert got["card"]["name"] == "voice" and got["card"]["scope"] == "project"
    again = call(conn, "POST", "/api/projects/p1/cards", {"template": "voice"})
    assert again["card"]["name"] == "voice-2"
    ref = call(conn, "POST", "/api/projects/p1/cards", {"pool": "images"})["card"]
    assert ref["ref"] == "images" and ref["about"] == "pool images"
    with pytest.raises(ValueError):
        call(conn, "POST", "/api/projects/p1/cards", {})
    gone = call(conn, "DELETE", "/api/projects/p1/cards/voice")
    assert gone["removed"] == "voice" and gone["trash"].startswith(str(paths.user_home() / ".Trash"))
    assert not (proj / "components" / "voice").exists()
    with pytest.raises(KeyError):
        call(conn, "DELETE", "/api/projects/p1/cards/voice")
    assert "components" not in workspace.git(proj, "diff", "--cached", "--name-only")
    assert workspace.git(proj, "log", "--oneline").count("\n") == 0       # nothing committed


def test_api_refuses_without_a_folder(conn):
    project(conn, None, pid="p2")
    for method, path, body in (("POST", "/api/projects/p2/cards", {"template": "voice"}),
                               ("DELETE", "/api/projects/p2/cards/voice", {})):
        with pytest.raises(ValueError, match="pick or clone a folder first"):
            call(conn, method, path, body)
