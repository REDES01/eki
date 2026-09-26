"""The comfyui provider against a stub ComfyUI on 127.0.0.1."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from eki.providers.base import Turn
from eki.providers.comfyui import DEFAULT_WORKFLOW, Comfyui
from eki.worker import CARRY_ON

PNG = b"\x89PNG\r\n\x1a\nfake"


class Stub:
    """What ComfyUI answers; tests change `mode` and read `posted`."""

    def __init__(self):
        self.mode = "ok"            # ok | pending | node_error | exec_error
        self.posted = []
        self.polls = 0
        self.known = {"p1"}

    def history(self, pid):
        self.polls += 1
        if pid not in self.known or self.mode == "pending" or self.polls < 2:
            return {}
        if self.mode == "exec_error":
            return {pid: {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
                ["execution_start", {}],
                ["execution_error", {"node_type": "UNETLoader", "exception_message": "model not found"}]]}}}
        return {pid: {"outputs": {"13": {"images": [
            {"filename": "eki_00001_.png", "subfolder": "", "type": "output"},
            {"filename": "preview.png", "subfolder": "", "type": "temp"}]}},
            "status": {"status_str": "success", "completed": True, "messages": []}}}


@pytest.fixture
def comfy():
    stub = Stub()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/system_stats":
                self._json(200, {"devices": [{"name": "mps"}]})
            elif u.path.startswith("/history/"):
                self._json(200, stub.history(u.path.rsplit("/", 1)[1]))
            elif u.path == "/queue":
                running = [[1, "p1", {}, {}, []]] if stub.polls < 2 and "p1" in stub.known else []
                self._json(200, {"queue_running": running, "queue_pending": []})
            elif u.path == "/view":
                q = parse_qs(u.query)
                assert q["filename"] == ["eki_00001_.png"] and q["type"] == ["output"]
                self.send_response(200)
                self.send_header("Content-Length", str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)
            else:
                self._json(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            stub.posted.append(body)
            if stub.mode == "node_error":
                self._json(400, {"error": {"type": "prompt_outputs_failed_validation",
                                           "message": "Prompt outputs failed validation"},
                                 "node_errors": {"1": {"class_type": "UNETLoader", "errors": [
                                     {"message": "Value not in list",
                                      "details": "unet_name: 'flux-2-klein-4b.safetensors' not in []"}]}}})
                return
            self._json(200, {"prompt_id": "p1", "number": 1, "node_errors": {}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    stub.provider = Comfyui("comfyui", {"kind": "comfyui", "base_url": f"http://127.0.0.1:{srv.server_port}",
                                        "poll": 0.05})
    yield stub
    srv.shutdown()
    srv.server_close()


def run(provider, turn):
    events = []
    out = provider.take(turn, lambda k, d: events.append((k, d)))
    return out, events


def test_draws_a_picture_into_the_run_folder(comfy, home):
    out, events = run(comfy.provider, Turn(prompt="draw a lighthouse", history=[], run_id="r1"))
    assert out.state == "done"
    assert events[0] == ("session", {"id": "p1"})
    tools = [d for k, d in events if k == "tool"]
    assert len(tools) == 1                                  # the preview isn't kept
    path = tools[0]["path"]
    assert tools[0]["name"] == "image" and tools[0]["detail"] == "draw a lighthouse"
    assert path == str((home / "images" / "r1" / "eki_00001_.png").resolve())
    assert open(path, "rb").read() == PNG
    assert any(k == "text" and "eki_00001_.png" in d["text"] for k, d in events)


def test_the_placeholders_are_filled(comfy):
    comfy.provider.cfg["width"] = 768
    run(comfy.provider, Turn(prompt='a "quoted" cat', history=[], run_id="r1"))
    graph = comfy.posted[0]["prompt"]
    assert "{{" not in json.dumps(graph)
    assert graph["4"]["inputs"]["text"] == 'a "quoted" cat'
    assert graph["9"]["inputs"]["width"] == 768 and graph["9"]["inputs"]["height"] == 1024
    assert isinstance(graph["10"]["inputs"]["noise_seed"], int)
    raw = DEFAULT_WORKFLOW.read_text()
    for key in ("{{prompt}}", "{{seed}}", "{{width}}", "{{height}}"):
        assert f'"{key}"' in raw


def test_a_workflow_of_its_own(comfy, tmp_path):
    wf = tmp_path / "mine.json"
    wf.write_text(json.dumps({"1": {"class_type": "X", "inputs": {"t": "a {{prompt}}!", "s": "{{seed}}"}}}))
    comfy.provider.cfg["workflow"] = str(wf)
    run(comfy.provider, Turn(prompt="fox", history=[], run_id="r1"))
    assert comfy.posted[0]["prompt"]["1"]["inputs"]["t"] == "a fox!"


def test_a_stop_mid_poll_cancels(comfy):
    comfy.mode = "pending"
    turn = Turn(prompt="draw a cat", history=[], run_id="r1")
    threading.Timer(0.2, turn.stop.set).start()
    t0 = time.time()
    out, events = run(comfy.provider, turn)
    assert out.state == "cancelled" and time.time() - t0 < 3
    assert comfy.polls >= 1 and not any(k == "tool" for k, _ in events)


def test_a_carry_on_polls_the_session_and_does_not_draw_again(comfy, home):
    out, events = run(comfy.provider, Turn(prompt=CARRY_ON, history=[], resume="p1", run_id="r2"))
    assert out.state == "done" and comfy.posted == []
    assert not any(k == "session" for k, _ in events)
    assert (home / "images" / "r2" / "eki_00001_.png").exists()


def test_a_new_request_in_the_same_thread_draws_anew(comfy):
    out, _ = run(comfy.provider, Turn(prompt="draw another", history=[], resume="p0", run_id="r3"))
    assert out.state == "done" and len(comfy.posted) == 1


def test_a_node_error_fails_with_comfyuis_message(comfy):
    comfy.mode = "node_error"
    out, _ = run(comfy.provider, Turn(prompt="draw", history=[], run_id="r1"))
    assert out.state == "failed"
    assert "Value not in list" in out.error and "UNETLoader" in out.error


def test_an_execution_error_fails_with_comfyuis_message(comfy):
    comfy.mode = "exec_error"
    out, _ = run(comfy.provider, Turn(prompt="draw", history=[], run_id="r1"))
    assert out.state == "failed" and "model not found" in out.error


def test_available_asks_system_stats(comfy):
    assert comfy.provider.available() == (True, "")


def test_no_server_is_unavailable():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ok, why = Comfyui("comfyui", {"kind": "comfyui", "base_url": f"http://127.0.0.1:{port}"}).available()
    assert not ok and why == f"ComfyUI isn't running at http://127.0.0.1:{port}"


def test_a_carry_on_comfyui_forgot_fails(comfy):
    out, _ = run(comfy.provider, Turn(prompt=CARRY_ON, history=[], resume="gone", run_id="r4"))
    assert out.state == "failed" and "lost" in out.error and comfy.posted == []
