# SPDX-License-Identifier: Apache-2.0
"""Providers in the database, secrets out of it, and the two API adapters."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from eki import catalog, secrets
from eki.adapters.base import BackendInfo, Capabilities, Message, build
from eki.config import Config
from eki.engine import Engine
from eki.models import LocalModel, ModelManager
from eki.providers import Provider, ProviderStore, seed_from_config


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
    store = ProviderStore(tmp_path / "eki.db")
    store.upsert(Provider(key="openai", kind="openai_compat", label="OpenAI",
                          options={"secret": True, "api_key": "sk-live-123",
                                   "base_url": "https://api.openai.com/v1"}))
    raw = sqlite3.connect(tmp_path / "eki.db").execute(
        "SELECT options FROM providers").fetchone()[0]
    assert "sk-live-123" not in raw
    assert json.loads(raw)["secret"] is True


def test_store_orders_and_updates(tmp_path):
    store = ProviderStore(tmp_path / "eki.db")
    store.upsert(Provider(key="a", kind="mlx", label="A"))
    store.upsert(Provider(key="b", kind="mlx", label="B"))
    store.upsert(Provider(key="a", kind="mlx", label="A2", enabled=False))
    got = store.all()
    assert [p.key for p in got] == ["a", "b"]
    assert got[0].label == "A2" and got[0].enabled is False
    assert store.delete("a") and not store.delete("a")


def _cfg(tmp_path):
    cfg = Config(db_path=str(tmp_path / "eki.db"))
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


def test_stopped_but_startable_model_stays_a_candidate(tmp_path, fake_keychain, monkeypatch):
    from eki import memory
    # whatever this Mac has loaded right now: the test's Mac has room
    monkeypatch.setattr(memory, "snapshot",
                        lambda: memory.Snapshot(total_gb=64, available_gb=40, used_gb=20, level=90))
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


def test_a_server_eki_launched_but_forgot_is_taken_back(monkeypatch):
    """The record said nothing (a reload lost it); the process says eki."""
    from eki import models
    stopped = []
    mm = ModelManager([LocalModel(key="a", label="a", port=1, stop="x", idle_minutes=30),
                       LocalModel(key="b", label="b", port=2, stop="x", idle_minutes=30)])
    monkeypatch.setattr(LocalModel, "running", property(lambda self: True))
    monkeypatch.setattr(models, "started_by_eki", lambda port: port == 1)

    async def fake_stop(key):
        stopped.append(key)
    monkeypatch.setattr(mm, "stop", fake_stop)
    assert mm.started == set()
    run(mm.reap_idle(now=1000))
    assert stopped == [] and mm.started == {"a"}        # found, with a fresh window
    run(mm.reap_idle(now=1000 + 31 * 60))
    assert stopped == ["a"]                             # b is yours: never


def test_a_reload_shares_who_started_what_with_the_old_manager():
    old = ModelManager([LocalModel(key="a", label="a", port=1, stop="x")])
    new = ModelManager([LocalModel(key="a", label="a", port=1, stop="x")])
    new.adopt(old)
    old.started.add("a")                    # a start that was still in flight on the old one
    old.last_used["a"] = 5.0
    assert new.started == {"a"} and new.last_used["a"] == 5.0


@pytest.mark.real_processes
def test_eki_knows_its_own_server_by_the_process(tmp_path):
    import os, shutil, socket, subprocess, sys, time
    from eki import models
    if not shutil.which("lsof"):
        pytest.skip("no lsof")
    ports, procs = [], []
    for env in ({**os.environ, "EKI_STARTED": "1"}, {k: v for k, v in os.environ.items()
                                                     if k != "EKI_STARTED"}):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        procs.append(subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                                       "--bind", "127.0.0.1"], env=env, cwd=tmp_path,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        ports.append(port)
    try:
        for _ in range(50):
            if all(models.port_open(p) for p in ports):
                break
            time.sleep(0.1)
        assert models.started_by_eki(ports[0]) is True
        assert models.started_by_eki(ports[1]) is False
        assert models.started_by_eki(1) is False        # nothing there
    finally:
        for p in procs:
            p.terminate()


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
    from eki.adapters.base import kinds
    ids = [t["id"] for t in catalog.TEMPLATES]
    assert len(ids) == len(set(ids))
    for t in catalog.TEMPLATES:
        assert t["kind"] in kinds(), t["id"]


def test_started_models_survive_an_engine_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(ModelManager, "STARTED_FILE", tmp_path / "started.json")
    monkeypatch.setattr(LocalModel, "running", property(lambda self: True))
    first = ModelManager([LocalModel(key="a", label="a", port=1, start="x", stop="y")])
    first.started.add("a")
    first.touch("a")
    first._save_started()
    # a new engine on the same Mac still knows it owns that server
    second = ModelManager([LocalModel(key="a", label="a", port=1, start="x", stop="y")])
    assert second.started == {"a"}


def test_memory_ladder_uses_the_os_not_just_the_weights(monkeypatch):
    from eki import memory
    from eki.models import LocalModel, ModelManager
    big = LocalModel(key="big", label="big", port=1, start="x", stop="y", gb=16)
    small = LocalModel(key="small", label="small", port=2, start="x", stop="y", gb=3)
    mm = ModelManager([big, small], ceiling_gb=37.4)
    monkeypatch.setattr(LocalModel, "running", property(lambda self: False))
    # weights would fit under the ceiling, but Docker took the memory
    monkeypatch.setattr(memory, "snapshot", lambda: memory.Snapshot(
        total_gb=48, available_gb=10, used_gb=38, level=20))
    assert mm.can_start("big") is False           # 10 available − 4 reserve < 16
    assert mm.can_start("small") is True          # 3 fits in the 6 of headroom
    ok, why = mm.fits(big)
    assert not ok and "available" in why


def test_make_room_only_unloads_what_eki_started(monkeypatch):
    from eki import memory
    from eki.models import LocalModel, ModelManager
    mine = LocalModel(key="mine", label="mine", port=1, start="x", stop="y", gb=14)
    theirs = LocalModel(key="theirs", label="theirs", port=2, start="x", stop="y", gb=14)
    want = LocalModel(key="want", label="want", port=3, start="x", stop="y", gb=12)
    mm = ModelManager([mine, theirs, want], ceiling_gb=37.4)
    running = {"mine", "theirs"}
    monkeypatch.setattr(LocalModel, "running", property(lambda self: self.key in running))
    mm.started = {"mine"}                          # theirs was started by hand
    mm.last_used = {"mine": 0, "theirs": 0}
    stopped = []

    async def fake_stop(key):
        stopped.append(key); running.discard(key)
    monkeypatch.setattr(mm, "stop", fake_stop)
    # 20 GB available; after mine goes, want's 12 fits under headroom and ceiling
    monkeypatch.setattr(memory, "snapshot", lambda: memory.Snapshot(
        total_gb=48, available_gb=12 if "mine" in running else 26, used_gb=0, level=50))
    assert mm.can_start("want") is True            # reclaimable counts
    asyncio.run(mm.make_room(12))
    assert stopped == ["mine"] and "theirs" in running


def test_pressure_shortens_the_idle_window(monkeypatch):
    from eki.models import LocalModel, ModelManager
    m = LocalModel(key="a", label="a", port=1, stop="x", idle_minutes=30)
    mm = ModelManager([m])
    monkeypatch.setattr(LocalModel, "running", property(lambda self: True))
    stopped = []

    async def fake_stop(key):
        stopped.append(key)
    monkeypatch.setattr(mm, "stop", fake_stop)
    mm.started = {"a"}
    mm.last_used = {"a": 0}
    asyncio.run(mm.reap_idle(now=5 * 60, pressure="normal"))
    assert stopped == []                           # five minutes idle: fine normally
    asyncio.run(mm.reap_idle(now=5 * 60, pressure="warning"))
    assert stopped == ["a"]                        # under pressure: gone
    mm.hold("a"); mm.started = {"a"}; stopped.clear()
    asyncio.run(mm.reap_idle(now=99 * 60, pressure="critical"))
    assert stopped == []                           # never from under a run


# ---- unloading to make room: who goes, and for whom -------------------------

def _crowded(monkeypatch, tmp_path):
    """Three eki-started servers up — one long idle, one used a moment ago,
    one pinned — and the memory a fourth would need only if they go."""
    import time
    from eki import memory
    from eki.models import LocalModel, ModelManager
    monkeypatch.setattr(ModelManager, "STARTED_FILE", tmp_path / "started.json")
    stale = LocalModel(key="stale", label="stale", port=1, start="x", stop="y", gb=6)
    fresh = LocalModel(key="fresh", label="fresh", port=2, start="x", stop="y", gb=14)
    pinned = LocalModel(key="pinned", label="pinned", port=3, start="x", stop="y",
                        gb=8, idle_minutes=0)
    want = LocalModel(key="want", label="want", port=4, start="x", stop="y", gb=18)
    mm = ModelManager([stale, fresh, pinned, want], ceiling_gb=37.4)
    running = {"stale", "fresh", "pinned"}
    monkeypatch.setattr(LocalModel, "running", property(lambda self: self.key in running))
    mm.started = set(running)
    now = time.time()
    mm.last_used = {"stale": now - 3600, "fresh": now - 20, "pinned": now - 3600}
    stopped = []

    async def fake_stop(key):
        stopped.append(key); running.discard(key)
    monkeypatch.setattr(mm, "stop", fake_stop)
    sizes = {m.key: m.gb for m in (stale, fresh, pinned)}
    monkeypatch.setattr(memory, "snapshot", lambda: memory.Snapshot(
        total_gb=48, available_gb=38 - sum(sizes[k] for k in running), used_gb=0, level=50))
    return mm, stopped


def test_background_work_only_takes_long_idle_unpinned_servers(monkeypatch, tmp_path):
    mm, stopped = _crowded(monkeypatch, tmp_path)
    assert [m.key for m in mm.evictable()] == ["stale"]
    assert mm.can_start("want") is False           # stale's 6 GB isn't enough
    asyncio.run(mm.make_room(18))
    assert stopped == ["stale"]                    # and it took nothing else


def test_a_waiting_user_outranks_a_resting_model_and_pins_go_last(monkeypatch, tmp_path):
    mm, stopped = _crowded(monkeypatch, tmp_path)
    assert [m.key for m in mm.evictable(eager=True)] == ["stale", "fresh", "pinned"]
    assert mm.can_start("want", eager=True) is True
    asyncio.run(mm.make_room(18, eager=True))
    assert stopped == ["stale", "fresh"]           # enough: the pin survives


def test_a_server_mid_answer_is_never_unloaded(monkeypatch, tmp_path):
    mm, stopped = _crowded(monkeypatch, tmp_path)
    mm.hold("fresh")
    assert "fresh" not in [m.key for m in mm.evictable(eager=True)]
    asyncio.run(mm.make_room(40, eager=True))
    assert "fresh" not in stopped


def test_pin_is_left_by_the_idle_timer_and_says_when_others_unload(monkeypatch, tmp_path):
    mm, stopped = _crowded(monkeypatch, tmp_path)
    import time
    assert mm.unloads_at("pinned") is None
    assert abs(mm.unloads_at("fresh") - (mm.last_used["fresh"] + 15 * 60)) < 1
    asyncio.run(mm.reap_idle(now=time.time() + 6 * 3600, pressure="normal"))
    assert sorted(stopped) == ["fresh", "stale"]
    rows = {r["key"]: r for r in mm.describe()}
    assert rows["pinned"]["pinned"] is True and rows["pinned"]["unloads_at"] is None


def test_holds_survive_an_engine_reload(monkeypatch, tmp_path):
    from eki.models import ModelManager
    mm, _ = _crowded(monkeypatch, tmp_path)
    mm.hold("fresh")
    again = ModelManager(list(mm.models.values()), ceiling_gb=37.4)
    again.adopt(mm)
    assert again.busy("fresh")
