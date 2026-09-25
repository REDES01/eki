import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from eki.providers.base import Outcome, Turn, with_history
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex, mcp_flags
from eki.providers.local import Local


def collect(provider, lines):
    got, out = [], Outcome()
    for line in lines:
        provider.read(line, lambda k, d: got.append((k, d)), out)
    return got, out


def test_claude_stream():
    p = ClaudeCode("claude", {})
    got, out = collect(p, [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "opus"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "hello"}]}},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
        {"type": "result", "is_error": False, "usage": {"output_tokens": 3}},
    ])
    kinds = [k for k, _ in got]
    assert kinds == ["session", "tool", "text", "usage"] and out.error == ""


def test_claude_limit():
    got, out = collect(ClaudeCode("claude", {}), [
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1900000000}},
        {"type": "result", "is_error": True, "result": "Claude AI usage limit reached"}])
    assert out.reset_at == 1900000000 and "limit" in out.error


def test_claude_argv_resume_and_folder():
    p = ClaudeCode("claude", {"binary": "/bin/echo"})
    argv = p.argv(Turn(prompt="hi", history=[], resume="s1", cwd="/tmp",
                       extra={"claude_plugin": "/p", "claude_mcp": "/m.json"}))
    assert argv[:3] == ["/bin/echo", "-p", "hi"]
    for pair in (["--resume", "s1"], ["--add-dir", "/tmp"], ["--plugin-dir", "/p"], ["--mcp-config", "/m.json"]):
        i = argv.index(pair[0])
        assert argv[i + 1] == pair[1]


def test_codex_stream_and_resume_argv():
    p = Codex("codex", {"binary": "/bin/echo"})
    got, out = collect(p, [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1}}])
    assert [k for k, _ in got] == ["session", "tool", "text", "usage"]
    argv = p.argv(Turn(prompt="go", history=[], resume="t1"))
    assert argv[1:4] == ["exec", "resume", "t1"] and argv[-1] == "go"


def test_codex_failure():
    _, out = collect(Codex("codex", {}), [{"type": "turn.failed", "error": {"message": "boom"}}])
    assert out.error == "boom"


def test_mcp_flags():
    flags = mcp_flags({"fs": {"command": "npx", "args": ["-y", "srv"], "env": {"K": "v"}},
                       "web": {"url": "https://x/mcp"}})
    assert 'mcp_servers.fs.command="npx"' in flags
    assert 'mcp_servers.fs.args=["-y", "srv"]' in flags
    assert 'mcp_servers.fs.env={K = "v"}' in flags
    assert 'mcp_servers.web.url="https://x/mcp"' in flags


def test_history_preface():
    t = Turn(prompt="now this", history=[{"role": "user", "content": "q"},
                                         {"role": "assistant", "content": "a"}])
    text = with_history(t)
    assert "User: q" in text and "Assistant: a" in text and text.endswith("now this")


# ---- a local model, over a tiny fake OpenAI-compatible server --------------------------

class _Server(BaseHTTPRequestHandler):
    reply = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send(json.dumps({"data": [{"id": "qwen"}]}).encode(), "application/json")

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b"".join(b"data: " + json.dumps({"choices": [{"delta": d}]}).encode() + b"\n\n"
                        for d in _Server.reply) + b"data: [DONE]\n\n"
        self._send(body, "text/event-stream")

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_local_answers(server):
    _Server.reply = [{"content": "a haiku "}, {"content": "about snow"}]
    p = Local("local", {"base_url": server})
    assert p.available()[0]
    got = []
    out = p.take(Turn(prompt="haiku", history=[]), lambda k, d: got.append((k, d)))
    assert out.state == "done" and "".join(d["text"] for k, d in got if k == "text") == "a haiku about snow"


def test_local_hands_off(server):
    _Server.reply = [{"tool_calls": [{"index": 0, "function": {"name": "handoff", "arguments": ""}}]},
                     {"tool_calls": [{"index": 0, "function": {"arguments": '{"reason": "needs files"}'}}]}]
    out = Local("local", {"base_url": server}).take(Turn(prompt="fix my repo", history=[]), lambda k, d: None)
    assert out.state == "handed_off" and out.reason == "needs files"


def test_local_down():
    ok, why = Local("local", {"base_url": "http://127.0.0.1:9"}).available()
    assert not ok and "not running" in why


def test_prompt_check_uses_the_local_model(server, home):
    from eki.routing.check import check
    (home / "providers.json").write_text(json.dumps({"local": {"kind": "local", "base_url": server}}))
    _Server.reply = [{"content": '{"row": "code", "why": "changes files"}'}]
    (home / "routing.json").unlink()
    assert check("fix the failing test") == ("code", "prompt check: changes files")
    _Server.reply = [{"content": "no idea"}]
    assert check("???")[0] == "general"
