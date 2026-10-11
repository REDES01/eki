"""A service card can offer an MCP server: project runs get it, and using it holds the card."""
import json
import os
import sys
from pathlib import Path

import pytest

from conftest import run_inline
from eki import components, db, store, worker
from eki.components import access, mcpserver, service
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex

EKI_DIR = str(Path(service.__file__).resolve().parents[2])


def _card(root, name, **spec):
    d = root / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": name, **spec}))
    return d


def _turn(conn, cwd):
    tid = store.create_thread(conn, "t", str(cwd))
    rid = store.create_run(conn, tid, "hi")
    return worker.build_turn(conn, store.run(conn, rid), "fake")


def _caller(conn, folder):
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "caller", str(folder)), "do it", provider="fake")
    store.update_run(conn, rid, state="running", started_at=db.now())
    return rid


def _use(conn, name, parent, folder, hold=False):
    call = {"component": name, "args": [], "caller_dir": str(folder), "caller_run": parent}
    if hold:
        call["hold"] = True
    with db.tx(conn):
        return store.create_run(conn, store.create_thread(conn, f"use {name}", None), json.dumps(call),
                                provider="component", parent=parent)


def test_run_config_wraps_the_server_so_using_it_holds_the_card(conn, proj, monkeypatch):
    monkeypatch.setenv("EKI_HOME", os.environ.get("EKI_HOME") or str(proj.parent / "h"))
    _card(proj, "ableton", run=["node", "auto.mjs"], access="exclusive",
          mcp={"command": "uvx", "args": ["ableton-mcp"], "env": {"K": "v"}})
    t = _turn(conn, proj)
    argv = ClaudeCode("claude", {"kind": "claude_code"}).argv(t)
    f = argv[argv.index("--mcp-config") + 1]
    got = json.loads(Path(f).read_text())["mcpServers"]["ableton"]
    assert got["command"] == sys.executable
    assert got["args"] == ["-m", "eki", "mcp-hold", "--run", t.run_id, "--dir", str(proj.resolve()),
                           "ableton", "--", "uvx", "ableton-mcp"]
    assert got["env"] == {"K": "v", "PYTHONPATH": EKI_DIR, "EKI_HOME": os.environ["EKI_HOME"]}
    flags = Codex("codex", {}).argv(t)
    assert f'mcp_servers.ableton.command={json.dumps(sys.executable)}' in flags
    assert any("mcp-hold" in a and "mcp_servers.ableton.args" in a for a in flags)
    # without a run it is the plain server
    assert mcpserver.servers_of(proj)["ableton"] == {"command": "uvx", "args": ["ableton-mcp"], "env": {"K": "v"}}


def test_url_server_passes_through_unwrapped(conn, proj):
    _card(proj, "live", run=["x"], mcp={"url": "https://live.example/mcp"})
    assert mcpserver.servers_of(proj, run_id="r1") == {"live": {"type": "http", "url": "https://live.example/mcp"}}
    comp = components.get("live", str(proj), conn)
    assert "MCP over url: https://live.example/mcp, not held" in service.notes(comp)


def test_validation_and_notes(proj, conn):
    ok = {"kind": "service", "run": ["x"], "mcp": {"command": "uvx", "args": ["ableton-mcp"]}}
    assert service.validate(ok, proj) == []
    assert service.validate({"mcp": {"command": "uvx"}}, proj)           # still needs a call form
    assert any(e.startswith('"mcp":') for e in service.validate({"run": ["x"], "mcp": {"args": [1]}}, proj))
    assert service.validate({"run": ["x"], "mcp": {"url": "ftp://x"}}, proj)
    assert service.validate({"run": ["x"], "mcp": "uvx"}, proj)
    _card(proj, "bad", run=["x"], mcp={"command": ""})
    with pytest.raises(KeyError):
        components.get("bad", str(proj), conn)
    _card(proj, "good", run=["x"], mcp={"command": "uvx", "args": ["ableton-mcp"]})
    assert "MCP: uvx ableton-mcp" in service.notes(components.get("good", str(proj), conn))


@pytest.mark.parametrize("second_hold", [False, True])
def test_hold_call_holds_the_card_like_a_script_call(conn, proj, second_hold):
    _card(proj, "ableton", run=["true"], access="exclusive", mcp={"command": "uvx", "args": ["ableton-mcp"]})
    _card(proj, "voice", run=["true"], conflicts=["ableton"])
    a = _caller(conn, proj)
    rid = _use(conn, "ableton", a, proj, hold=True)
    assert access.gate(conn, store.run(conn, rid)) == (True, "")
    r = run_inline(conn, rid)
    assert r["state"] == "done"
    assert store.answer(conn, rid).strip() == f"ableton: held for {a} (MCP)"
    assert access.holders(conn, "ableton") == [a]
    b = _caller(conn, proj)
    other = _use(conn, "ableton", b, proj, hold=second_hold)
    assert access.gate(conn, store.run(conn, other)) == (False, f"waits for ableton: held by {a}")
    v = _use(conn, "voice", b, proj)
    ok, why = access.gate(conn, store.run(conn, v))
    assert not ok and why == f"waits for voice: conflicts with ableton, held by {a}"
    store.update_run(conn, a, state="done", ended_at=db.now())
    assert access.holders(conn, "ableton") == []
    assert access.gate(conn, store.run(conn, other)) == (True, "")
