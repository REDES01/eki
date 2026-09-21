# SPDX-License-Identifier: Apache-2.0
"""Model setup: names, fit estimates and the scripts eki writes."""
from eki import deploy
from eki import settings as settings_mod


def test_slug_drops_quant_suffixes():
    assert deploy.slug("mlx-community/Qwen3.5-2B-MLX-4bit") == "qwen3-5-2b"
    assert deploy.slug("mlx-community/gemma-3-12b-it-4bit") == "gemma-3-12b-it"


def test_kv_estimate_reads_nested_text_config():
    config = {"text_config": {"num_hidden_layers": 28, "num_attention_heads": 16,
                              "num_key_value_heads": 8, "head_dim": 128}}
    # 2 (k,v) * 28 layers * 8 heads * 128 dim * 2 bytes * 32768 tokens
    assert deploy.kv_gb(config, 32768) == round(2 * 28 * 8 * 128 * 2 * 32768 / 1024**3, 2)


def test_fit_verdicts():
    d = {"weights_gb": 10.0, "context": 8192, "config": {}}
    assert deploy.fit(d, 20.0, 30.0)["verdict"] == "fits"
    assert deploy.fit(d, 11.5, 11.5)["verdict"] == "tight"
    assert deploy.fit(d, 5.0, 5.0)["verdict"] == "too big"
    assert deploy.fit(d, 20.0, 30.0)["context"] == 8192              # no config: the smallest window


def test_fit_sizes_against_the_most_this_mac_can_give():
    """What's loaded this minute decides whether it can start now, never
    how much context it gets — a person can pin a smaller window later."""
    hybrid = {"num_hidden_layers": 64, "num_key_value_heads": 4, "num_attention_heads": 24,
              "head_dim": 256, "max_position_embeddings": 262144,
              "layer_types": ["linear_attention"] * 48 + ["full_attention"] * 16}
    d = {"weights_gb": 27.5, "context": 262144, "config": hybrid}       # the 8-bit 27B
    r = deploy.fit(d, free_gb=22.7, ceiling_gb=37.4)
    # 37.4 − 27.5 − 1.2 = 8.7 GB beside the weights → 6.5 usable → 96k (6 GB)
    assert r["context"] == 98304 and r["window"]["limited_by"] == "memory"
    assert r["need_gb"] == 34.7 and r["verdict"] == "tight" and r["fits"]
    assert not r["fits_now"]                                            # the 4-bit is loaded
    roomy = deploy.fit({**d, "weights_gb": 14.9}, free_gb=30.0, ceiling_gb=37.4)
    assert roomy["context"] == 131072 and roomy["fits_now"]


def test_sampling_only_what_was_published():
    assert deploy.sampling({"temperature": 0.6, "top_p": 0.95, "do_sample": True}) == \
        {"temperature": 0.6, "top_p": 0.95}
    assert deploy.sampling({}) == {}


def test_scripts_turn_thinking_off_and_bind_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "HOME", tmp_path)
    monkeypatch.setattr(settings_mod, "PATH", tmp_path / "settings.json")
    paths = deploy.write_scripts("m", "org/m-4bit", 8099, {"temperature": 0.7}, thinking=False)
    start = open(paths["start"]).read()
    assert "--host 127.0.0.1 --port 8099" in start
    assert "--temp 0.7" in start
    assert "enable_thinking" in start and "false" in start
    assert "8099" in open(paths["stop"]).read()


def test_kv_gb_counts_only_full_attention_layers_in_hybrids():
    config = {"num_hidden_layers": 64, "num_key_value_heads": 4, "num_attention_heads": 24,
              "head_dim": 256, "layer_types": ["linear_attention"] * 48 + ["full_attention"] * 16}
    # 16 layers × 4 heads × 256 × K+V × fp16 = 64 KB per token → 8 GB at 128k
    assert deploy.kv_gb(config, 131072) == 8.0
