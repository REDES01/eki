"""The programs actually receive the run's pictures (turn.images)."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eki.providers.base import Turn, image_block
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex
from eki.providers.local import Local

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture
def picture(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    return str(p)


class Recording:
    """A Channel without a program: it keeps what the provider writes."""

    def __init__(self, turn):
        self.turn, self.state, self.pending, self.sent = turn, {}, {}, []

    def write(self, obj):
        self.sent.append(obj)


def test_image_block(tmp_path, picture):
    assert image_block(picture) == ("image/png", base64.b64encode(PNG).decode())
    jpg = tmp_path / "b.JPEG"
    jpg.write_bytes(b"x")
    assert image_block(str(jpg))[0] == "image/jpeg"


def test_claude_sends_an_image_block(picture):
    ch = Recording(Turn(prompt="what is this", history=[], images=[picture]))
    ClaudeCode("claude", {})._send_turn(ch)
    content = ch.sent[0]["message"]["content"]
    assert content[0] == {"type": "text", "text": "what is this"}
    assert content[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                      "data": base64.b64encode(PNG).decode()}}


def test_claude_without_pictures_is_unchanged():
    ch = Recording(Turn(prompt="hi", history=[]))
    ClaudeCode("claude", {})._send_turn(ch)
    assert ch.sent[0]["message"]["content"] == [{"type": "text", "text": "hi"}]


def test_codex_turn_start_has_local_image(picture):
    p = Codex("codex", {})
    ch = Recording(Turn(prompt="what is this", history=[], images=[picture]))
    ch.state["calls"] = {1: "thread"}
    p.read({"id": 1, "result": {"thread": {"id": "t1"}}}, lambda k, d: None, None, ch)
    start = [m for m in ch.sent if m.get("method") == "turn/start"][0]
    assert start["params"]["threadId"] == "t1"
    assert start["params"]["input"] == [{"type": "text", "text": "what is this"},
                                        {"type": "localImage", "path": picture}]


class _OpenAI(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send(json.dumps({"data": [{"id": "m"}]}).encode(), "application/json")

    def do_POST(self):
        _OpenAI.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        body = (b"data: " + json.dumps({"choices": [{"delta": {"content": "a cat"}}]}).encode()
                + b"\n\ndata: [DONE]\n\n")
        self._send(body, "text/event-stream")

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def openai():
    _OpenAI.bodies = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _last_user(body):
    return [m for m in body["messages"] if m["role"] == "user"][-1]["content"]


def test_local_with_vision_sends_image_url(openai, picture):
    p = Local("local", {"base_url": openai, "can": ["text", "vision"]})
    out = p.take(Turn(prompt="what is this", history=[], images=[picture]), lambda k, d: None)
    assert out.state == "done"
    content = _last_user(_OpenAI.bodies[-1])
    assert content[0] == {"type": "text", "text": "what is this"}
    assert content[1] == {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(PNG).decode()}}


def test_local_without_vision_sends_no_picture(openai, picture):
    p = Local("local", {"base_url": openai, "can": ["text"]})
    out = p.take(Turn(prompt="what is this", history=[], images=[picture]), lambda k, d: None)
    assert out.state == "done"
    assert _last_user(_OpenAI.bodies[-1]) == "what is this"
    assert "image_url" not in json.dumps(_OpenAI.bodies[-1])
