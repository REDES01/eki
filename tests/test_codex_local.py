"""Rung 2: Codex's harness on the local model, handing off to Claude when it can't."""
import json
import sys
import textwrap

import pytest

from eki import db, models, observe, providers, score, store, worker
from eki.providers import codex_local
from eki.providers.codex_local import CodexLocal
from eki.routing import table

from conftest import run_inline

#: a scripted `codex app-server`: CODEX_MODE says how the turn goes
SCRIPT = textwrap.dedent('''
    import json, os, sys
    open(os.environ["CODEX_ARGV"], "w").write(json.dumps(sys.argv[1:]))
    mode = os.environ.get("CODEX_MODE", "ok")
    def say(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        m, rid = msg.get("method"), msg.get("id")
        if m == "initialize":
            say({"id": rid, "result": {}})
        elif m == "thread/start":
            open(os.environ["CODEX_ARGV"] + ".thread", "w").write(json.dumps(msg["params"]))
            say({"id": rid, "result": {"thread": {"id": "th1"}}})
        elif m == "turn/start":
            say({"id": rid, "result": {"turn": {"id": "u1"}}})
            if mode == "hang":
                continue
            if mode == "fail":
                say({"method": "turn/completed", "params": {"turn": {"error": {"message": "endpoint 503"}}}})
                continue
            text = {"ok": "renamed it", "handoff": "tried a bit\\nHANDOFF: needs a redesign"}[mode]
            say({"method": "item/started", "params": {"item": {"type": "agentMessage"}}})
            say({"method": "item/agentMessage/delta", "params": {"delta": text}})
            say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": text}}})
            say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        elif m == "turn/interrupt":
            say({"id": rid, "result": {}})
            say({"method": "turn/completed", "params": {"turn": {"status": "interrupted"}}})
''')


@pytest.fixture
def rung2(home, tmp_path, monkeypatch):
    """A local entry, a scripted Codex, `claude` a fake that takes the handoff."""
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\n{SCRIPT}")
    binary.chmod(0o755)
    monkeypatch.setenv("CODEX_ARGV", str(tmp_path / "argv.json"))
    cfg = json.loads((home / "providers.json").read_text())
    cfg.update({"lm": {"kind": "local", "base_url": "http://127.0.0.1:1", "model": "qwen-small"},
                "claude": {"kind": "fake"},
                "codex-local": {"kind": "codex_local", "local": "lm", "binary": str(binary),
                                "context_tokens": 16384, "handoff_after": 20}})
    (home / "providers.json").write_text(json.dumps(cfg))
    rt = json.loads((home / "routing.json").read_text())
    for r in rt["rows"]:
        if r["key"] == "code":
            r["targets"] = ["claude"]
    (home / "routing.json").write_text(json.dumps(rt))
    ensured = []
    monkeypatch.setattr(models, "ensure", lambda name, timeout=180: ensured.append(name) or True)
    return {"argv": tmp_path / "argv.json", "ensured": ensured}


def _run(conn, prompt="rename parse to parse_line", tid=None):
    tid = tid or store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, prompt, provider="codex-local", row="code-easy")
    return tid, run_inline(conn, rid)


def test_argv_carries_the_provider_and_the_model(conn, rung2, monkeypatch):
    monkeypatch.setenv("EKI_PORT", "7790")
    _, r = _run(conn)
    assert r["state"] == "done" and rung2["ensured"] == ["lm"]
    argv = json.loads(rung2["argv"].read_text())
    flags = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    for want in ('model_provider="eki_local"',
                 'model_providers.eki_local.base_url="http://127.0.0.1:7790/v1"',
                 'model_providers.eki_local.wire_api="responses"',
                 'model_providers.eki_local.http_headers={"X-Eki"="1"}',
                 f"model_providers.eki_local.stream_max_retries={codex_local.STREAM_RETRIES}",
                 "model_context_window=16384"):
        assert want in flags
    assert argv[argv.index("-m") + 1] == "qwen-small" and argv[-1] == "app-server"
    thread = json.loads((rung2["argv"].parent / "argv.json.thread").read_text())
    assert thread["model"] == "qwen-small" and "HANDOFF:" in thread["developerInstructions"]


def test_handoff_line_goes_to_claude_with_the_transcript(conn, rung2, monkeypatch):
    monkeypatch.setenv("CODEX_MODE", "handoff")
    tid = store.create_thread(conn, "t", None)
    first = store.create_run(conn, tid, "what does parse do", provider="fake")
    assert run_inline(conn, first)["state"] == "done"
    tid, r = _run(conn, tid=tid)
    assert r["state"] == "handed_off"
    nxt = conn.execute("SELECT * FROM runs WHERE parent=?", (r["id"],)).fetchone()
    assert nxt["row"] == "code" and "codex-local" in store.excluded(nxt)
    assert "needs a redesign" in nxt["why"] and "files are left" in nxt["why"]
    turn = worker.build_turn(conn, nxt, "claude")
    done = run_inline(conn, nxt["id"])
    assert done["provider"] == "claude" and done["state"] == "done"
    assert [m["content"] for m in turn.history][:1] == ["what does parse do"]   # the thread so far


