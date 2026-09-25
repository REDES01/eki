import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from eki.providers.base import Outcome, Turn, with_history
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex, mcp_flags
from eki.providers.local import Local


class FakeChannel:
    def __init__(self, turn=None):
        self.turn = turn or Turn(prompt="hi", history=[])
        self.written, self.asked, self.state, self.pending = [], [], {}, {}

    def write(self, obj):
        self.written.append(obj)

    def ask(self, kind, payload, key):
        self.asked.append((kind, payload, key))
        return f"a{len(self.asked)}"

    def retract(self, key):
        self.asked = [a for a in self.asked if a[2] != key and a[2].get("rid") != key]

    def close_input(self):
        pass


def collect(provider, lines, ch=None):
    got, out, ch = [], Outcome(), ch or FakeChannel()
    for line in lines:
        provider.read(line, lambda k, d: got.append((k, d)), out, ch)
    return got, out


def test_claude_stream():
    p = ClaudeCode("claude", {})
    ch = FakeChannel()
    p.opening(ch, ch.turn)
    got, out = collect(p, [
        {"type": "control_response", "response": {"request_id": "eki-init", "subtype": "success"}},
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "opus"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/a.html", "content": "x"}},
            {"type": "text", "text": "hello"}]}},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.06, "resetsAt": 1790371200}}}},
        {"type": "result", "is_error": False, "usage": {"output_tokens": 3}},
    ], ch)
    kinds = [k for k, _ in got]
    assert kinds == ["session", "tool", "text", "quota", "usage"] and out.error == "" and out.finished
    assert got[1][1]["path"] == "/tmp/a.html"
    assert got[3][1]["windows"]["five_hour"]["used"] == 0.06
    assert ch.written[0]["request"]["subtype"] == "initialize"
    assert ch.written[1]["type"] == "user" and ch.written[1]["message"]["content"][0]["text"] == "hi"
    assert len(ch.written) == 2               # the turn is sent once, however it's prompted


def test_claude_question_and_answer():
    p = ClaudeCode("claude", {})
    ch = FakeChannel()
    q = {"questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]}
    collect(p, [{"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": q, "tool_use_id": "t1"}}], ch)
    kind, payload, key = ch.asked[0]
    assert kind == "question" and payload["questions"][0]["question"] == "Which?"
    p.answer(ch, key, {"allow": True, "answers": {"Which?": "B"}})
    resp = ch.written[-1]["response"]
    assert resp["request_id"] == "r1"
    assert resp["response"] == {"behavior": "allow", "updatedInput": {**q, "answers": {"Which?": "B"}},
                                "toolUseID": "t1"}


def test_claude_permission_denied_and_withdrawn():
    p = ClaudeCode("claude", {"permissions": "ask"})
    assert "default" in p.argv(Turn(prompt="x", history=[]))
    ch = FakeChannel()
    collect(p, [{"type": "control_request", "request_id": "r2", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "rm -rf build"},
        "permission_suggestions": [{"type": "addRules"}]}}], ch)
    kind, payload, key = ch.asked[0]
    assert kind == "permission" and payload["can_always"] and "rm -rf" in payload["detail"]
    p.answer(ch, key, {"allow": False})
    assert ch.written[-1]["response"]["response"]["behavior"] == "deny"
    collect(p, [{"type": "control_cancel_request", "request_id": "r2"}], ch)
    assert ch.asked == []


def test_claude_limit():
    got, out = collect(ClaudeCode("claude", {}), [
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1900000000}},
        {"type": "result", "is_error": True, "result": "Claude AI usage limit reached"}])
    assert out.reset_at == 1900000000 and "limit" in out.error


def test_claude_argv_resume_and_folder():
    p = ClaudeCode("claude", {"binary": "/bin/echo"})
    argv = p.argv(Turn(prompt="hi", history=[], resume="s1", cwd="/tmp",
                       extra={"claude_plugin": "/p", "claude_mcp": "/m.json"}))
    assert argv[0] == "/bin/echo" and "bypassPermissions" in argv
    for pair in (["--resume", "s1"], ["--add-dir", "/tmp"], ["--plugin-dir", "/p"], ["--mcp-config", "/m.json"]):
        i = argv.index(pair[0])
        assert argv[i + 1] == pair[1]


def test_codex_conversation():
    p = Codex("codex", {"binary": "/bin/echo"})
    ch = FakeChannel(Turn(prompt="go", history=[], resume="t0", cwd="/w"))
    p.opening(ch, ch.turn)
    assert ch.written[0]["method"] == "initialize"
    got, out = collect(p, [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": "t0"}}},
        {"id": 3, "result": {"turn": {"id": "u1"}}},
        {"method": "item/started", "params": {"item": {"type": "commandExecution", "command": "ls"}}},
        {"method": "item/agentMessage/delta", "params": {"delta": "do"}},
        {"method": "item/agentMessage/delta", "params": {"delta": "ne"}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "done"}}},
        {"method": "account/rateLimits/updated", "params": {"rateLimits": {
            "primary": {"usedPercent": 46, "windowDurationMins": 43200, "resetsAt": 1791819816}}}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ], ch)
    methods = [w.get("method") for w in ch.written]
    assert methods == ["initialize", "initialized", "thread/resume", "turn/start"]
    assert ch.written[2]["params"]["threadId"] == "t0" and ch.written[3]["params"]["input"][0]["text"] == "go"
    assert [k for k, _ in got] == ["session", "tool", "text", "quota"] and out.finished
    assert got[2][1]["text"] == "done" and got[3][1]["windows"]["thirty_day"]["used"] == 0.46


def test_codex_asks_and_auto_approves():
    p = Codex("codex", {})
    ch = FakeChannel()
    got, _ = collect(p, [{"id": 9, "method": "item/commandExecution/requestApproval",
                          "params": {"command": ["rm", "x"]}}], ch)
    assert ch.written[-1] == {"jsonrpc": "2.0", "id": 9, "result": {"decision": "accept"}}
    assert got[0][1]["name"] == "approved"
    collect(p, [{"id": 10, "method": "item/tool/requestUserInput", "params": {"questions": [
        {"id": "q", "question": "Which?", "options": [{"label": "A"}]}]}}], ch)
    kind, payload, key = ch.asked[0]
    p.answer(ch, key, {"allow": True, "answers": {"Which?": "A"}})
    assert ch.written[-1]["result"] == {"answers": {"q": {"answers": ["A"]}}}


def test_codex_failure():
    ch = FakeChannel()
    ch.state["calls"] = {3: "turn"}
    _, out = collect(Codex("codex", {}), [{"method": "turn/completed", "params": {"turn": {
        "error": {"message": "boom"}}}}], ch)
    assert out.error == "boom" and out.finished


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
