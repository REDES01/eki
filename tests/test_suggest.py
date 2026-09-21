# SPDX-License-Identifier: Apache-2.0
"""Model suggestions: the boards' quality × the catalogue's builds × this Mac."""
import asyncio

import pytest

from eki import suggest


def test_rollup_ignores_easy_slots_and_groups_the_rest():
    got = suggest._rollup({"math/easy": 1.0, "math/hard": 0.6, "code/hard": 0.7,
                           "repo/medium": 0.5, "chat/hard": 0.8})
    assert got == {"code": 0.6, "chat": 0.8, "math": 0.6, "overall": 0.67}
    assert suggest._rollup({"math/easy": 1.0}) == {}


def test_weights_from_the_hubs_parameter_counts():
    row = {"safetensors": {"parameters": {"U32": 26_893_352_960, "BF16": 463_375_600}}}
    assert suggest.weights_gb(row, 4) == 15.0          # the real file is 14.97 GB
    assert suggest.weights_gb(row, 8) == 27.5
    assert suggest._bits({"id": "mlx-community/Qwen3.8-27B-4bit"}) == 4
    assert suggest._bits({"id": "mlx-community/Qwen3.5-27B-OptiQ-4bit"}) == 4
    assert suggest._bits({"id": "x/y-mxfp4", "config": {"quantization_config": {"bits": 4}}}) == 4


def test_choose_prefers_precision_only_while_the_mac_keeps_room():
    builds = [{"bits": 4, "need_gb": 17.4, "context": 131072, "fits": True},
              {"bits": 6, "need_gb": 23.6, "context": 131072, "fits": True},
              {"bits": 8, "need_gb": 29.9, "context": 98304, "fits": True}]
    assert suggest._choose(builds, 37.4)["bits"] == 4       # 6-bit would take 63% of the ceiling
    assert suggest._choose(builds, 99.8)["bits"] == 8
    cramped = [{"bits": 4, "need_gb": 11.0, "context": 16384, "fits": True},
               {"bits": 8, "need_gb": 12.4, "context": 8192, "fits": True}]
    assert suggest._choose(cramped, 12.5)["bits"] == 4      # neither has a harness window: most context
    assert suggest._choose([{"bits": 4, "need_gb": 30, "context": 8192, "fits": False}], 12.5) is None


def test_labels_name_the_ones_that_stand_out():
    items = [{"scores": {"code": 0.6, "overall": 0.77}, "need_gb": 17.4},
             {"scores": {"code": 0.7, "overall": 0.65}, "need_gb": 21.8},
             {"scores": {"chat": 0.8, "overall": 0.68}, "need_gb": 4.3},
             {"scores": {"overall": 0.3}, "need_gb": 3.0}]
    got = suggest._label(items)
    assert [it["label"] for it in got] == ["Best on this Mac", "Best for code", "Light and quick", ""]


