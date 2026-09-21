# SPDX-License-Identifier: Apache-2.0
"""The helper Codex needs before it can touch a file.

Codex runs its shell and edit tools through a separate program,
`codex-code-mode-host`, that it expects to find beside its own binary. The
standalone install doesn't always ship it, and without it Codex fails
closed: it answers questions and politely declines to create anything.

This fetches the helper that matches the installed Codex version from
Codex's own GitHub release — the same place the CLI came from — and checks
Apple's signature says it's OpenAI's before putting it anywhere. It only
ever runs when the user asks.
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import httpx

RELEASES = "https://github.com/openai/codex/releases/download"
#: Apple Developer Team behind Codex's signed binaries
OPENAI_TEAM = "2DC432GLL2"


def host_path(binary: str) -> Path:
    return Path(binary).resolve().with_name("codex-code-mode-host")


def present(binary: str) -> bool:
    p = host_path(binary)
    return p.exists() and os.access(p, os.X_OK)


def version_of(binary: str) -> Optional[str]:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def _asset() -> str:
    arch = {"arm64": "aarch64", "x86_64": "x86_64"}.get(platform.machine(), platform.machine())
    return f"codex-code-mode-host-{arch}-apple-darwin.tar.gz"


def _signed_by_openai(path: Path) -> bool:
    out = subprocess.run(["codesign", "-dv", str(path)], capture_output=True, text=True)
    return f"TeamIdentifier={OPENAI_TEAM}" in out.stderr + out.stdout


async def install(binary: str) -> str:
    """Put the matching helper beside `binary`. Returns what happened."""
    if present(binary):
        return "codex-code-mode-host is already installed"
    version = version_of(binary)
    if not version:
        raise RuntimeError("couldn't read Codex's version")
    url = f"{RELEASES}/rust-v{version}/{_asset()}"
    target = host_path(binary)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "host.tar.gz"
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as c:
            r = await c.get(url)
        if r.status_code != 200:
            raise RuntimeError(f"no helper for Codex {version} at {url} (HTTP {r.status_code})")
        archive.write_bytes(r.content)
        with tarfile.open(archive) as tar:
            members = [m for m in tar.getmembers()
                       if m.isfile() and "code-mode-host" in m.name and "/" not in m.name.strip("./")]
            if not members:
                raise RuntimeError("the release archive didn't contain the helper")
            tar.extract(members[0], tmp, filter="data")
            extracted = Path(tmp) / members[0].name
        if not _signed_by_openai(extracted):
            raise RuntimeError("the downloaded helper isn't signed by OpenAI — not installing it")
        extracted.chmod(0o755)
        shutil.move(str(extracted), str(target))
    subprocess.run(["xattr", "-d", "com.apple.quarantine", str(target)], capture_output=True)
    return f"installed codex-code-mode-host {version} beside {binary}"
