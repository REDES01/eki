# SPDX-License-Identifier: Apache-2.0
"""A local model is described from its files, never typed in."""
import json

from eki import profile

CONFIG = {"model_type": "qwen3_5", "vision_config": {"depth": 27},
          "quantization": {"group_size": 64, "bits": 4, "mode": "affine"},
          "text_config": {"num_hidden_layers": 64, "num_key_value_heads": 4, "num_attention_heads": 24,
                          "head_dim": 256, "max_position_embeddings": 262144,
                          "layer_types": ["linear_attention"] * 48 + ["full_attention"] * 16}}


def _snapshot(tmp_path, template="{% if tools %}...{% endif %}{{ enable_thinking }}"):
    (tmp_path / "config.json").write_text(json.dumps(CONFIG))
    (tmp_path / "generation_config.json").write_text(json.dumps({"temperature": 1.0, "top_p": 0.95, "top_k": 20}))
    (tmp_path / "chat_template.jinja").write_text(template)
    (tmp_path / "model-00001-of-00002.safetensors").write_bytes(b"x" * (7 * 1024**3 // 1024))
    return tmp_path


def test_a_quantised_hybrid_build_is_read_whole(tmp_path):
    p = profile.from_files("mlx-community/Qwen3.8-27B-4bit", CONFIG, _snapshot(tmp_path))
    assert (p.base, p.family, p.params_b) == ("qwen3.8-27b", "qwen3_5", 27.0)
    assert (p.quant_bits, p.quant_group) == (4, 64)
    assert p.native_context == 262144 and p.hybrid and p.attention_layers == 16
    assert p.kv_per_token_kb == 64.0
    assert p.sampling == {"temperature": 1.0, "top_p": 0.95, "top_k": 20}
    assert p.tools and p.thinking_switch and p.vision_weights
    d = p.as_dict()
    assert "config" not in d and d["hybrid"] is True
    assert d["summary"].startswith("27B · 4-bit (group 64) · ")
    assert "hybrid · 16 of 64 layers keep a cache" in d["summary"]
    assert "tool calling" in d["summary"] and "thinking switch" in d["summary"]


def test_a_template_without_tools_is_said_so(tmp_path):
    p = profile.from_files("mlx-community/gemma-3-12b-it-4bit", CONFIG,
                           _snapshot(tmp_path, template="{{ messages }}"))
    assert not p.tools and not p.thinking_switch
    assert "no tool calling in its template" in p.describe()


def test_unquantised_and_unnamed_sizes():
    p = profile.from_files("someone/mystery-model", {"num_hidden_layers": 2, "num_attention_heads": 2,
                                                     "hidden_size": 8}, None)
    assert p.quant_bits == 0 and p.params_b == 0 and "16-bit" in p.describe()
    assert not p.hybrid


def test_params_from_name():
    assert profile.params_from_name("mlx-community/Qwen3.5-2B-MLX-4bit") == 2.0
    assert profile.params_from_name("mlx-community/Llama-3.3-70B-Instruct-8bit") == 70.0
    assert profile.params_from_name("mlx-community/gemma-3-12b-it-4bit") == 12.0
    assert profile.params_from_name("x/no-size-here") == 0.0


def test_read_returns_none_when_not_downloaded(monkeypatch):
    from eki import deploy
    monkeypatch.setattr(deploy, "snapshot_dir", lambda repo: None)
    assert profile.read("x/y") is None


def test_a_model_without_tool_calling_gets_no_companion(tmp_path, monkeypatch):
    from eki import secrets, settings
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    from eki.models import LocalModel, Memory, ModelManager
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    settings.save({"router_model": ""})
    snap = _snapshot(tmp_path, template="{{ messages }}")
    monkeypatch.setattr(profile, "read", lambda repo: profile.from_files(repo, CONFIG, snap))
    monkeypatch.setattr(ModelManager, "memory", lambda self: Memory(
        total_gb=48, ceiling_gb=37.4, committed_gb=0, free_gb=30.0))
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [
        BackendInfo(key="g", kind="mlx", label="Gemma", capabilities=Capabilities(context_tokens=32000)),
        BackendInfo(key="codex", kind="codex", label="Codex", cost=Cost(tier=50),
                    capabilities=Capabilities(repo=True, tools=True)),
    ]
    cfg.options = {"g": {"base_url": "http://127.0.0.1:1", "model": "mlx-community/gemma-3-12b-it-4bit"},
                   "codex": {"binary": "/bin/echo"}}
    cfg.local_models = [LocalModel(key="g", label="Gemma", port=1, start="true", backend="g", gb=0)]
    eng = Engine(cfg, port=8799)
    p = eng.providers.get("g")
    assert p.runtime["profile"]["quant_bits"] == 4 and p.runtime["gb"] > 0     # estimated from the weights
    assert p.capabilities["context_tokens"] == 131072                           # 30 GB free: the speed cap
    assert eng.get("codex-g") is None and "no tool calling" in eng.failed["codex-g"]
    assert eng.models.describe()[0]["profile"]["summary"].startswith("12B · 4-bit")
