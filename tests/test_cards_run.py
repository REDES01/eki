"""Running a card from the project page: the run `eki use` makes, then its uses."""
import json
import sys

import pytest

from eki import apiroutes, store

from conftest import run_inline

PY = sys.executable


def put(base, name, spec):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(spec if isinstance(spec, str) else json.dumps(spec))
    return d


def echo(**kw):
    return {"kind": "service", "about": "echoes its args",
            "run": [PY, "-c", "import sys; print('said ' + ' '.join(sys.argv[1:])); print('more')"], **kw}


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder or "/nowhere"), "trunk", 0, str(folder) if folder else None))
    conn.commit()


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def test_run_makes_the_eki_use_run(conn, proj):
    project(conn, proj)
    put(proj / "components", "voice", echo())
    rid = call(conn, "POST", "/api/projects/p1/cards/voice/run", {"args": "hello 'big world'"})["run"]
    r = store.run(conn, rid)
    assert r["provider"] == "component" and r["priority"] == "now" and r["state"] == "queued"
    assert r["parent"] is None
    want = json.loads(r["prompt"])
    assert want["component"] == "voice" and want["args"] == ["hello", "big world"]
    assert want["caller_dir"] == str(proj.resolve()) and want["caller_run"] is None


def test_run_inline_shows_in_uses(conn, proj):
    project(conn, proj)
    put(proj / "components", "voice", echo())
    old = call(conn, "POST", "/api/projects/p1/cards/voice/run", {"args": "first"})["run"]
    run_inline(conn, old)
    rid = call(conn, "POST", "/api/projects/p1/cards/voice/run", {"args": "hi there"})["run"]
    assert run_inline(conn, rid)["state"] == "done"
    got = call(conn, "GET", "/api/projects/p1/cards/voice/uses")["uses"]
    assert [u["run"] for u in got] == [rid, old]
    u = got[0]
    assert u["caller"] == "you" and u["result"] == "said hi there" and u["state"] == "done"
    assert isinstance(u["seconds"], float) and u["error"] == ""
    one = call(conn, "GET", f"/api/projects/p1/cards/voice/uses/{rid}")
    assert one["state"] == "done" and one["text"].startswith("said hi there\nmore")
    assert one["why"] == "you picked component" and one["paths"] == [] and one["result"] == "said hi there"
    json.dumps(one)


def test_failing_call_shows_its_error(conn, proj):
    project(conn, proj)
    put(proj / "components", "bad", echo(run=[PY, "-c", "import sys; sys.stderr.write('no voice'); sys.exit(3)"]))
    rid = call(conn, "POST", "/api/projects/p1/cards/bad/run", {"args": ""})["run"]
    assert run_inline(conn, rid)["state"] == "failed"
    u = call(conn, "GET", "/api/projects/p1/cards/bad/uses")["uses"][0]
    assert u["state"] == "failed" and "no voice" in u["error"]
    assert "no voice" in call(conn, "GET", f"/api/projects/p1/cards/bad/uses/{rid}")["error"]


def test_a_run_of_another_card_is_not_its_use(conn, proj):
    project(conn, proj)
    put(proj / "components", "voice", echo())
    put(proj / "components", "other", echo())
    rid = call(conn, "POST", "/api/projects/p1/cards/other/run", {})["run"]
    with pytest.raises(KeyError):
        call(conn, "GET", f"/api/projects/p1/cards/voice/uses/{rid}")


def test_unknown_broken_or_bad_args_refuse(conn, proj):
    project(conn, proj)
    put(proj / "components", "voice", echo())
    put(proj / "components", "broken", "{nope")
    with pytest.raises(KeyError):
        call(conn, "POST", "/api/projects/p1/cards/nope/run", {"args": "x"})
    with pytest.raises(ValueError, match="broken"):
        call(conn, "POST", "/api/projects/p1/cards/broken/run", {"args": "x"})
    with pytest.raises(ValueError, match="args"):
        call(conn, "POST", "/api/projects/p1/cards/voice/run", {"args": "'open"})
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE provider='component'").fetchone()[0] == 0


def test_folderless_project_refuses_run(conn):
    project(conn, None)
    with pytest.raises(ValueError, match="pick or clone a folder first"):
        call(conn, "POST", "/api/projects/p1/cards/voice/run", {"args": "x"})
    assert call(conn, "GET", "/api/projects/p1/cards/voice/uses") == {"uses": []}
