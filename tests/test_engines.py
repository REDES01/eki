# SPDX-License-Identifier: Apache-2.0
"""Engines are fetched by eki, pinned and verified, into its own folder."""
import asyncio
import hashlib
import io
import tarfile

import httpx
import pytest

from eki import engines, gguf
from eki.engines import base, llamacpp, mlx


def test_engines_are_found_by_format():
    assert engines.for_format("mlx").info.name == "mlx"
    assert engines.for_format("gguf").info.name == "llamacpp"
    assert engines.for_format("onnx") is None
    names = {s["name"] for s in engines.statuses()}
    assert names == {"mlx", "llamacpp"}


def test_scripts_come_from_the_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "MODELS", tmp_path)
    monkeypatch.setattr(base, "HOME", tmp_path / "engines")
    eng = llamacpp.LlamaCppEngine()
    spec = base.ServeSpec(key="q", model="/w/Qwen3-14B-Q4_K_M.gguf", port=8093, context=32768,
                          sampling={"temperature": 0.6}, thinking=False)
    paths = eng.write_scripts(spec)
    start = open(paths["start"]).read()
    assert "llama-server" in start and "-m /w/Qwen3-14B-Q4_K_M.gguf" in start
    assert "--host 127.0.0.1 --port 8093" in start and "--jinja" in start
    assert "-c 32768" in start and "--reasoning-budget 0" in start and "--temp 0.6" in start
    assert paths["engine"] == "llamacpp" and paths["engine_version"] == llamacpp.RELEASE
    assert "8093" in open(paths["stop"]).read()


def test_llamacpp_install_verifies_then_unpacks(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "HOME", tmp_path / "engines")
    # a fake release archive shaped like the real one
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        script = b"#!/bin/sh\necho 'version: 0.4.1-dev (build 11070)'\n"
        info = tarfile.TarInfo(f"llama-{llamacpp.RELEASE}/llama-server")
        info.size = len(script); info.mode = 0o755
        tar.addfile(info, io.BytesIO(script))
        lic = tarfile.TarInfo(f"llama-{llamacpp.RELEASE}/LICENSE"); lic.size = 3
        tar.addfile(lic, io.BytesIO(b"MIT"))
    blob = buf.getvalue()

    def transport(request):
        return httpx.Response(200, content=blob, headers={"content-length": str(len(blob))})

    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(transport), **{k: v for k, v in kw.items()
                                                                          if k != "transport"})
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    eng = llamacpp.LlamaCppEngine()
    monkeypatch.setattr(llamacpp, "SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        asyncio.run(_drain(eng.install()))
    assert not eng.installed() and not list((tmp_path / "engines").rglob("llama-server"))
    monkeypatch.setattr(llamacpp, "SHA256", hashlib.sha256(blob).hexdigest())
    lines = asyncio.run(_drain(eng.install()))
    assert eng.installed() and eng.server.exists()
    assert any("verified" in ln for ln in lines) and "ready" in lines[-1]
    ok, detail = asyncio.run(eng.health())
    assert ok and "11070" in detail
    eng.remove()
    assert not eng.installed()


async def _drain(gen):
    return [ln async for ln in gen]


def test_mlx_engine_honours_an_external_python(tmp_path, monkeypatch):
    from eki import settings
    monkeypatch.setattr(base, "HOME", tmp_path / "engines")
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    eng = mlx.MlxEngine()
    settings.save({"mlx_python": str(tmp_path / "nowhere" / "python")})
    assert not eng.installed() and eng.python() == ""
    py = tmp_path / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True); py.write_text(""); (py.parent / "python").touch()
    settings.save({"mlx_python": str(py)})
    monkeypatch.setattr(mlx, "_KNOWN", {str(py): True})
    assert eng.installed() and eng.python() == str(py)
    assert eng.status()["note"] == "using the MLX Python from Settings"
    argv = eng.serve_argv(base.ServeSpec(key="m", model="org/m-4bit", port=8090, thinking=False,
                                         sampling={"top_k": 20}))
    assert argv[:4] == [str(py), "-m", "mlx_lm", "server"] and "--top-k" in argv
    assert '{"enable_thinking": false}' in argv


# ---- GGUF -------------------------------------------------------------------

def test_gguf_files_fold_shards_and_skip_projectors():
    siblings = [
        {"rfilename": "Qwen3-14B-Q4_K_M.gguf", "size": 9_001_753_984, "lfs": {"sha256": "aa"}},
        {"rfilename": "Qwen3-14B-Q8_0.gguf", "size": 15_700_000_000},
        {"rfilename": "Qwen3-14B-BF16-00001-of-00002.gguf", "size": 15_000_000_000},
        {"rfilename": "Qwen3-14B-BF16-00002-of-00002.gguf", "size": 14_543_424_160},
        {"rfilename": "mmproj-F16.gguf", "size": 800_000_000},
        {"rfilename": "README.md", "size": 100},
    ]
    files = gguf.files_of(siblings)
    assert [f["quant"] for f in files] == ["Q4_K_M", "Q8_0", "BF16"]
    assert files[0]["gb"] == 8.38 and files[0]["bits"] == 4.8 and files[0]["sha256s"] == {"Qwen3-14B-Q4_K_M.gguf": "aa"}
    assert files[2]["shards"] == 2 and len(files[2]["files"]) == 2 and files[2]["gb"] == 27.51
    assert gguf.default_quant(files)["quant"] == "Q4_K_M"
    assert gguf.first_shard(files[2]).endswith("00001-of-00002.gguf")
    assert gguf.quant_of("model.IQ4_XS.gguf") == "IQ4_XS" and gguf.quant_of("model-UD-Q4_K_XL.gguf") == "UD-Q4_K_XL"
    assert gguf.quant_of("weird.gguf") == ""
