"""Providers in the database, secrets out of it, and the two API adapters."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from hub import catalog, secrets
from hub.adapters.base import BackendInfo, Capabilities, Message, build
from hub.config import Config
from hub.engine import Engine
from hub.models import LocalModel, ModelManager
from hub.providers import Provider, ProviderStore, seed_from_config


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_keychain(monkeypatch):
    vault = {}
    monkeypatch.setattr(secrets, "get", lambda k: vault.get(k))
    monkeypatch.setattr(secrets, "put", lambda k, v: vault.__setitem__(k, v))
    monkeypatch.setattr(secrets, "delete", lambda k: vault.pop(k, None))
    return vault


@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.AsyncClient through a handler the test supplies."""
    holder = {}
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(holder["handler"])
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return holder


def test_api_key_never_lands_in_sqlite(tmp_path):
    store = ProviderStore(tmp_path / "hub.db")
    store.upsert(Provider(key="openai", kind="openai_compat", label="OpenAI",
                          options={"secret": True, "api_key": "sk-live-123",
                                   "base_url": "https://api.openai.com/v1"}))
    raw = sqlite3.connect(tmp_path / "hub.db").execute(
        "SELECT options FROM providers").fetchone()[0]
    assert "sk-live-123" not in raw
    assert json.loads(raw)["secret"] is True


def test_store_orders_and_updates(tmp_path):
    store = ProviderStore(tmp_path / "hub.db")
    store.upsert(Provider(key="a", kind="mlx", label="A"))
    store.upsert(Provider(key="b", kind="mlx", label="B"))
    store.upsert(Provider(key="a", kind="mlx", label="A2", enabled=False))
    got = store.all()
    assert [p.key for p in got] == ["a", "b"]
    assert got[0].label == "A2" and got[0].enabled is False
    assert store.delete("a") and not store.delete("a")


def _cfg(tmp_path):
    cfg = Config(db_path=str(tmp_path / "hub.db"))
    cfg.backends = [BackendInfo(key="qwen", kind="mlx", label="Qwen",
                                capabilities=Capabilities(context_tokens=32000))]
    cfg.options = {"qwen": {"base_url": "http://127.0.0.1:1"}}
    cfg.local_models = [LocalModel(key="qwen", label="Qwen", port=1,
                                   start="true", backend="qwen", gb=16)]
    return cfg


def test_seed_carries_runtime(tmp_path):
    seeded = seed_from_config(_cfg(tmp_path))
    assert seeded[0].runtime["port"] == 1 and seeded[0].runtime["gb"] == 16


def test_engine_seeds_once_then_reads_the_table(tmp_path, fake_keychain):
    eng = Engine(_cfg(tmp_path))
    assert [b.key for b in eng.backends] == ["qwen"]
    eng.providers.upsert(Provider(key="grok", kind="openai_compat", label="Grok",
                                  tier=100, options={"secret": True}))
    run(eng.reload())
    # no key in the Keychain → not built, and says why
    assert "grok" in eng.failed and eng.get("grok") is None
    fake_keychain["grok"] = "xai-key"
    run(eng.reload())
    assert eng.get("grok").options["api_key"] == "xai-key"
    run(eng.quota.stop())
    # a second engine on the same database doesn't re-seed over the edits
    again = Engine(_cfg(tmp_path))
    assert {p.key for p in again.providers.all()} == {"qwen", "grok"}


def test_stopped_but_startable_model_stays_a_candidate(tmp_path, fake_keychain):
    eng = Engine(_cfg(tmp_path))
    # port 1 is never listening; it has a start command and fits in memory
    assert eng._is_up("qwen") is None
    eng.models.ceiling_gb = 8           # now it would not fit
    assert eng._is_up("qwen") is False


def test_idle_reaper_only_touches_what_hub_started(monkeypatch):
    stopped = []
    mm = ModelManager([LocalModel(key="a", label="a", port=1, stop="x", idle_minutes=30),
                       LocalModel(key="b", label="b", port=2, stop="x", idle_minutes=30)])
    monkeypatch.setattr(LocalModel, "running", property(lambda self: True))

    async def fake_stop(key):
        stopped.append(key)
    monkeypatch.setattr(mm, "stop", fake_stop)
    mm.started = {"a"}
    mm.last_used = {"a": 0, "b": 0}
    run(mm.reap_idle(now=31 * 60))
    assert stopped == ["a"]                 # b was started by the user


def test_openai_compat_streams_and_sends_key(mock_http):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        frames = [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        ]
        text = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text)

    mock_http["handler"] = handler
    b = build(BackendInfo(key="oa", kind="openai_compat", label="oa"),
              {"base_url": "https://api.example/v1", "model": "m1", "api_key": "sk-x"})

    async def go():
        return [c async for c in b.stream([Message("user", "hi")])]

    assert "".join(run(go())) == "Hello"
    assert seen["auth"] == "Bearer sk-x" and seen["body"]["model"] == "m1"
    assert b.last_usage["completion_tokens"] == 2


def test_anthropic_api_picks_listed_model_and_streams(mock_http):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        assert request.headers["x-api-key"] == "ak"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "newest"}, {"id": "older"}]})
        body = json.loads(request.content)
        assert body["model"] == "newest" and body["system"] == "be brief"
        assert [m["role"] for m in body["messages"]] == ["user"]
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "!"}},
            {"type": "message_delta", "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=text)

    mock_http["handler"] = handler
    b = build(BackendInfo(key="an", kind="anthropic_api", label="an"), {"api_key": "ak"})

    async def go():
        return [c async for c in b.stream([Message("system", "be brief"),
                                           Message("user", "hi")])]

    assert "".join(run(go())) == "Hi!"
    assert calls == ["/v1/models", "/v1/messages"]
    assert b.last_usage == {"input_tokens": 7, "output_tokens": 2}


def test_templates_are_buildable():
    from hub.adapters.base import kinds
    ids = [t["id"] for t in catalog.TEMPLATES]
    assert len(ids) == len(set(ids))
    for t in catalog.TEMPLATES:
        assert t["kind"] in kinds(), t["id"]
