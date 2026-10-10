"""The service kind: a command per call, a server kept up while used, a provider's turn."""
import json
import socket
import sys
import time

import pytest

from eki import components, db, housekeep
from eki.components import Call, Component, servers, service

PY = sys.executable


def comp(folder, spec, name="svc"):
    d = folder / name
    d.mkdir(parents=True, exist_ok=True)
    spec = {"kind": "service", "about": "a test service", **spec}
    (d / "component.json").write_text(json.dumps(spec))
    return components.load(d, "project")


def a_call(caller, args=(), run_id="r1"):
    events = []
    return Call(args=list(args), caller_dir=str(caller), caller_run=None, run_id=run_id,
                emit=lambda k, d: events.append((k, d)), extra={}), events


def test_run_form_appends_args_in_the_callers_folder(tmp_path):
    caller = tmp_path / "caller"
    caller.mkdir()
    script = ("import os,sys,pathlib; p=pathlib.Path('out.txt'); p.write_text(' '.join(sys.argv[1:]));"
              "print(os.getcwd()); print(os.environ['EKI_CALLER_DIR']); print('out.txt'); print('nope.txt')")
    c = comp(tmp_path / "components", {"run": [PY, "-c", script, "first"]})
    call, _ = a_call(caller, ["second", "third"])
    got = service.call(c, call)
    assert got.error == ""
    lines = got.text.splitlines()
    assert lines[0] == str(caller.resolve()) and lines[1] == str(caller)
    assert (caller / "out.txt").read_text() == "first second third"
    assert got.paths == [str((caller / "out.txt").resolve())]


def test_run_form_failure_carries_stderr(tmp_path):
    c = comp(tmp_path / "components", {"run": [PY, "-c", "import sys; sys.stderr.write('bad voice'); sys.exit(3)"]})
    got = service.call(c, a_call(tmp_path)[0])
    assert "exited 3" in got.error and "bad voice" in got.error


def test_voice_placeholder_fails_with_the_fill_in_reason(tmp_path):
    folder = tmp_path / "components" / "voice"
    folder.parent.mkdir()
    import shutil
    shutil.copytree(components.templates_dir() / "voice", folder)
    c = components.load(folder, "project")
    got = service.call(c, a_call(tmp_path)[0])
    assert got.error == f'fill in "run" in {folder}/component.json'
    assert any("placeholder" in n for n in service.notes(c))


@pytest.mark.parametrize("name", ["service", "images", "voice"])
def test_templates_validate(name):
    c = components.load(components.templates_dir() / name, "pool")
    assert c.kind == "service" and c.spec["about"]


def test_images_template_is_the_comfyui_queue():
    spec = components.load(components.templates_dir() / "images", "pool").spec
    assert (spec["provider"], spec["access"], spec["slots"], spec["memory_gb"], spec["idle_stop"]) == \
        ("comfyui", "queue", 1, 20, 5)


def test_validate_picks_one_form_and_rejects_incomplete_ones(tmp_path):
    assert service.validate({"run": ["a"], "provider": "x"}, tmp_path) == []      # the provider form wins
    assert "missing health" in service.validate({"serve": ["a"], "call": ["b"]}, tmp_path)[0]
    assert service.validate({"serve": "x", "health": "http://127.0.0.1:9/ok", "call": "z"}, tmp_path) == []
    assert service.validate({"run": []}, tmp_path)
    assert service.validate({"run": "echo hi"}, tmp_path) == []


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def add_run(conn, rid, state, ended_at=None):
    conn.execute("INSERT INTO runs(id, thread_id, seq, prompt, provider, state, created_at, ended_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (rid, "t", 1, "x", "component", state, db.now(), ended_at))


def test_serve_form_starts_on_demand_and_housekeeping_stops_it_when_idle(tmp_path, conn):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    c = comp(tmp_path / "components", {
        "serve": [PY, "-m", "http.server", str(port), "--bind", "127.0.0.1"], "url": url, "health": "/",
        "start_timeout": 20, "idle_stop": 0.002,
        "call": [PY, "-c", "import os,sys; print(os.environ['EKI_COMPONENT_URL'], *sys.argv[1:])"]})
    try:
        assert not service.is_up(c)
        add_run(conn, "call1", "running")
        got = service.call(c, a_call(tmp_path, ["hello"], run_id="call1")[0])
        assert got.error == "" and got.text.strip() == f"{url} hello"
        assert service.is_up(c) and servers.ours(c)
        pid = servers.read(c)["pid"]
        time.sleep(0.3)
        assert housekeep.tick(conn) is not None and servers.ours(c)     # its call is still running
        conn.execute("UPDATE runs SET state='done', ended_at=? WHERE id='call1'", (db.now(),))
        time.sleep(0.3)
        said = servers.tick(conn)
        assert said == [f"stopped component svc: idle for 0.002 min"]
        assert not servers.alive(pid) and not service.is_up(c)
        assert not servers.read(c)
    finally:
        service.stop(c)


def test_two_projects_same_name_keep_separate_state(tmp_path):
    a = comp(tmp_path / "a" / "components", {"run": ["echo"]}, "voice")
    b = comp(tmp_path / "b" / "components", {"run": ["echo"]}, "voice")
    assert servers.key(a.name, a.folder) != servers.key(b.name, b.folder)


def test_eki_never_stops_a_server_it_did_not_start(tmp_path):
    c = comp(tmp_path / "components", {"serve": ["x"], "url": "http://127.0.0.1:9", "health": "/",
                                       "call": ["y"]})
    assert service.stop(c) is False


def test_provider_form_is_one_turn_of_that_provider(tmp_path):
    c = comp(tmp_path / "components", {"provider": "fake"})
    call, events = a_call(tmp_path, ["say", "hi"])
    got = service.call(c, call)
    assert got.error == "" and got.text
    assert any(k == "text" for k, _ in events)


def test_provider_form_paths_are_the_pictures_it_emits(tmp_path, monkeypatch):
    from eki import providers
    from eki.providers.base import Outcome
    pic = tmp_path / "p.png"
    pic.write_bytes(b"x")
    seen = {}

    class Drawer:
        def take(self, turn, emit):
            seen.update(prompt=turn.prompt, run=turn.run_id, cwd=turn.cwd)
            emit("tool", {"name": "image", "path": str(pic)})
            emit("text", {"text": f"Drew 1 picture:\n{pic}"})
            return Outcome("done", finished=True)

    monkeypatch.setattr(providers, "get", lambda name: Drawer())
    c = comp(tmp_path / "components", {"provider": "comfyui"})
    got = service.call(c, a_call(tmp_path, ["a", "red", "fox"], run_id="r9")[0])
    assert got.paths == [str(pic)] and seen == {"prompt": "a red fox", "run": "r9", "cwd": str(tmp_path)}


def test_provider_form_failure_and_missing_provider(tmp_path):
    c = comp(tmp_path / "components", {"provider": "nosuch"})
    assert "no provider named" in service.call(c, a_call(tmp_path)[0]).error
    f = comp(tmp_path / "components", {"provider": "fake"}, "failing")
    assert service.call(f, a_call(tmp_path, ["fail"])[0]).error
