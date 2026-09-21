# SPDX-License-Identifier: Apache-2.0
"""Engines: the programs that serve models, fetched by eki when needed.

A model is a file; something has to run it. mlx_lm runs MLX builds,
llama.cpp runs GGUF, ComfyUI runs diffusion checkpoints. None of them is
something a person should have to install: an engine here is a manifest
(what it serves, which version, where it comes from, how big it is) and
an installer that puts exactly that version under ~/.eki/engines, owned
by eki — no sudo, nothing on PATH, nothing in the user's Homebrew or
Python. Uninstalling is deleting the folder.

Installs are pinned and verified: the official source, the version eki
chose, a checksum before anything runs. A failed check leaves nothing
behind and says why. The first model that needs an engine triggers the
install, as part of that model's setup, with progress in the thread.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

HOME = Path(os.environ.get("EKI_ENGINES", "~/.eki/engines")).expanduser()
MODELS = Path("~/.eki/models").expanduser()


@dataclass
class ServeSpec:
    """What a start script needs to know."""
    key: str                            # provider key: ~/.eki/models/<key>/
    model: str                          # repo (MLX) or a file path (GGUF)
    port: int
    context: int = 0                    # 0 = engine default
    max_tokens: int = 8192
    sampling: Dict[str, Any] = field(default_factory=dict)
    thinking: Optional[bool] = None     # False = switch a reasoning model's thinking off
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class EngineInfo:
    name: str
    title: str
    formats: Tuple[str, ...]            # "mlx" | "gguf"
    version: str
    size_mb: int
    source: str                         # where the install comes from, for the person
    note: str = ""


class Engine:
    """One serving program. Subclasses fill in how to get it and run it."""
    info: EngineInfo

    @property
    def home(self) -> Path:
        return HOME / self.info.name / self.info.version

    # ---- presence ---------------------------------------------------------

    def installed(self) -> bool:
        return (self.home / "ok").exists()

    def status(self) -> Dict[str, Any]:
        return {"name": self.info.name, "title": self.info.title, "formats": list(self.info.formats),
                "version": self.info.version, "size_mb": self.info.size_mb, "source": self.info.source,
                "installed": self.installed(), "path": str(self.home), "note": self.info.note}

    async def install(self) -> AsyncIterator[str]:
        """Progress lines; the last state is `installed()`."""
        raise NotImplementedError
        yield ""                                    # pragma: no cover

    def remove(self) -> None:
        shutil.rmtree(HOME / self.info.name, ignore_errors=True)

    async def health(self) -> Tuple[bool, str]:
        """Can it be run at all? A version string when it can."""
        raise NotImplementedError

    # ---- serving ----------------------------------------------------------

    def serve_argv(self, spec: ServeSpec) -> List[str]:
        raise NotImplementedError

    def serve_env(self, spec: ServeSpec) -> Dict[str, str]:
        return dict(spec.env)

    def write_scripts(self, spec: ServeSpec) -> Dict[str, str]:
        """start.sh / stop.sh under ~/.eki/models/<key>/ — what the model
        manager runs, whichever engine is behind it."""
        folder = MODELS / spec.key
        folder.mkdir(parents=True, exist_ok=True)
        env = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in self.serve_env(spec).items())
        argv = " ".join(shlex.quote(a) for a in self.serve_argv(spec))
        start = folder / "start.sh"
        start.write_text(f"""#!/bin/bash
# Written by eki. Serves {spec.model} with {self.info.title} {self.info.version} on 127.0.0.1:{spec.port}.
{env}exec {argv} >> {shlex.quote(str(folder / 'server.log'))} 2>&1
""")
        stop = folder / "stop.sh"
        stop.write_text(f"""#!/bin/bash
# Written by eki. Stops whatever listens on {spec.port}.
for p in $(lsof -nP -iTCP:{spec.port} -sTCP:LISTEN -t 2>/dev/null); do kill "$p"; done
""")
        start.chmod(0o755)
        stop.chmod(0o755)
        return {"start": str(start), "stop": str(stop), "engine": self.info.name,
                "engine_version": self.info.version}


# ---- shared helpers ----------------------------------------------------------

async def fetch(url: str, dest: Path, expected_sha256: str = "",
                expected_bytes: int = 0) -> AsyncIterator[str]:
    """Download to `dest` (through a .part file), verify, report progress."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    done, last = 0, -1
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or expected_bytes or 0)
            with part.open("wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    pct = int(100 * done / total) if total else 0
                    if pct >= last + 10:
                        last = pct
                        yield f"  {done / 1024**2:.0f} of {total / 1024**2:.0f} MB ({pct}%)\n"
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {dest.name}: not installing it")
    part.replace(dest)


async def run(argv: List[str], cwd: Optional[Path] = None,
              env: Optional[Dict[str, str]] = None) -> AsyncIterator[str]:
    """Run a command, yielding its output lines; raises on a non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd) if cwd else None, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert proc.stdout is not None
    tail: List[str] = []
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        tail = (tail + [line])[-5:]
        yield line
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed: " + (tail[-1] if tail else f"exit {proc.returncode}"))


def own_python() -> str:
    """The interpreter eki itself runs on — the app bundles one, so a venv
    made from it needs nothing from the system."""
    return sys.executable
