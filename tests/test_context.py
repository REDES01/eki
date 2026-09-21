# SPDX-License-Identifier: Apache-2.0
"""Context windows come from the model and the memory, not a catalog."""
from eki import context

HYBRID = {"num_hidden_layers": 64, "num_key_value_heads": 4, "num_attention_heads": 24,
          "head_dim": 256, "max_position_embeddings": 262144,
          "layer_types": ["linear_attention"] * 48 + ["full_attention"] * 16}
DENSE = {"num_hidden_layers": 28, "num_key_value_heads": 8, "num_attention_heads": 16,
         "head_dim": 128, "max_position_embeddings": 32768}


def test_hybrid_27b_gets_the_speed_cap_when_memory_allows():
    w = context.size(HYBRID, room_gb=12.0)
    assert w.tokens == 131072 and w.limited_by == "speed"
    assert w.kv_gb == 8.0 and w.native == 262144
    assert "128k context" in w.describe() and "native 256k" in w.describe()


def test_memory_limits_the_window():
    w = context.size(HYBRID, room_gb=4.0)      # 3 GB usable → 48k (3 GB) fits, 64k (4) doesn't
    assert w.tokens == 49152 and w.limited_by == "memory"
    assert "limited by this Mac's memory" in w.describe()


def test_native_limit_wins_for_a_small_model():
    w = context.size(DENSE, room_gb=40.0)
    assert w.tokens == 32768 and w.limited_by == "native"
    assert "the model's maximum" in w.describe()


def test_no_config_no_opinion():
    assert context.size({}, room_gb=40.0) is None


def test_harness_needs_room_for_its_own_prompt():
    assert context.harness_ready(49152)
    assert not context.harness_ready(32768)


def test_engine_sizes_the_window_from_the_model(tmp_path, monkeypatch):
    """A provider stored at 32k gets the window its config and memory allow,
    and the companion is handed that number."""
    from eki import secrets, settings
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    from eki.models import LocalModel, Memory, ModelManager
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    settings.save({"router_model": ""})
    from eki import profile
    monkeypatch.setattr(profile, "read", lambda repo: profile.from_files(repo, HYBRID, None)
                        if "27B" in repo else None)
    monkeypatch.setattr(ModelManager, "memory", lambda self: Memory(
        total_gb=48, ceiling_gb=37.4, committed_gb=14.5, free_gb=12.0))
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [
        BackendInfo(key="qwen", kind="mlx", label="Qwen 27B", capabilities=Capabilities(context_tokens=32000)),
        BackendInfo(key="codex", kind="codex", label="Codex", cost=Cost(tier=50),
                    capabilities=Capabilities(repo=True, tools=True)),
    ]
    cfg.options = {"qwen": {"base_url": "http://127.0.0.1:1", "model": "mlx-community/Qwen3.8-27B-4bit"},
                   "codex": {"binary": "/bin/echo"}}
    cfg.local_models = [LocalModel(key="qwen", label="Qwen", port=1, start="true", backend="qwen", gb=14.5)]
    eng = Engine(cfg, port=8799)
    # sized against the ceiling less its weights (37.4 − 14.5 = 22.9 GB), not
    # what's free this minute: the window mustn't flap as other models come and go
    p = eng.providers.get("qwen")
    assert p.capabilities["context_tokens"] == 131072
    assert p.runtime["context"]["limited_by"] == "speed"
    assert eng.get("codex-qwen").options["context_tokens"] == 131072
    assert eng.models.describe()[0]["context"]["tokens"] == 131072


def test_a_pinned_window_is_honoured_and_its_cost_said(tmp_path, monkeypatch):
    w = context.pinned(HYBRID, 65536, room_gb=12.0)
    assert w.tokens == 65536 and w.limited_by == "pinned" and w.fits
    assert "64k context · set by you · native 256k" in w.describe()
    big = context.pinned(HYBRID, 262144, room_gb=12.0)      # 16 GB of cache, 9 usable
    assert big.tokens == 262144 and not big.fits
    assert "the model's maximum" in big.describe() and "more than fits beside it now" in big.describe()
    assert context.pinned(HYBRID, 999999, room_gb=12.0).tokens == 262144     # never past native
    assert context.pinned({}, 65536, room_gb=12.0) is None


def test_engine_keeps_a_pin_across_reloads(tmp_path, monkeypatch):
    from eki import secrets, settings, profile
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    from eki.models import LocalModel, Memory, ModelManager
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    settings.save({"router_model": ""})
    monkeypatch.setattr(profile, "read", lambda repo: profile.from_files(repo, HYBRID, None))
    monkeypatch.setattr(ModelManager, "memory", lambda self: Memory(
        total_gb=48, ceiling_gb=37.4, committed_gb=14.5, free_gb=12.0))
    monkeypatch.setattr(LocalModel, "running", property(lambda self: True))
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [
        BackendInfo(key="qwen", kind="mlx", label="Qwen 27B", capabilities=Capabilities(context_tokens=32000)),
        BackendInfo(key="codex", kind="codex", label="Codex", cost=Cost(tier=50),
                    capabilities=Capabilities(repo=True, tools=True)),
    ]
    cfg.options = {"qwen": {"base_url": "http://127.0.0.1:1", "model": "mlx-community/Qwen3.8-27B-4bit"},
                   "codex": {"binary": "/bin/echo"}}
    cfg.local_models = [LocalModel(key="qwen", label="Qwen", port=1, start="true", backend="qwen", gb=14.5)]
    eng = Engine(cfg, port=8799)
    assert eng.providers.get("qwen").capabilities["context_tokens"] == 131072
    p = eng.providers.get("qwen")
    p.runtime["context_pin"] = 65536
    eng.providers.upsert(p)
    eng._build()
    p = eng.providers.get("qwen")
    assert p.capabilities["context_tokens"] == 65536 and p.runtime["context"]["limited_by"] == "pinned"
    assert eng.get("codex-qwen").options["context_tokens"] == 65536
    p.runtime.pop("context_pin")
    eng.providers.upsert(p)
    eng._build()
    assert eng.providers.get("qwen").capabilities["context_tokens"] == 131072
