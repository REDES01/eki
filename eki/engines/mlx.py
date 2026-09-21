# SPDX-License-Identifier: Apache-2.0
"""mlx_lm: MLX builds on Apple Silicon.

A venv of eki's own with a pinned mlx-lm, made from the Python eki runs
on. Someone who already has an MLX environment (Settings → Models → MLX
Python) keeps using it: that path is honoured first, and nothing is
installed while it works.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import AsyncIterator, Dict, List, Tuple

from .base import Engine, EngineInfo, ServeSpec, own_python, run
from .. import settings as settings_mod

MLX_LM = "0.31.3"


class MlxEngine(Engine):
    info = EngineInfo(
        name="mlx", title="mlx_lm", formats=("mlx",), version=MLX_LM, size_mb=250,
        source=f"pip: mlx-lm=={MLX_LM}",
        note="Apple's MLX runtime for the mlx-community builds — the fastest way to run a model on this Mac.")

    # ---- which python ------------------------------------------------------

    def external_python(self) -> str:
        """A Python the person pointed eki at, if it has mlx_lm."""
        py = os.path.expanduser(str(settings_mod.load().get("mlx_python") or ""))
        return py if py and Path(py).exists() and (Path(py).parent / "python").exists() else ""

    def python(self) -> str:
        own = self.home / "venv" / "bin" / "python"
        if self.installed() and own.exists():
            return str(own)
        return self.external_python()

    def installed(self) -> bool:
        return super().installed() or bool(self.external_python() and _has_mlx_lm(self.external_python()))

    def status(self):
        s = super().status()
        if not super().installed() and self.external_python():
            s["path"] = self.external_python()
            s["note"] = "using the MLX Python from Settings"
        return s

    # ---- install -----------------------------------------------------------

    async def install(self) -> AsyncIterator[str]:
        venv = self.home / "venv"
        self.home.mkdir(parents=True, exist_ok=True)
        yield f"Making a Python environment for mlx_lm {MLX_LM}…\n"
        async for _ in run([own_python(), "-m", "venv", "--clear", str(venv)]):
            pass
        pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
               f"mlx-lm=={MLX_LM}", "huggingface_hub"]
        yield "Installing mlx-lm (about 250 MB)…\n"
        async for line in run(pip):
            if line.strip() and not line.startswith("WARNING"):
                yield f"  {line[:120]}\n"
        ok, detail = await self.health()
        if not ok:
            raise RuntimeError(f"mlx_lm installed but doesn't run: {detail}")
        (self.home / "ok").write_text(MLX_LM)
        yield f"mlx_lm {MLX_LM} ready.\n"

    async def health(self) -> Tuple[bool, str]:
        py = self.python()
        if not py:
            return False, "not installed"
        proc = await asyncio.create_subprocess_exec(
            py, "-c", "import mlx_lm; print(mlx_lm.__version__)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if proc.returncode != 0:
            lines = err.decode("utf-8", "replace").strip().splitlines()
            return False, lines[-1] if lines else "import failed"
        return True, f"mlx_lm {out.decode().strip()}"

    # ---- serve -------------------------------------------------------------

    def serve_env(self, spec: ServeSpec) -> Dict[str, str]:
        s = settings_mod.load()
        return {**spec.env, "HF_HOME": os.path.expanduser(s["hf_home"]),
                "HF_HUB_DISABLE_XET": "1", "HF_HUB_OFFLINE": "1"}

    def serve_argv(self, spec: ServeSpec) -> List[str]:
        argv = [self.python(), "-m", "mlx_lm", "server", "--model", spec.model,
                "--host", "127.0.0.1", "--port", str(spec.port), "--max-tokens", str(spec.max_tokens)]
        if spec.thinking is not None:
            argv += ["--chat-template-args", json.dumps({"enable_thinking": spec.thinking})]
        for key, flag in (("temperature", "--temp"), ("top_p", "--top-p"), ("top_k", "--top-k")):
            if key in spec.sampling:
                argv += [flag, str(spec.sampling[key])]
        return argv


_KNOWN: Dict[str, bool] = {}


def _has_mlx_lm(py: str) -> bool:
    """Asked once per interpreter: a subprocess is too slow for every status call."""
    import subprocess
    if py not in _KNOWN:
        try:
            _KNOWN[py] = subprocess.run([py, "-c", "import mlx_lm"], capture_output=True,
                                        timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _KNOWN[py] = False
    return _KNOWN[py]