def test_a_failed_turn_hands_off(conn, rung2, monkeypatch):
    monkeypatch.setenv("CODEX_MODE", "fail")
    _, r = _run(conn)
    assert r["state"] == "handed_off"
    nxt = conn.execute("SELECT why FROM runs WHERE parent=?", (r["id"],)).fetchone()
    assert "endpoint 503" in nxt["why"]


def test_overtime_interrupts_then_hands_off(home, conn, rung2, monkeypatch):
    monkeypatch.setenv("CODEX_MODE", "hang")
    cfg = json.loads((home / "providers.json").read_text())
    cfg["codex-local"]["handoff_after"] = 0.02            # a little over a second
    (home / "providers.json").write_text(json.dumps(cfg))
    _, r = _run(conn)
    assert r["state"] == "handed_off"
    assert "ran past" in conn.execute("SELECT why FROM runs WHERE parent=?", (r["id"],)).fetchone()[0]


def test_local_model_not_up_hands_off(conn, rung2, monkeypatch):
    monkeypatch.setattr(models, "ensure", lambda name, timeout=180: False)
    _, r = _run(conn)
    assert r["state"] == "handed_off"


def test_handoff_line():
    assert codex_local.handoff_in("done\nHANDOFF: too big") == "too big"
    assert codex_local.handoff_in("all done") is None


def test_in_use_sees_the_local_model_busy(conn, rung2):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "x", provider="codex-local")
    assert not models.in_use(conn, "lm")
    store.update_run(conn, rid, state="running")
    assert models.in_use(conn, "lm") and not models.in_use(conn, "fake")


def test_default_entry_only_with_codex_and_a_local_model(home, monkeypatch):
    monkeypatch.setattr(providers, "codex_installed", lambda cfg: True)
    assert "codex-local" not in providers.config()        # no local entry
    cfg = json.loads((home / "providers.json").read_text())
    cfg["lm"] = {"kind": "local", "base_url": "http://127.0.0.1:1"}
    (home / "providers.json").write_text(json.dumps(cfg))
    got = providers.config()["codex-local"]
    assert got["kind"] == "codex_local" and got["local"] == "lm"
    assert "codex-local" not in json.loads((home / "providers.json").read_text())   # in memory only
    assert isinstance(providers.get("codex-local"), CodexLocal)
    assert providers.capabilities("codex-local", got) == ["text", "tools"]
    monkeypatch.setattr(providers, "codex_installed", lambda cfg: False)
    assert "codex-local" not in providers.config()        # no Codex
    monkeypatch.setattr(providers, "codex_installed", lambda cfg: True)
    cfg["codex-local"] = {"kind": "codex_local", "local": "lm", "handoff_after": 5}
    (home / "providers.json").write_text(json.dumps(cfg))
    assert providers.config()["codex-local"]["handoff_after"] == 5     # yours wins


def test_code_easy_row_only_with_codex_local(home, rung2, monkeypatch):
    monkeypatch.setattr(providers, "codex_installed", lambda cfg: False)
    easy = [r for r in table.rows() if r["key"] == "code-easy"]
    assert easy and easy[0]["targets"] == ["codex-local", "claude", "codex"] and easy[0]["needs"] == ["tools"]
    assert "code-easy" not in json.dumps(json.loads((home / "routing.json").read_text()))
    assert table.KIND_ORDER["codex_local"] == 0
    cfg = json.loads((home / "providers.json").read_text())
    del cfg["codex-local"]
    (home / "providers.json").write_text(json.dumps(cfg))
    assert not [r for r in table.rows() if r["key"] == "code-easy"]


def test_code_easy_row_you_wrote_wins(home, rung2):
    rt = json.loads((home / "routing.json").read_text())
    rt["rows"].append({"key": "code-easy", "title": "mine", "targets": ["claude"]})
    (home / "routing.json").write_text(json.dumps(rt))
    assert [r["title"] for r in table.rows() if r["key"] == "code-easy"] == ["mine"]


def test_finished_codex_local_run_counts_as_local(conn, rung2):
    tid = store.create_thread(conn, "t", None)
    for state in ("done", "handed_off"):
        rid = store.create_run(conn, tid, "x", provider="codex-local")
        store.update_run(conn, rid, state=state, ended_at=db.now())
        observe.run_ended(conn, rid)
    got = score.compute(conn, 0)
    assert got["runs"] == 2 and got["local_share"] == 0.5
