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

It also updates that standalone install (`update`): Codex's own `codex
update` can't tell how the standalone binary got there and gives up, and
npm or Homebrew would update a different copy. The new binary comes from
the same release, is checked the same way, and the helper is replaced with
the matching one — they're versioned together.
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


LATEST = "https://api.github.com/repos/openai/codex/releases/latest"


def _arch() -> str:
    return {"arm64": "aarch64", "x86_64": "x86_64"}.get(platform.machine(), platform.machine())


async def _fetch(version: str, name: str, into: Path) -> Path:
    """`name`-<arch>-apple-darwin from the release, checked, in `into`."""
    url = f"{RELEASES}/rust-v{version}/{name}-{_arch()}-apple-darwin.tar.gz"
    archive = into / f"{name}.tar.gz"
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise RuntimeError(f"no {name} for Codex {version} at {url} (HTTP {r.status_code})")
    archive.write_bytes(r.content)
    with tarfile.open(archive) as tar:
        members = [m for m in tar.getmembers()
                   if m.isfile() and m.name.strip("./").startswith(name) and "/" not in m.name.strip("./")]
        if not members:
            raise RuntimeError(f"the release archive didn't contain {name}")
        tar.extract(members[0], into, filter="data")
    got = into / members[0].name
    if not _signed_by_openai(got):
        raise RuntimeError(f"the downloaded {name} isn't signed by OpenAI — not installing it")
    got.chmod(0o755)
    subprocess.run(["xattr", "-d", "com.apple.quarantine", str(got)], capture_output=True)
    return got


async def latest_version() -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(LATEST)
    tag = str((r.json() or {}).get("tag_name") or "") if r.status_code == 200 else ""
    m = re.search(r"(\d+\.\d+\.\d+)", tag)
    if not m:
        raise RuntimeError(f"couldn't read Codex's latest release ({r.status_code})")
    return m.group(1)


async def update(binary: str, version: str = "") -> str:
    """Replace a standalone Codex (and its helper) with `version` (latest)."""
    target = Path(binary).resolve()
    now = version_of(str(target))
    version = version or await latest_version()
    if now == version:
        return f"Codex {version} is already installed"
    # downloaded beside it, so the swap is a rename on one disk
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        new = await _fetch(version, "codex", Path(tmp))
        helper = await _fetch(version, "codex-code-mode-host", Path(tmp)) if present(str(target)) else None
        os.replace(new, target)
        if helper is not None:
            os.replace(helper, host_path(str(target)))
    got = version_of(str(target))
    if got != version:
        raise RuntimeError(f"updated, but {target} reports {got}")
    return f"updated Codex {now} → {version} at {target}" + (" (and its helper)" if helper else "")


def main(argv: Optional[list] = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "update":
        print("usage: python -m eki.codex_host update <codex binary> [version]", file=sys.stderr)
        return 2
    try:
        print(asyncio.run(update(args[1], args[2] if len(args) > 2 else "")))
    except (RuntimeError, OSError, httpx.HTTPError) as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
