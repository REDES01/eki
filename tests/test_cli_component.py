"""`eki component`: kinds, ls, add from a template (never overwriting), show."""
import json

import pytest

from eki import components, mcp, workspace
from eki.cli import main


@pytest.fixture
def temps(tmp_path, monkeypatch):
    t = tmp_path / "templates"
    (t / "service").mkdir(parents=True)
    (t / "service" / "component.json").write_text(json.dumps(
        {"kind": "service", "about": "a command per call", "run": "echo hi"}))
    (t / "browser").mkdir()
    (t / "browser" / "component.json").write_text(json.dumps(
        {"kind": "service", "about": "drives a browser", "run": "echo b",
         "mcp_servers": {"playwright": {"command": "npx", "args": ["@playwright/mcp", "--vision"]}}}))
    (t / "browser" / "README.md").write_text("fill in\n")
    monkeypatch.setattr(components, "templates_dir", lambda: t)
    return t


def test_kinds_lists_kinds_and_templates(temps, capsys):
    assert main(["component", "kinds"]) == 0
    out = capsys.readouterr().out
    for want in ("service", "assets", "agent", "browser", "drives a browser"):
        assert want in out


def test_add_writes_the_folder_and_refuses_to_overwrite(proj, temps, monkeypatch, capsys):
    monkeypatch.chdir(proj)
    assert main(["component", "add", "service", "--name", "say"]) == 0
    made = proj / "components" / "say" / "component.json"
    assert json.loads(made.read_text())["run"] == "echo hi"
    assert str(made.parent) in capsys.readouterr().out
    made.write_text(json.dumps({"kind": "service", "about": "mine", "run": "echo mine"}))
    assert main(["component", "add", "service", "--name", "say"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert json.loads(made.read_text())["about"] == "mine"
    assert workspace.git(proj, "status", "--porcelain").count("components") == 1   # untracked, nothing staged
    assert "components" not in workspace.git(proj, "diff", "--cached", "--name-only")


def test_add_from_a_worktree_goes_to_the_project(proj, temps, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    workspace.git(proj, "worktree", "add", "-q", "-b", "w", str(wt))
    monkeypatch.chdir(wt)
    assert main(["component", "add", "service"]) == 0
    assert (proj / "components" / "service" / "component.json").is_file()
    assert not (wt / "components").exists()


def test_pool_and_dash_c_place_it(proj, temps, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["component", "add", "service", "--pool"]) == 0
    assert (components.pool_dir() / "service" / "component.json").is_file()
    assert main(["component", "add", "service", "-C", str(proj), "--name", "here"]) == 0
    assert (proj / "components" / "here" / "component.json").is_file()


def test_unknown_template_and_bad_name(proj, temps, monkeypatch, capsys):
    monkeypatch.chdir(proj)
    assert main(["component", "add", "rocket"]) == 1
    assert "no template rocket" in capsys.readouterr().err
    assert main(["component", "add", "service", "--name", "sessions", "--pool"]) == 1
    assert not (proj / "components").exists()


def test_template_mcp_servers_are_registered_off_and_dropped(proj, temps, monkeypatch):
    monkeypatch.chdir(proj)
    assert main(["component", "add", "browser"]) == 0
    spec = json.loads((proj / "components" / "browser" / "component.json").read_text())
    assert "mcp_servers" not in spec and spec["about"] == "drives a browser"
    assert (proj / "components" / "browser" / "README.md").is_file()
    assert "playwright" not in mcp.servers()
    assert mcp.off()["playwright"]["args"] == ["@playwright/mcp", "--vision"]
    assert "mcp_servers" in json.loads((temps / "browser" / "component.json").read_text())


def test_an_existing_mcp_server_is_left_alone(proj, temps, monkeypatch):
    mcp.add("playwright", command="mine")
    monkeypatch.chdir(proj)
    assert main(["component", "add", "browser"]) == 0
    assert mcp.servers()["playwright"]["command"] == "mine" and "playwright" not in mcp.off()


def test_ls_shows_pool_project_and_broken(proj, temps, monkeypatch, capsys):
    monkeypatch.chdir(proj)
    main(["component", "add", "service", "--pool", "--name", "shared"])
    main(["component", "add", "service", "--name", "own"])
    (proj / "components" / "shared").mkdir()
    (proj / "components" / "shared" / "component.json").write_text('{"pool": "shared"}')
    (proj / "components" / "bad").mkdir()
    (proj / "components" / "bad" / "component.json").write_text("{nope")
    capsys.readouterr()
    assert main(["component", "ls", str(proj)]) == 0
    out = capsys.readouterr().out
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln and not ln.startswith(" ")}
    assert "pool" in lines["shared"] and "project" in lines["own"]
    assert "broken" in lines["bad"] and "unreadable" in lines["bad"]


def test_show_prints_folder_scope_and_json(proj, temps, monkeypatch, capsys):
    monkeypatch.chdir(proj)
    main(["component", "add", "service", "--name", "say"])
    capsys.readouterr()
    assert main(["component", "show", "say"]) == 0
    out = capsys.readouterr().out
    assert str(proj / "components" / "say") in out and "project" in out and '"run": "echo hi"' in out
    assert main(["component", "show", "nope"]) == 1


def test_add_without_name_takes_the_next_free(proj, temps, monkeypatch):
    monkeypatch.chdir(proj)
    for _ in range(3):
        assert main(["component", "add", "service"]) == 0
    assert sorted(p.name for p in (proj / "components").iterdir()) == ["service", "service-2", "service-3"]


def test_attach_rm_and_uses(proj, temps, monkeypatch, capsys, conn):
    from eki import db, paths, store
    monkeypatch.chdir(proj)
    main(["component", "add", "service", "--pool", "--name", "shared"])
    assert main(["component", "attach", "shared", "-C", str(proj)]) == 0
    card = proj / "components" / "shared" / "component.json"
    assert json.loads(card.read_text()) == {"pool": "shared"}
    assert main(["component", "attach", "shared"]) == 1
    assert main(["component", "attach", "nope"]) == 1
    assert "no component nope" in capsys.readouterr().err

    prompt = json.dumps({"component": "shared", "args": ["x"], "caller_dir": str(proj), "caller_run": None})
    with db.tx(conn):
        tid = store.create_thread(conn, "use shared", None, by="eki")
        rid = store.create_run(conn, tid, prompt, provider="component")
    conn.execute("UPDATE runs SET state='done', created_at=100, started_at=100, ended_at=103.5 WHERE id=?", (rid,))
    conn.commit()
    store.add_event(conn, rid, 0, "text", {"text": "made it\nmore"})
    assert main(["component", "uses", "shared"]) == 0
    out = capsys.readouterr().out
    assert "you" in out and "done" in out and "3.5s" in out and "made it" in out and "more" not in out
    assert main(["component", "uses", "voice"]) == 0
    assert "no calls" in capsys.readouterr().out

    assert main(["component", "rm", "shared", "-C", str(proj)]) == 0
    out = capsys.readouterr().out
    assert str(paths.user_home() / ".Trash") in out
    assert not card.exists() and (components.pool_dir() / "shared" / "component.json").is_file()
    assert main(["component", "rm", "shared"]) == 1
    assert "no card shared" in capsys.readouterr().err
