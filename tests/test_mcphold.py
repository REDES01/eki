"""`eki mcp-hold`: an MCP server behind a proxy that takes its card before the first tool call."""
import json
import os
import queue
import sys
import threading
import time

import pytest

from eki import store
from eki.cli import parser
from eki.components import mcphold

SERVER = """
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "tools/call":
        r = {"content": [{"type": "text", "text": "did " + m["params"]["name"]}]}
    else:
        r = {"method": m["method"]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
sys.exit(3)
"""


class Client:
    """The agent's side: write requests to the proxy, read its answers."""

    def __init__(self, tmp_path, run_id, monkeypatch, timeout=30.0):
        monkeypatch.setattr(mcphold, "ensure_engine", lambda: None)
        monkeypatch.setattr(mcphold, "POLL", 0.02)
        server = tmp_path / "server.py"
        server.write_text(SERVER)
        in_r, self.in_w = os.pipe()
        out_r, out_w = os.pipe()
        self.inp = os.fdopen(self.in_w, "wb")
        stdin, stdout = os.fdopen(in_r, "rb"), os.fdopen(out_w, "wb")
        self.answers = queue.Queue()
        self.code = queue.Queue()

        def proxy():
            self.code.put(mcphold.main([sys.executable, str(server)], card="ableton", run_id=run_id,
                                       folder=str(tmp_path), stdin=stdin, stdout=stdout, timeout=timeout))
            stdout.close()

        def read():
            for line in os.fdopen(out_r, "rb"):
                self.answers.put(json.loads(line))
        threading.Thread(target=proxy, daemon=True).start()
        threading.Thread(target=read, daemon=True).start()

    def send(self, rid, method, **params):
        self.inp.write((json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
                        + "\n").encode())
        self.inp.flush()

    def answer(self, wait=10):
        return self.answers.get(timeout=wait)

    def close(self):
        self.inp.close()
        return self.code.get(timeout=10)


def hold_runs(conn):
    return conn.execute("SELECT * FROM runs WHERE provider='component'").fetchall()


def wait_for_run(conn):
    for _ in range(500):
        rows = hold_runs(conn)
        if rows:
            return rows
        time.sleep(0.01)
    raise AssertionError("no hold run")


@pytest.fixture
def caller(conn, tmp_path):
    return store.create_run(conn, store.create_thread(conn, "agent", str(tmp_path)), "hi", provider="fake")


def test_listing_passes_through_without_a_run(conn, caller, tmp_path, monkeypatch):
    c = Client(tmp_path, caller, monkeypatch)
    c.send(1, "initialize")
    assert c.answer() == {"jsonrpc": "2.0", "id": 1, "result": {"method": "initialize"}}
    c.send(2, "tools/list")
    assert c.answer()["result"] == {"method": "tools/list"}
    assert hold_runs(conn) == []
    assert c.close() == 3                          # the server's own exit code


def test_first_tool_call_holds_the_card_then_goes_on(conn, caller, tmp_path, monkeypatch):
    c = Client(tmp_path, caller, monkeypatch)
    c.send(1, "tools/call", name="play")
    [r] = wait_for_run(conn)
    assert r["priority"] == "now" and r["parent"] == caller
    assert json.loads(r["prompt"]) == {"component": "ableton", "args": [], "caller_dir": str(tmp_path),
                                       "caller_run": caller, "hold": True}
    c.send(2, "tools/list")                        # other messages flow while it waits
    assert c.answer()["id"] == 2
    store.update_run(conn, r["id"], state="done")
    assert c.answer() == {"jsonrpc": "2.0", "id": 1,
                          "result": {"content": [{"type": "text", "text": "did play"}]}}
    c.send(3, "tools/call", name="stop")           # held: never asks again
    assert c.answer()["result"]["content"][0]["text"] == "did stop"
    assert len(hold_runs(conn)) == 1
    c.close()


def test_a_failed_hold_answers_with_the_reason_and_the_next_call_tries_again(conn, caller, tmp_path,
                                                                             monkeypatch):
    c = Client(tmp_path, caller, monkeypatch)
    c.send(1, "tools/call", name="play")
    [r] = wait_for_run(conn)
    store.update_run(conn, r["id"], state="failed", error="waits for ableton: held by r-other")
    a = c.answer()
    assert a["id"] == 1 and "result" not in a
    assert "waits for ableton: held by r-other" in a["error"]["message"]
    c.send(2, "tools/call", name="play")
    for _ in range(500):
        if len(hold_runs(conn)) == 2:
            break
        time.sleep(0.01)
    second = [x for x in hold_runs(conn) if x["id"] != r["id"]][0]
    store.update_run(conn, second["id"], state="done")
    assert c.answer()["result"]["content"][0]["text"] == "did play"
    c.close()


def test_no_run_relays_without_holding(conn, tmp_path, monkeypatch):
    c = Client(tmp_path, "", monkeypatch)
    c.send(1, "tools/call", name="play")
    assert c.answer()["result"]["content"][0]["text"] == "did play"
    assert hold_runs(conn) == []
    c.close()


def test_acquire_times_out_with_the_reason(conn, caller, tmp_path, monkeypatch):
    monkeypatch.setattr(mcphold, "ensure_engine", lambda: None)
    real = store.create_run

    def waiting(*a, **k):
        rid = real(*a, **k)
        store.update_run(conn, rid, why="waits for ableton: held by r-other")
        return rid
    monkeypatch.setattr(store, "create_run", waiting)
    ok, why = mcphold.acquire(conn, "ableton", str(tmp_path), caller, timeout=0.1, poll=0.02)
    assert not ok and "not held after 0.1s" in why and "held by r-other" in why
    [r] = hold_runs(conn)
    assert r["state"] == "cancelled"               # nobody waits for it any more


def test_the_command_is_registered():
    args = parser().parse_args(["mcp-hold", "--run", "r1", "--dir", "/x", "ableton", "--",
                                "uvx", "ableton-mcp"])
    assert args.card == "ableton" and args.run == "r1" and args.dir == "/x"
    assert [a for a in args.command if a != "--"] == ["uvx", "ableton-mcp"]
