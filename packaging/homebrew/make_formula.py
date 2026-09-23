#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the Homebrew formula for a release of eki.

    python3 packaging/homebrew/make_formula.py <version> [--url URL] [--sha256 HEX] > eki.rb

The formula installs eki's own source beside a virtualenv holding exactly the
dependencies of requirements.txt, resolved for each platform Homebrew runs
on (uv pip compile), each as a wheel pinned by its SHA-256 from PyPI. So a
`brew install` builds nothing and resolves nothing: what was tested is what
lands. Linux pulls a few more (keyring's Secret Service backend).

Without --url the release tarball on GitHub is used, and its SHA-256 is read
from it; --url file:///… lets a formula be tried before anything is pushed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
PY = "3.12"
CP = "cp" + PY.replace(".", "")
REPO = "https://github.com/REDES01/eki"
#: (Homebrew's block, uv's platform, what a matching wheel tag contains)
PLATFORMS = [
    ("macos_arm", "aarch64-apple-darwin", ("macosx", ("arm64", "universal2"))),
    ("macos_intel", "x86_64-apple-darwin", ("macosx", ("x86_64", "universal2"))),
    ("linux_intel", "x86_64-unknown-linux-gnu", ("manylinux", ("x86_64",))),
    ("linux_arm", "aarch64-unknown-linux-gnu", ("manylinux", ("aarch64",))),
]


def resolve(platform: str) -> Dict[str, str]:
    out = subprocess.run(["uv", "pip", "compile", str(ROOT / "requirements.txt"), "--python-version", PY,
                          "--python-platform", platform, "--no-header", "--no-annotate", "-q"],
                         capture_output=True, text=True, check=True).stdout
    pins = {}
    for line in out.splitlines():
        name, _, ver = line.strip().partition("==")
        if ver:
            pins[name] = ver
    return pins


def pypi(name: str, version: str) -> List[dict]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        return [f for f in json.load(r)["urls"] if f["packagetype"] == "bdist_wheel"]


def _tags(filename: str) -> Tuple[str, str, str]:
    parts = filename[:-4].split("-")
    return parts[-3], parts[-2], parts[-1]


def _python_ok(py: str, abi: str) -> bool:
    if py in ("py3", "py2.py3") and abi == "none":
        return True
    if py == CP:
        return True
    m = re.fullmatch(r"cp3(\d+)", py)
    return bool(m and abi == "abi3" and int(m.group(1)) <= int(CP[3:]))


def pick(files: List[dict], want: Optional[Tuple[str, Tuple[str, ...]]]) -> Optional[dict]:
    """The wheel for a platform (None: the pure one, if any)."""
    best, key = None, None
    for f in files:
        py, abi, plat = _tags(f["filename"])
        if not _python_ok(py, abi):
            continue
        if want is None:
            if plat == "any":
                return f
            continue
        family, arches = want
        if plat == "any":
            return f
        if "musllinux" in plat or not any(family in p and any(a in p for a in arches) for p in plat.split(".")):
            continue
        # the most widely compatible build: the oldest macOS / glibc it names
        nums = tuple(int(n) for n in re.findall(r"_(\d+)_(\d+)_", "_" + plat + "_")[0]) \
            if re.search(r"_(\d+)_(\d+)_", "_" + plat + "_") else (0, 0)
        rank = (py != CP, nums)                   # a cp312 wheel over abi3
        if key is None or rank < key:
            best, key = f, rank
    return best


def block(name: str, f: dict, indent: str) -> str:
    return (f'{indent}resource "{name}" do\n'
            f'{indent}  url "{f["url"]}"\n'
            f'{indent}  sha256 "{f["digests"]["sha256"]}"\n'
            f'{indent}end\n')


def formula(version: str, url: str, sha: str) -> str:
    pins = {tag: resolve(plat) for tag, plat, _ in PLATFORMS}
    names = sorted(set().union(*[set(p) for p in pins.values()]))
    common, special = [], {tag: [] for tag, _, _ in PLATFORMS}
    for name in names:
        versions = {pins[t].get(name) for t in pins if name in pins[t]}
        if len(versions) != 1:
            raise SystemExit(f"{name} resolves to different versions per platform: {versions}")
        ver = versions.pop()
        files = pypi(name, ver)
        where = [t for t in pins if name in pins[t]]
        pure = pick(files, None)
        if pure and len(where) == len(PLATFORMS):
            common.append(block(name, pure, "  "))
            continue
        for tag, _, want in PLATFORMS:
            if tag not in where:
                continue
            f = pure or pick(files, want)
            if f is None:
                raise SystemExit(f"no {CP} wheel of {name} {ver} for {tag}")
            special[tag].append(block(name, f, "      "))

    def section(outer: str, inner: Dict[str, str]) -> str:
        body = ""
        for hb, tag in inner.items():
            if special[tag]:
                body += f"    {hb} do\n" + "".join(special[tag]) + "    end\n"
        return f"  {outer} do\n{body}  end\n" if body else ""

    platform_blocks = (section("on_macos", {"on_arm": "macos_arm", "on_intel": "macos_intel"})
                       + section("on_linux", {"on_arm": "linux_arm", "on_intel": "linux_intel"}))
    return TEMPLATE.format(version=version, url=url, sha=sha, py=PY,
                           resources="".join(common) + ("\n" + platform_blocks if platform_blocks else ""))


TEMPLATE = '''# typed: false
# frozen_string_literal: true

# Generated by packaging/homebrew/make_formula.py in REDES01/eki — edit that, not this.
class Eki < Formula
  desc "Routes each request to Claude Code, Codex or a local model, and learns from use"
  homepage "https://github.com/REDES01/eki"
  url "{url}"
  sha256 "{sha}"
  license "Apache-2.0"
  head "https://github.com/REDES01/eki.git", branch: "main"

  depends_on "python@{py}"

{resources}
  def install
    venv = libexec/"venv"
    system Formula["python@{py}"].opt_bin/"python{py}", "-m", "venv", venv
    wheels = buildpath/"wheels"
    wheels.mkpath
    resources.each do |r|
      r.fetch
      cp r.cached_download, wheels/File.basename(r.url)
    end
    system venv/"bin/python", "-m", "pip", "install", "--no-deps", "--no-index",
           "--disable-pip-version-check", *wheels.glob("*.whl")
    libexec.install "eki", "config.yaml", "VERSION"
    # the same layout as a checkout: the package and its seed config side by
    # side, run by the venv's python. EKI_INSTALL tells eki Homebrew owns it.
    (bin/"eki").write <<~SH
      #!/bin/bash
      export PYTHONPATH="#{{opt_libexec}}"
      export EKI_INSTALL=homebrew
      exec "#{{opt_libexec}}/venv/bin/python" -m eki.cli "$@"
    SH
  end

  def caveats
    <<~EOS
      eki drives the Claude Code and Codex you already have (`claude`, `codex`),
      signed in as you. To keep the engine running from login:
        eki agent install        (the same as `brew services start eki`)
      Then: eki ask "hello"
    EOS
  end

  service do
    run [opt_bin/"eki", "serve"]
    keep_alive true
    process_type :interactive
    log_path var/"log/eki.log"
    error_log_path var/"log/eki.log"
    environment_variables PATH: "#{{Dir.home}}/.local/bin:#{{HOMEBREW_PREFIX}}/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/eki --version")
    system libexec/"venv/bin/python", "-c", "import fastapi, uvicorn, httpx, yaml, pydantic, keyring, pyte"
  end
end
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--url")
    ap.add_argument("--sha256")
    a = ap.parse_args()
    url = a.url or f"{REPO}/archive/refs/tags/v{a.version}.tar.gz"
    sha = a.sha256
    if not sha:
        # curl: GitHub's archive redirect to codeload can stall urllib
        data = subprocess.run(["curl", "-fsSL", "--max-time", "120", url], capture_output=True,
                              check=True).stdout
        sha = hashlib.sha256(data).hexdigest()
    sys.stdout.write(formula(a.version, url, sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
