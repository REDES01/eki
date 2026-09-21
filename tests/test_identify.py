# SPDX-License-Identifier: Apache-2.0
"""One field takes anything; eki says what it is."""
import asyncio
import struct

import httpx
import pytest

from eki import gguf, identify

HYBRID = {"num_hidden_layers": 64, "num_key_value_heads": 4, "num_attention_heads": 24,
          "head_dim": 256, "max_position_embeddings": 262144,
          "layer_types": ["linear_attention"] * 48 + ["full_attention"] * 16}


def _gguf(path, kv):
    """A GGUF header with these metadata pairs and no tensors."""
    def s(x):
        b = x.encode(); return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kv))
    for k, v in kv.items():
        out += s(k)
        if isinstance(v, str):
            out += struct.pack("<I", 8) + s(v)
        elif isinstance(v, float):
            out += struct.pack("<I", 6) + struct.pack("<f", v)
        elif isinstance(v, list):
            out += struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", len(v)) + b"".join(s(x) for x in v)
        else:
            out += struct.pack("<I", 4) + struct.pack("<I", v)
    path.write_bytes(out + b"\0" * 4096)
    return path


def test_a_gguf_header_says_what_the_file_is(tmp_path):
    f = _gguf(tmp_path / "Qwen3-14B-Q4_K_M.gguf", {
        "general.architecture": "qwen3", "general.basename": "Qwen3-14B", "general.size_label": "14B",
        "qwen3.block_count": 40, "qwen3.context_length": 40960, "qwen3.embedding_length": 5120,
        "qwen3.attention.head_count": 40, "qwen3.attention.head_count_kv": 8, "qwen3.attention.key_length": 128,
        "tokenizer.chat_template": "{% if tools %}…{% endif %}{{ enable_thinking }}",
        "tokenizer.ggml.tokens": ["a", "b", "c"]})
    h = gguf.read_header(f)
    assert h["general.architecture"] == "qwen3" and h["qwen3.context_length"] == 40960
    cfg = gguf.config_from_header(h)
    assert cfg == {"model_type": "qwen3", "num_hidden_layers": 40, "num_attention_heads": 40,
                   "num_key_value_heads": 8, "head_dim": 128, "hidden_size": 5120,
                   "max_position_embeddings": 40960}
    got = asyncio.run(identify.identify(str(f), 20.0, 37.4))
    assert got["kind"] == "gguf_file" and got["next"] == "deploy" and got["format"] == "gguf"
    fit = got["fit"]
    assert fit["quant"] == "Q4_K_M" and fit["profile"]["tools"] and fit["profile"]["thinking_switch"]
    assert fit["profile"]["base"] == "qwen3-14b" and fit["profile"]["params_b"] == 14.0
    assert fit["context"] == 32768 and fit["fits"]
    (tmp_path / "x.gguf").write_bytes(b"nope")
    with pytest.raises(ValueError):
        gguf.read_header(tmp_path / "x.gguf")


def test_an_mlx_folder_and_a_stray_path(tmp_path):
    folder = tmp_path / "Qwen3.8-27B-4bit"
    folder.mkdir()
    (folder / "config.json").write_text('{"model_type": "qwen3_5", "quantization": {"bits": 4, "group_size": 64}, '
                                        '"text_config": ' + __import__("json").dumps(HYBRID) + "}")
    (folder / "model.safetensors").write_bytes(b"\0" * (11 * 1024**2))
    got = asyncio.run(identify.identify(str(folder), 20.0, 37.4))
    assert got["kind"] == "mlx_dir" and got["format"] == "mlx" and got["fit"]["context"] == 131072
    assert got["fit"]["profile"]["quant_bits"] == 4
    empty = tmp_path / "empty"; empty.mkdir()
    assert asyncio.run(identify.identify(str(empty), 20.0, 37.4))["kind"] == "unsupported"
    assert asyncio.run(identify.identify("what is this", 20.0, 37.4))["kind"] == "unknown"
    assert asyncio.run(identify.identify("", 20.0, 37.4))["kind"] == "empty"


def test_a_hub_link_in_any_spelling(monkeypatch):
    seen = {}

    async def fake_details(repo, quant=""):
        seen["repo"] = repo
        return {"repo": repo, "format": "mlx", "weights_gb": 14.9, "download_gb": 15.0, "context": 262144,
                "config": HYBRID, "sampling": {}, "license": "apache-2.0", "gated": False, "vision": False,
                "base_id": ""}
    monkeypatch.setattr(identify.deploy, "details", fake_details)
    for text in ("https://huggingface.co/mlx-community/Qwen3.8-27B-4bit",
                 "hf.co/mlx-community/Qwen3.8-27B-4bit/tree/main",
                 "mlx-community/Qwen3.8-27B-4bit"):
        got = asyncio.run(identify.identify(text, 22.0, 37.4))
        assert got["kind"] == "hub" and got["repo"] == "mlx-community/Qwen3.8-27B-4bit", text
        assert got["fit"]["context"] == 131072 and "config" not in got["fit"]


def test_a_server_address_becomes_a_provider(monkeypatch):
    def handler(request):
        if request.url.path == "/system_stats" and request.url.port == 8188:
            return httpx.Response(200, json={"devices": []})
        if request.url.path == "/v1/models" and request.url.port == 1234:
            return httpx.Response(200, json={"data": [{"id": "qwen3-14b"}]})
        return httpx.Response(404)

    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"})
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    comfy = asyncio.run(identify.identify("http://127.0.0.1:8188", 20.0, 37.4))
    assert comfy["kind"] == "server" and comfy["template"] == "comfyui" and comfy["next"] == "provider"
    lm = asyncio.run(identify.identify("http://127.0.0.1:1234", 20.0, 37.4))
    assert lm["template"] == "custom" and lm["url"] == "http://127.0.0.1:1234/v1" and lm["models"] == ["qwen3-14b"]
    assert asyncio.run(identify.identify("http://127.0.0.1:9", 20.0, 37.4))["kind"] == "unknown"
