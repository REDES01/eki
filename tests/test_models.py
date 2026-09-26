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
