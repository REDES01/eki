"""The web server is the engine's child: a pid of its own, kept, and gone with the engine."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from eki import webproc

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def status(port: int, wait: float = 20.0, unless=None) -> dict:
    """GET /api/status until it answers (and, with `unless`, until it says something else)."""
    until = time.time() + wait
    while time.time() < until:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
                got = json.loads(r.read())
            if unless is None or not unless(got):
                return got
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"no answer on :{port} in {wait}s")


def gone(pid: int, wait: float) -> bool:
    until = time.time() + wait
    while time.time() < until:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def bindable(port: int, wait: float) -> bool:
    until = time.time() + wait
    while time.time() < until:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as the next server binds it
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.1)
        finally:
            s.close()
    return False


def kill(pid) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, TypeError):
        pass


@pytest.fixture
def engine_up(home):
    port = free_port()
    env = {**os.environ, "EKI_HOME": str(home), "EKI_PORT": str(port), "EKI_MACHINE": "ok",
           "PYTHONPATH": str(ROOT) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    proc = subprocess.Popen([sys.executable, "-m", "eki.engine"], cwd=str(home), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    seen: list = []
    try:
        st = status(port)
        seen.append(st["server"])
        yield proc, port, st, seen
    finally:
        kill(proc.pid)
        proc.wait(timeout=10)
        for pid in seen:
            kill(pid)


def test_the_server_answers_from_a_different_pid(engine_up):
    proc, _port, st, _seen = engine_up
    assert st["server"] != st["engine"]
    assert st["engine"] == proc.pid
    assert st["pid"] == st["server"]


def test_a_killed_server_comes_back(engine_up):
    proc, port, st, seen = engine_up
    os.kill(st["server"], signal.SIGKILL)
    again = status(port, wait=10, unless=lambda s: s["server"] == st["server"])
    seen.append(again["server"])
    assert again["server"] != st["server"] and again["engine"] == proc.pid


def test_stopping_the_engine_stops_the_server(engine_up):
    proc, port, st, _seen = engine_up
    proc.send_signal(signal.SIGTERM)
    assert gone(st["server"], 5)
    assert bindable(port, 5)
    assert proc.wait(timeout=10) == 0


def test_the_lifeline_holds_when_the_engine_is_killed(engine_up):
    proc, port, st, _seen = engine_up
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=5)
    assert gone(st["server"], 5)
    assert bindable(port, 5)


def test_port_zero_starts_nothing(monkeypatch):
    monkeypatch.setenv("EKI_PORT", "0")
    assert webproc.start() is None
    assert webproc.keep(None) is None


class Dead:
    pid = 4242
    stdin = None

    def poll(self):
        return 1


def test_the_restart_pause_holds(monkeypatch):
    monkeypatch.setenv("EKI_PORT", "7")
    started, faults = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: started.append(Dead()) or started[-1])
    monkeypatch.setattr(webproc.observe, "fault", lambda conn, where, *a, **kw: faults.append(where))
    clock = [1000.0]
    monkeypatch.setattr(webproc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(webproc, "_last_start", [0.0])
    proc = webproc.start()
    assert len(started) == 1
    for _ in range(5):                                  # dead at once: one restart per pause, no spin
        clock[0] += 0.3
        proc = webproc.keep(proc)
    assert len(started) == 1 and faults == ["web server exited 1"]
    clock[0] += webproc.RESTART_PAUSE
    proc = webproc.keep(proc)
    assert len(started) == 2 and proc is started[-1]
    clock[0] += 0.1
    proc = webproc.keep(proc)
    assert len(started) == 2 and faults == ["web server exited 1"] * 2
    clock[0] += webproc.RESTART_PAUSE
    webproc.keep(proc)
    assert len(started) == 3