def test_build_joins_boards_catalogue_and_memory(tmp_path, monkeypatch):
    rows = [
        {"id": "mlx-community/Qwen3.8-27B-4bit", "downloads": 100, "pipeline_tag": "text-generation",
         "baseModels": {"models": [{"id": "Qwen/Qwen3.8-27B"}]},
         "safetensors": {"parameters": {"U32": 26_893_352_960, "BF16": 463_375_600}}},
        {"id": "mlx-community/Qwen3.8-27B-8bit", "downloads": 50, "pipeline_tag": "text-generation",
         "baseModels": {"models": [{"id": "Qwen/Qwen3.8-27B"}]},
         "safetensors": {"parameters": {"U32": 26_893_352_960, "BF16": 463_375_600}}},
        {"id": "mlx-community/Qwen3.8-27B-MTP-4bit", "downloads": 500, "pipeline_tag": "text-generation",
         "baseModels": {"models": [{"id": "Qwen/Qwen3.8-27B"}]},
         "safetensors": {"parameters": {"U32": 26_893_352_960}}},
        {"id": "mlx-community/Nobody-7B-4bit", "downloads": 9, "pipeline_tag": "text-generation",
         "baseModels": {"models": [{"id": "someone/Nobody-7B"}]},
         "safetensors": {"parameters": {"U32": 7_000_000_000}}},
    ]
    hybrid = {"num_hidden_layers": 64, "num_key_value_heads": 4, "num_attention_heads": 24,
              "head_dim": 256, "max_position_embeddings": 262144,
              "layer_types": ["linear_attention"] * 48 + ["full_attention"] * 16}

    async def fake_catalogue(client):
        return rows

    async def fake_config(client, repo, cache):
        cache[repo] = hybrid
        return hybrid

    monkeypatch.setattr(suggest, "catalogue", fake_catalogue)
    monkeypatch.setattr(suggest, "_config", fake_config)
    monkeypatch.setattr(suggest, "CONFIGS", tmp_path / "configs.json")
    got = asyncio.run(suggest.build(37.4, 21.9, ["mlx-community/Qwen3.8-27B-4bit"]))
    assert len(got["suggestions"]) == 1                      # Nobody-7B is on no board
    it = got["suggestions"][0]
    assert it["repo"] == "mlx-community/Qwen3.8-27B-4bit"   # the MTP build never counts
    assert it["installed"] and it["label"] == "Best on this Mac"
    assert it["context"] == 131072 and it["need_gb"] == 18.2  # 15.0 + 2 GB at 32k + 1.2
    assert [o["bits"] for o in it["others"]] == [8]
    assert "code" in it["scores"]


def test_family_reads_vendor_generation_and_the_rest():
    assert suggest.family("qwen3.8-27b") == ("qwen-27b", 3.8)
    assert suggest.family("qwen3.5-35b-a3b") == ("qwen-35b-a3b", 3.5)
    assert suggest.family("gemma-4-31b-it") == ("gemma-31b-it", 4.0)
    assert suggest.family("glm-4.7-flash") == ("glm-flash", 4.7)
    assert suggest.family("llama-3.1-8b-instruct") == ("llama-8b-instruct", 3.1)
    assert suggest.family("gpt-oss-20b") == ("gpt-oss-20b", 0.0)          # no generation to read
    assert suggest.family("deepseek-r1-distill-qwen-14b")[0] == "deepseek-r1-distill-qwen-14b"


def test_the_newest_generation_supersedes_and_inherits_what_it_lacks():
    old = {"base": "qwen3.5-27b", "name": "Qwen3.5-27B", "date": "2026-02-24", "builds": [1],
           "slots": {"math/hard": 0.86, "chat/hard": 0.71, "repo/medium": 0.87}}
    mid = {"base": "qwen3.6-27b", "name": "Qwen3.6 27B", "date": "2026-04-22", "builds": [1],
           "slots": {"math/hard": 0.64, "chat/hard": 0.90}}
    new = {"base": "qwen3.8-27b", "name": "Qwen 3.8 27B", "date": "2026-08-14", "builds": [1],
           "slots": {"code/hard": 0.64}}
    other = {"base": "gemma-4-31b-it", "name": "Gemma 4 31B", "date": "2026-04-02", "builds": [1],
             "slots": {"code/hard": 0.7}}
    got = {e["base"]: e for e in suggest.supersede([old, mid, new, other])}
    assert set(got) == {"qwen3.8-27b", "gemma-4-31b-it"}
    q = got["qwen3.8-27b"]
    assert q["supersedes"] == ["Qwen3.6 27B", "Qwen3.5-27B"]
    # the nearest predecessor's number wins for a slot several have
    assert q["estimated"] == ["chat/hard", "math/hard", "repo/medium"]
    assert q["scores"] == {"code": 0.76, "chat": 0.9, "math": 0.64, "overall": 0.77}
    assert got["gemma-4-31b-it"]["estimated"] == [] and got["gemma-4-31b-it"]["supersedes"] == []
