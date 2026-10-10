"""Components: found in a project folder and the machine-wide pool; broken ones skipped."""
import json
import os

from eki import components, workspace
from eki.cli import main


def put(base, name, spec, files=None):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(spec if isinstance(spec, str) else json.dumps(spec))
    for f, text in (files or {}).items():
        (d / f).write_text(text)
    return d


def svc(about="does a thing", **kw):
    return {"kind": "service", "about": about, "run": "echo hi", **kw}


def test_nothing_reachable(proj):
    assert components.find(proj) == ({}, [])
    assert components.brief(proj) == ""
    assert components.listing(proj) == []


def test_broken_is_reported_and_skipped(proj):
    base = proj / "components"
    put(base, "good", svc())
    put(base, "badjson", "{nope")
    put(base, "badkind", {"kind": "rocket", "about": "x"})
    put(base, "noabout", {"kind": "service", "run": "x"})
    put(base, "badaccess", svc(access="sometimes"))
    put(base, "noprompt", {"kind": "agent", "about": "a persona"})
    put(base, "badpath", {"kind": "assets", "about": "art", "path": "../out"})
    put(base, "Bad Name", svc())
    found, errors = components.find(proj)
    assert list(found) == ["good"]
    broken = {os.path.basename(f): why for f, why in errors}
    assert set(broken) == {"badjson", "badkind", "noabout", "badaccess", "noprompt", "badpath", "Bad Name"}
    assert "unreadable" in broken["badjson"] and "prompt.md" in broken["noprompt"]
    rows = {r["name"]: r for r in components.listing(proj)}
    assert rows["good"]["error"] == "" and rows["badkind"]["error"]


def test_validate_exception_is_contained(proj, monkeypatch):
    from eki.components import service
    monkeypatch.setattr(service, "validate", lambda spec, folder: 1 / 0)
    put(proj / "components", "boom", svc())
    found, errors = components.find(proj)
    assert found == {} and "ZeroDivisionError" in errors[0][1]


def test_project_overrides_pool(proj):
    put(components.pool_dir(), "voice", svc("pool voice"))
    put(components.pool_dir(), "images", svc("pool images"))
    put(proj / "components", "voice", svc("project voice"))
    found, _ = components.find(proj)
    assert found["voice"].scope == "project" and found["voice"].spec["about"] == "project voice"
    assert found["images"].scope == "pool"
    assert components.brief(proj) == ("Components you can call with `eki use <name> [args…]` (`eki use` lists them): "
                                      "images — pool images; voice — project voice")


def test_sessions_folder_in_pool_ignored(proj):
    put(components.pool_dir(), "sessions", svc())
    assert components.find(proj) == ({}, [])


def test_worktree_finds_its_projects_components(proj, tmp_path):
    put(proj / "components", "voice", svc())
    wt = tmp_path / "wt"
    workspace.git(proj, "worktree", "add", "-q", "-b", "side", str(wt))
    found, _ = components.find(wt)
    assert components.project_root(wt) == proj.resolve()
    assert found["voice"].folder == (proj / "components" / "voice").resolve()


def test_github_row_reads_from_folder(conn, tmp_path):
    clone = tmp_path / "clone"
    clone.mkdir()
    workspace.git(clone, "init", "-q")
    mine = tmp_path / "mine"
    put(mine / "components", "voice", svc())
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 ("p1", "clone", str(clone.resolve()), "main", 0, str(mine)))
    conn.commit()
    assert components.project_root(clone, conn) == mine.resolve()
    assert list(components.find(clone, conn)[0]) == ["voice"]
    assert components.find(clone)[0] == {}


def test_get_missing_says_why(proj):
    put(proj / "components", "voice", svc())
    try:
        components.get("nope", proj)
        assert False
    except KeyError as e:
        assert "no component nope reachable" in str(e) and "voice" in str(e)
    assert components.get("voice", proj).kind == "service"


def test_journal_session(home):
    from eki.components import journal
    a, b = journal.new_session("tester"), journal.new_session("tester")
    assert a != b and a.parent == components.sessions_dir() / "tester"
    r = journal.keep(a, ["first", "the review"])
    assert r.text == f"the review\n\nsession: {a}" and r.paths == [str(a)]
    assert "first" in (a / "transcript.md").read_text()


def test_eki_use_lists(proj, monkeypatch, capsys):
    put(proj / "components", "voice", svc("speaks lines"))
    put(proj / "components", "broken", "{")
    monkeypatch.chdir(proj)
    assert main(["use"]) == 0
    out = capsys.readouterr()
    assert "voice" in out.out and "speaks lines" in out.out and "project" in out.out
    assert "broken: " in out.err
