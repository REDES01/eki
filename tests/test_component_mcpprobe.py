"""An mcp card's Test: mcpprobe shakes hands with a stdio or Streamable HTTP server and lists its
tools, fails clearly and leaves no process behind; the card's test route answers it unmasked-free."""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eki import apiroutes, server
from eki.components import mcpprobe

SECRET = "sk-very-secret-value-123"

SERVER = r'''
import json, sys
TOOLS = [[{"name": "roll", "description": "rolls a die"}], [{"name": "flip", "description": "flips a coin"}]]
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        res = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
               "serverInfo": {"name": "dice", "version": "0.1"}}
    elif msg["method"] == "tools/list":
        page = int((msg.get("params") or {}).get("cursor") or 0)
        res = {"tools": TOOLS[page]}
        if page + 1 < len(TOOLS):
            res["nextCursor"] = str(page + 1)
    else:
        res = {}
    print("a log line, not JSON", flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
'''

HANGS = r'''
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(f"{child.pid}\n")
time.sleep(60)
'''


def gone(pid, wait=5.0):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_stdio_server_starts_and_lists_its_tools_across_pages(tmp_path):
    script = tmp_path / "dice.py"
    script.write_text(SERVER)
    got = mcpprobe.probe({"command": sys.executable, "args": [str(script)], "env": {"TOKEN": SECRET}})
    assert got["ok"] and got["error"] == ""
    assert (got["name"], got["version"]) == ("dice", "0.1")
    assert got["tools"] == [{"name": "roll", "description": "rolls a die"},
                            {"name": "flip", "description": "flips a coin"}]
    assert 0 <= got["seconds"] < 15


def test_no_such_command():
    got = mcpprobe.probe({"command": "eki-no-such-mcp-server", "args": []})
    assert not got["ok"] and "no such command" in got["error"] and "eki-no-such-mcp-server" in got["error"]


def test_exits_at_once_with_stderr_tail_and_env_hidden():
    code = "import os, sys; sys.stderr.write('bad token ' + os.environ['TOKEN'] + '\\n'); sys.exit(3)"
    got = mcpprobe.probe({"command": sys.executable, "args": ["-c", code], "env": {"TOKEN": SECRET}})
    assert not got["ok"] and "bad token" in got["error"]
    assert "exited" in got["error"] or "closed" in got["error"]
    assert SECRET not in json.dumps(got)


def test_a_hanging_server_times_out_and_its_whole_group_is_killed(tmp_path):
    script, pidfile = tmp_path / "hang.py", tmp_path / "child.pid"
    script.write_text(HANGS)
    got = mcpprobe.probe({"command": sys.executable, "args": [str(script), str(pidfile)]}, timeout=1.5)
    assert not got["ok"] and "timed out" in got["error"] and "initialize" in got["error"]
    assert got["seconds"] < 10
    assert gone(int(pidfile.read_text()))


def test_bad_server_never_raises():
    assert "neither" in mcpprobe.probe({})["error"]
    assert mcpprobe.probe("nope")["ok"] is False


# ---- Streamable HTTP ----------------------------------------------------------------------------

class Mcp(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mcp.seen.append((msg.get("method"), self.headers.get("Mcp-Session-Id")))
        if "id" not in msg:
            self.send_response(202)
            self.end_headers()
            return
        if msg["method"] == "initialize":
            body = json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                               "result": {"serverInfo": {"name": "web", "version": "2"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "s-42")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.headers.get("Mcp-Session-Id") != "s-42":
            self.send_response(400)
            self.end_headers()
            return
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "fetch", "description": "gets"}]}}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n')
        self.wfile.write(("data: " + json.dumps(reply) + "\n\n").encode())
        self.wfile.flush()

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()


@pytest.fixture
def http_mcp():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Mcp.seen = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    srv.shutdown()


def test_a_url_server_over_json_and_event_stream_with_its_session(http_mcp):
    got = mcpprobe.probe({"url": http_mcp})
    assert got["ok"], got
    assert got["name"] == "web" and got["tools"] == [{"name": "fetch", "description": "gets"}]
    assert ("tools/list", "s-42") in Mcp.seen and ("notifications/initialized", "s-42") in Mcp.seen


def test_a_url_server_answering_an_http_error_or_not_there():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)   # no do_POST: 501
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = mcpprobe.probe({"url": f"http://127.0.0.1:{srv.server_address[1]}/"}, timeout=3)
    finally:
        srv.shutdown()
        srv.server_close()
    assert not got["ok"] and "HTTP 501" in got["error"] and "initialize" in got["error"]
    got = mcpprobe.probe({"url": f"http://127.0.0.1:{srv.server_address[1]}/"}, timeout=3)   # shut down now
    assert not got["ok"] and "can't reach" in got["error"]


# ---- the card's test route ----------------------------------------------------------------------

def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder)))
    conn.commit()


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def card(where, spec):
    where.mkdir(parents=True)
    (where / "component.json").write_text(json.dumps(spec))


@pytest.fixture
def cards(conn, proj, tmp_path):
    project(conn, proj)
    script = tmp_path / "dice.py"
    script.write_text(SERVER)
    base = proj / "components"
    card(base / "dice", {"kind": "mcp", "about": "dice", "server": {
        "command": sys.executable, "args": [str(script)], "env": {"TOKEN": SECRET}}})
    card(base / "notes", {"kind": "knowledge", "about": "notes", "files": ["README.md"], "read": "always"})
    return base


def test_the_route_answers_the_probe_and_never_an_env_value(conn, cards):
    got = call(conn, "POST", "/api/projects/p1/cards/dice/test")
    assert got["ok"] and [t["name"] for t in got["tools"]] == ["roll", "flip"]
    assert SECRET not in json.dumps(got)


def test_the_route_refuses_other_kinds_and_unknown_cards(conn, cards):
    with pytest.raises(ValueError, match="not an mcp card"):
        call(conn, "POST", "/api/projects/p1/cards/notes/test")
    with pytest.raises(KeyError):
        call(conn, "POST", "/api/projects/p1/cards/nope/test")


def test_the_route_is_not_the_card_edit_route():
    fn, _ = apiroutes.match("POST", "/api/projects/p1/cards/dice/test")
    assert fn.__module__ == "eki.api_cardmcp"


def test_a_pool_reference_card_is_tested_too(conn, proj, tmp_path):
    from eki import components
    project(conn, proj)
    script = tmp_path / "dice.py"
    script.write_text(SERVER)
    card(components.pool_dir() / "dice", {"kind": "mcp", "about": "dice", "server": {
        "command": sys.executable, "args": [str(script)], "env": {"TOKEN": SECRET}}})
    card(proj / "components" / "dice", {"pool": "dice"})
    got = call(conn, "POST", "/api/projects/p1/cards/dice/test")
    assert got["ok"] and SECRET not in json.dumps(got)


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_the_page_part_is_served(web):
    with urllib.request.urlopen(web + "/ui/cardmcp.js", timeout=10) as r:
        js = r.read().decode()
    assert "/test" in js and "projectCardKinds" in js and '"mcp"' in js and "cardmcp.css" in js
    with urllib.request.urlopen(web + "/ui/cardmcp.css", timeout=10) as r:
        assert r.status == 200 and ".cm-" in r.read().decode()
