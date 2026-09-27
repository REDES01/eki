"""/v1/responses against a tiny chat-completions server the test starts."""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eki import models, server


class Chat(BaseHTTPRequestHandler):
    """Streams back whatever chunks the test put on the server; keeps the requests."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "qwen-local"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.server.seen.append((self.path, json.loads(self.rfile.read(n))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in self.server.script:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def chat(home):
    srv = start(Chat)
    srv.seen, srv.script = [], []
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"},
        "mlx": {"kind": "local", "base_url": f"http://127.0.0.1:{srv.server_address[1]}", "params": {"temperature": 0.2}}}))
    yield srv
    srv.shutdown()


@pytest.fixture
def web(home, monkeypatch):
    woken = []
    monkeypatch.setattr(models, "ensure", lambda name, timeout=180: woken.append(name) or True)
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.woken = woken
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, body, header=True):
    req = urllib.request.Request(url + "/v1/responses", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-Eki": "1"} if header else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read().decode()


def parse(sse):
    return [json.loads(block.split("data: ", 1)[1]) for block in sse.strip().split("\n\n") if block]


def test_the_route_streams_a_tool_call_from_the_local_model(chat, web):
    srv, url = web
    chat.script = [
        {"choices": [{"delta": {"content": "<think>hmm</think>Checking."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "shell", "arguments": "{}"}}]}}]},
        {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}]
    code, ctype, body = post(url, {"model": "mlx", "stream": True, "instructions": "you are codex",
                                   "input": [{"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "ls"}]}],
                                   "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}]})
    assert code == 200 and ctype == "text/event-stream"
    evs = parse(body)
    assert evs[0]["type"] == "response.created" and evs[-1]["type"] == "response.completed"
    assert [e["delta"] for e in evs if e["type"] == "response.output_text.delta"] == ["Checking."]
    out = evs[-1]["response"]["output"]
    assert [o["type"] for o in out] == ["message", "function_call"] and out[1]["call_id"] == "c1"
    assert evs[-1]["response"]["usage"]["input_tokens"] == 11
    assert srv.woken == ["mlx"]
    path, sent = chat.seen[0]
    assert path == "/v1/chat/completions" and sent["model"] == "qwen-local" and sent["stream"] is True
    assert sent["messages"][0] == {"role": "system", "content": "you are codex"}
    assert sent["temperature"] == 0.2 and sent["tools"][0]["function"]["name"] == "shell"


def test_an_unknown_model_name_takes_the_first_local_model(chat, web):
    chat.script = [{"choices": [{"delta": {"content": "hi"}}]}]
    code, _, body = post(web[1], {"model": "gpt-5", "input": "hello"})
    assert code == 200 and parse(body)[-1]["response"]["output"][0]["content"][0]["text"] == "hi"


def test_without_the_header_it_is_403(chat, web):
    code, _, body = post(web[1], {"input": "hi"}, header=False)
    assert code == 403 and web[0].woken == [] and chat.seen == []


def test_previous_response_id_is_400_and_wakes_nothing(chat, web):
    code, _, body = post(web[1], {"previous_response_id": "resp_1", "input": "hi"})
    assert code == 400 and "previous_response_id" in json.loads(body)["error"]
    assert web[0].woken == [] and chat.seen == []


def test_a_model_that_does_not_come_up_is_503(chat, web, monkeypatch):
    monkeypatch.setattr(models, "ensure", lambda name, timeout=180: False)
    code, _, _ = post(web[1], {"input": "hi"})
    assert code == 503 and chat.seen == []


def test_a_local_server_that_refuses_is_502_before_any_stream(home, web):
    (home / "providers.json").write_text(json.dumps({
        "mlx": {"kind": "local", "model": "q", "base_url": "http://127.0.0.1:9"}}))
    code, _, body = post(web[1], {"input": "hi"})
    assert code == 502 and "local server" in json.loads(body)["error"]


def test_no_local_model_is_404(web):
    code, _, _ = post(web[1], {"input": "hi"})
    assert code == 404
