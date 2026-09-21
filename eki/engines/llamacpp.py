# SPDX-License-Identifier: Apache-2.0
"""llama.cpp: GGUF models, through its `llama-server`.

The official macOS arm64 build of one pinned release, from the project's
GitHub releases, checksum verified against the release manifest before
it's unpacked. Small (about 11 MB) and self-contained: the binaries and
their dylibs live together under eki's engines folder.
"""
from __future__ import annotations

import asyncio
import platform
import tarfile
from pathlib import Path
from typing import AsyncIterator, List, Tuple

from .base import Engine, EngineInfo, ServeSpec, fetch

RELEASE = "b11070"
ASSET = f"llama-{RELEASE}-bin-macos-arm64.tar.gz"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/{ASSET}"
SHA256 = "a398187d867fea5595de57bbec1ed464292f28b491f2d6063bd756bc453bd99f"
BYTES = 11_179_494


class LlamaCppEngine(Engine):
    info = EngineInfo(
        name="llamacpp", title="llama.cpp", formats=("gguf",), version=RELEASE, size_mb=11,
        source=f"github.com/ggml-org/llama.cpp release {RELEASE}",
        note="Runs GGUF builds — the format with the most models — with Metal acceleration.")

    @property
    def server(self) -> Path:
        return self.home / "bin" / "llama-server"

    async def install(self) -> AsyncIterator[str]:
        if platform.machine() != "arm64":
            raise RuntimeError("llama.cpp is fetched for Apple Silicon only")
        self.home.mkdir(parents=True, exist_ok=True)
        archive = self.home / ASSET
        yield f"Getting llama.cpp {RELEASE} ({BYTES / 1024**2:.0f} MB) from GitHub…\n"
        async for line in fetch(URL, archive, SHA256, BYTES):
            yield line
        yield "Checksum verified. Unpacking…\n"
        bin_dir = self.home / "bin"
        with tarfile.open(archive) as tar:
            # the dylibs come with version symlinks the binaries load through
            members = [m for m in tar.getmembers() if (m.isfile() or m.issym()) and "/" in m.name]
            for m in members:                       # llama-b11070/llama-server → bin/llama-server
                m.name = m.name.split("/", 1)[1]
            tar.extractall(bin_dir, members=members, filter="data")
        archive.unlink(missing_ok=True)
        for f in bin_dir.iterdir():
            if not f.is_symlink() and (f.name.startswith("llama") or f.name.startswith("ggml")):
                f.chmod(0o755)
        ok, detail = await self.health()
        if not ok:
            raise RuntimeError(f"llama.cpp unpacked but doesn't run: {detail}")
        (self.home / "ok").write_text(RELEASE)
        yield f"llama.cpp {detail} ready.\n"

    async def health(self) -> Tuple[bool, str]:
        if not self.server.exists():
            return False, "not installed"
        proc = await asyncio.create_subprocess_exec(
            str(self.server), "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = out.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            return False, text.splitlines()[-1] if text else f"exit {proc.returncode}"
        return True, text.splitlines()[0] if text else RELEASE

    def serve_argv(self, spec: ServeSpec) -> List[str]:
        argv = [str(self.server), "-m", spec.model, "--host", "127.0.0.1", "--port", str(spec.port),
                "--jinja",                          # the chat template, tool calls included
                "-ngl", "99",                       # every layer on the GPU
                "-n", str(spec.max_tokens)]
        if spec.context:
            argv += ["-c", str(spec.context)]
        if spec.thinking is False:
            argv += ["--reasoning-budget", "0"]
        for key, flag in (("temperature", "--temp"), ("top_p", "--top-p"), ("top_k", "--top-k")):
            if key in spec.sampling:
                argv += [flag, str(spec.sampling[key])]
        return argv
