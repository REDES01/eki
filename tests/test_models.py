import json
import socket
import sys
import time

import pytest

from eki import models

SERVE = """
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"data": [{"id": "m"}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("EKI_MODELS_POLL", "0.02")
    monkeypatch.setenv("EKI_MODELS_STOP_WAIT", "0.5")


@pytest.fixture
def model(home):
    port = free_port()
    cfg = {"kind": "local", "base_url": f"http://127.0.0.1:{port}", "keep_up": True,
           "serve": {"command": [sys.executable, "-c", SERVE, str(port)]}}
    (home / "providers.json").write_text(json.dumps({"local": cfg}))
    yield "local"
    models.stop("local", by="test")


def wait_up(name, up=True, t=10):
    end = time.time() + t
    while time.time() < end:
        if models.status(name)["up"] == up:
            return True
        time.sleep(0.02)
    return False


def test_start_and_stop(model):
    assert not models.status(model)["up"]
    models.start(model)
    assert wait_up(model)
    st = models.stop(model)
    assert wait_up(model, up=False) and not st["held"]       # a plain stop is not a hold


def test_ensure_starts_it_for_a_run(model):
    assert models.ensure(model, timeout=10)
    assert models.status(model)["managed"]


def test_kept_up_and_stepped_out(model, conn, monkeypatch):
    assert models.duty(conn) == ["started local: kept up"]
    assert wait_up(model)
    monkeypatch.setenv("EKI_MEMORY_PRESSURE", "2")
    assert models.duty(conn) == ["stepped local out: memory under pressure"]
    assert wait_up(model, up=False)
    monkeypatch.setenv("EKI_MEMORY_PRESSURE", "1")
    assert models.duty(conn) == []          # waits a while before coming back


def test_your_stop_is_a_step_out_unless_held(model, conn, monkeypatch):
    models.duty(conn)
    assert wait_up(model)
    models.stop(model)                                   # "give me the memory": comes back after the pause
    assert wait_up(model, up=False)
    assert models.duty(conn) == []
    monkeypatch.setenv("EKI_MODELS_STEP_OUT_PAUSE", "0")
    assert models.duty(conn) == ["started local: kept up"]
    assert wait_up(model)
    models.stop(model, hold=True)                        # "keep it off": stays down
    assert wait_up(model, up=False)
    assert models.duty(conn) == [] and models.status(model)["held"]
    models.start(model)                                  # your start lifts the hold
    assert not models.status(model)["held"]


def test_a_server_eki_didnt_start_is_left_alone(model, conn, monkeypatch):
    import subprocess
    cfg = json.loads((models.paths.config("providers")).read_text())["local"]
    proc = subprocess.Popen(cfg["serve"]["command"])
    try:
        assert wait_up(model)
        monkeypatch.setenv("EKI_MEMORY_PRESSURE", "4")
        assert models.duty(conn) == []
        assert models.status(model)["up"] and not models.status(model)["managed"]
    finally:
        proc.terminate()


def test_a_short_step_out_pause_brings_it_back(model, conn, monkeypatch):
    models.duty(conn)
    assert wait_up(model)
    monkeypatch.setenv("EKI_MEMORY_PRESSURE", "2")
    assert models.duty(conn) == ["stepped local out: memory under pressure"]
    assert wait_up(model, up=False)
    monkeypatch.setenv("EKI_MEMORY_PRESSURE", "1")
    monkeypatch.setenv("EKI_MODELS_STEP_OUT_PAUSE", "0")
    assert models.duty(conn) == ["started local: kept up"]
    assert wait_up(model)


def test_intervals_from_the_environment(monkeypatch):
    assert models.interval("step_out_pause") == models.STEP_OUT_PAUSE
    monkeypatch.setenv("EKI_MODELS_POLL", "0.25")
    assert models.interval("poll") == 0.25
    monkeypatch.setenv("EKI_MODELS_STOP_WAIT", "not a number")
    assert models.interval("stop_wait") == models.STOP_WAIT


def on_demand(home, model, idle_minutes):
    cfg = json.loads((home / "providers.json").read_text())
    cfg["local"].update(keep_up=False, idle_stop=idle_minutes)
    (home / "providers.json").write_text(json.dumps(cfg))


def test_an_on_demand_model_stays_off_until_a_run_and_stops_when_idle(home, model, conn):
    from eki import capacity
    on_demand(home, model, 0.001)                        # ~0.06 s idle
    assert models.duty(conn) == []                       # not kept up: stays off
    assert not models.status(model)["up"] and models.status(model)["on_demand"]
    ok, why = capacity.status(conn)[model]
    assert ok and "starts for the run" in why            # routing may still pick it
    assert models.ensure(model) and models.status(model)["up"]   # a run starts it
    time.sleep(0.1)
    assert models.duty(conn) == ["stopped local: idle for 0 min"]
    assert wait_up(model, up=False)


def test_idle_stop_zero_never_stops(home, model, conn):
    on_demand(home, model, 0)
    assert models.ensure(model)
    time.sleep(0.05)
    assert models.duty(conn) == [] and models.status(model)["up"]


# ---- ComfyUI: a managed server like the local model -------------------------------------

@pytest.fixture
def comfy(home):
    """A local model and an on-demand ComfyUI entry, each a fake server on its own port."""
    lport, cport = free_port(), free_port()
    cfg = {"local": {"kind": "local", "base_url": f"http://127.0.0.1:{lport}",
                     "serve": {"command": [sys.executable, "-c", SERVE, str(lport)]}},
           "comfyui": {"kind": "comfyui", "label": "ComfyUI (pictures)", "base_url": f"http://127.0.0.1:{cport}",
                       "serve": {"command": [sys.executable, "-c", SERVE, str(cport)]}, "idle_stop": 5}}
    (home / "providers.json").write_text(json.dumps(cfg))
    yield "comfyui"
    models.stop("comfyui", by="test")
    models.stop("local", by="test")


def test_comfyui_is_managed_and_started_for_a_run(comfy, conn):
    from eki import capacity
    assert set(models.managed()) == {"local", "comfyui"} and models.local_models is models.managed
    st = models.status(comfy)
    assert st["kind"] == "comfyui" and st["on_demand"] and not st["up"]
    assert models.duty(conn) == []                       # on demand: stays off
    assert capacity.status(conn)[comfy] == (True, "off; starts for the run")
    assert models.ensure(comfy, timeout=10) and models.status(comfy)["managed"]


def test_comfyui_stops_when_idle(comfy, conn, monkeypatch):
    assert models.ensure(comfy, timeout=10)
    assert models.duty(conn) == []                       # just started: not idle yet
    models._write(comfy, started_at=time.time() - 10 * 60)
    assert models.duty(conn) == ["stopped comfyui: idle for 5 min"]
    assert wait_up(comfy, up=False)


def test_comfyui_steps_out_under_pressure_unless_drawing(comfy, conn, monkeypatch):
    from eki import db, store
    assert models.ensure(comfy, timeout=10)
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "t", None), "draw a fox", provider=comfy)
        store.update_run(conn, rid, state="running")
    monkeypatch.setenv("EKI_MEMORY_PRESSURE", "2")
    assert models.in_use(conn, comfy) and models.duty(conn) == []
    with db.tx(conn):
        store.update_run(conn, rid, state="done", ended_at=time.time())
    assert models.duty(conn) == ["stepped comfyui out: memory under pressure"]
    assert wait_up(comfy, up=False)


def test_a_comfyui_started_outside_eki_is_left_alone(comfy, conn, monkeypatch):
    import subprocess
    cfg = models.managed()[comfy]
    proc = subprocess.Popen(cfg["serve"]["command"])
    try:
        assert wait_up(comfy)
        models._write(comfy, started_at=0)
        monkeypatch.setenv("EKI_MEMORY_PRESSURE", "4")
        assert models.duty(conn) == []
        monkeypatch.setenv("EKI_MEMORY_PRESSURE", "1")
        assert models.duty(conn) == []                   # nor for idleness
        assert models.status(comfy)["up"] and not models.status(comfy)["managed"]
    finally:
        proc.terminate()


def test_eki_models_lists_comfyui_beside_local(comfy, capsys):
    from eki import cli
    assert cli.main(["models"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("local") and any(ln.startswith("comfyui") for ln in lines)
    assert "  draft: flux-2-klein-4b" in lines and "  hq: qwen-image-2.1-Q8" in lines
    assert "on demand" in out


def test_the_default_comfyui_joins_only_where_it_is_installed(home, tmp_path, monkeypatch):
    (home / "providers.json").write_text(json.dumps({"local": {"kind": "local", "base_url": "http://127.0.0.1:1"}}))
    before = (home / "providers.json").read_bytes()
    assert "comfyui" not in models.managed()             # conftest points at a missing folder
    where = tmp_path / "ComfyUI"
    where.mkdir()
    (where / "main.py").write_text("")
    monkeypatch.setenv("EKI_COMFYUI_DIR", str(where))
    assert models.managed()["comfyui"]["kind"] == "comfyui"
    assert models.status("comfyui")["startable"]
    assert (home / "providers.json").read_bytes() == before


def test_a_run_pinned_to_an_off_comfyui_starts_it_and_the_sidebar_sees_it(comfy, conn):
    from eki import api, db, routing, store
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "t", None), "draw a fox", provider=comfy)
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider == comfy and d.row == "picked"            # capacity: off, starts for the run
    item = next(p for p in api.provider_list(conn) if p["name"] == comfy)
    assert item["model"]["kind"] == "comfyui" and item["model"]["startable"]
