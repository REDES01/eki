# SPDX-License-Identifier: Apache-2.0
"""GGUF builds: the files, their quantisations, what they cost.

A GGUF repo holds one file per quantisation (Q4_K_M, Q8_0, …), sometimes
sharded. The Hub's metadata says the architecture, the trained context,
the parameter count and the chat template; the base model's config says
how the cache grows. Between them a GGUF build is profiled and sized
before a byte is downloaded, the same as an MLX one.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path("~/.eki/gguf").expanduser()

#: effective bits per weight of llama.cpp's quantisation types, scales
#: included — what the file size divided by the parameter count comes to
BITS = {
    "IQ1_S": 1.6, "IQ1_M": 1.8, "IQ2_XXS": 2.1, "IQ2_XS": 2.3, "IQ2_S": 2.5, "IQ2_M": 2.7, "Q2_K": 2.6,
    "Q2_K_L": 2.8, "IQ3_XXS": 3.1, "IQ3_XS": 3.3, "IQ3_S": 3.4, "IQ3_M": 3.7, "Q3_K_S": 3.5, "Q3_K_M": 3.9,
    "Q3_K_L": 4.3, "IQ4_XS": 4.3, "IQ4_NL": 4.5, "Q4_0": 4.5, "Q4_1": 5.0, "Q4_K_S": 4.6, "Q4_K_M": 4.8,
    "Q5_0": 5.5, "Q5_1": 6.0, "Q5_K_S": 5.5, "Q5_K_M": 5.7, "Q6_K": 6.6, "Q8_0": 8.5, "F16": 16.0,
    "BF16": 16.0, "F32": 32.0, "MXFP4": 4.3, "UD-Q4_K_XL": 4.9, "UD-Q5_K_XL": 5.8, "UD-Q8_K_XL": 8.7,
}
_QUANT = re.compile(r"[-.](" + "|".join(sorted((re.escape(k) for k in BITS), key=len, reverse=True))
                    + r")(?:[-.]|$)", re.I)
_SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
_MMPROJ = re.compile(r"mmproj", re.I)

#: what a person gets when they don't choose: the community's default,
#: the best size/quality trade for most machines
PREFERRED = ("Q4_K_M", "UD-Q4_K_XL", "IQ4_XS", "Q4_K_S", "Q4_0", "Q5_K_M", "Q6_K", "Q8_0", "Q3_K_M")


def quant_of(filename: str) -> str:
    m = _QUANT.search(Path(filename).name)
    return m.group(1).upper() if m else ""


def bits_of(quant: str) -> float:
    return BITS.get(quant.upper(), 0.0)


def files_of(siblings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per quantisation, shards folded together, vision projectors
    left out: {quant, files, bytes, sha256s}."""
    out: Dict[str, Dict[str, Any]] = {}
    for s in siblings:
        name = s.get("rfilename", "")
        if not name.endswith(".gguf") or _MMPROJ.search(name):
            continue
        quant = quant_of(name)
        if not quant:
            continue
        m = _SHARD.search(name)
        entry = out.setdefault(quant, {"quant": quant, "files": [], "bytes": 0, "sha256s": {}, "bits": bits_of(quant)})
        entry["files"].append(name)
        entry["bytes"] += int(s.get("size") or 0)
        sha = ((s.get("lfs") or {}).get("sha256")) or ""
        if sha:
            entry["sha256s"][name] = sha
        if m:
            entry["shards"] = int(m.group(2))
    for e in out.values():
        e["files"].sort()
        e["gb"] = round(e["bytes"] / 1024**3, 2)
    return sorted(out.values(), key=lambda e: e["bits"])


def default_quant(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    by = {f["quant"]: f for f in files}
    for q in PREFERRED:
        if q in by:
            return by[q]
    return files[len(files) // 2] if files else None


def local_path(repo: str, filename: str) -> Path:
    return HOME / repo.replace("/", "--") / filename


def first_shard(entry: Dict[str, Any]) -> str:
    """llama-server takes the first shard and finds the rest itself."""
    return entry["files"][0]


# ---- reading a file's header ---------------------------------------------------

import struct

_TYPES = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
_SIZES = {"B": 1, "b": 1, "H": 2, "h": 2, "I": 4, "i": 4, "f": 4, "?": 1, "Q": 8, "q": 8, "d": 8}


def read_header(path: Path, want: Optional[set] = None) -> Dict[str, Any]:
    """The metadata a GGUF file carries about itself: architecture, context
    length, layer and head counts, the chat template — enough to profile
    and size a file someone already has, without the Hub. Tensor data is
    never read; big arrays in the metadata (the vocabulary) are skipped."""
    out: Dict[str, Any] = {}
    with open(path, "rb") as f:
        def rd(fmt: str):
            size = _SIZES[fmt]
            return struct.unpack("<" + fmt, f.read(size))[0]

        def rstr() -> str:
            n = rd("Q")
            return f.read(n).decode("utf-8", "replace")

        def rval(t: int):
            if t == 8:
                return rstr()
            if t == 9:
                et, n = rd("I"), rd("Q")
                if et == 8:
                    return [rstr() for _ in range(n)] if n <= 64 else _skip_strings(f, n)
                fmt = _TYPES[et]
                data = f.read(_SIZES[fmt] * n)
                return list(struct.unpack("<" + fmt * n, data)) if n <= 64 else n
            return rd(_TYPES[t])

        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        version = rd("I")
        if version < 2:
            raise ValueError(f"GGUF version {version} is too old")
        n_tensors, n_kv = rd("Q"), rd("Q")
        out["version"], out["tensors"] = version, n_tensors
        for _ in range(n_kv):
            key = rstr()
            t = rd("I")
            val = rval(t)
            if want is None or key in want or not key.startswith("tokenizer.ggml."):
                out[key] = val
    return out


def _skip_strings(f, n: int) -> int:
    for _ in range(n):
        ln = struct.unpack("<Q", f.read(8))[0]
        f.seek(ln, 1)
    return n


def config_from_header(h: Dict[str, Any]) -> Dict[str, Any]:
    """The shape eki's sizing reads (config.json's names), from a header."""
    arch = str(h.get("general.architecture") or "")
    g = lambda k, d=0: h.get(f"{arch}.{k}", d)          # noqa: E731
    heads = int(g("attention.head_count") or 0)
    kv = int(g("attention.head_count_kv") or heads)
    embed = int(g("embedding_length") or 0)
    head_dim = int(g("attention.key_length") or (embed // heads if heads else 0))
    return {"model_type": arch, "num_hidden_layers": int(g("block_count") or 0),
            "num_attention_heads": heads, "num_key_value_heads": kv, "head_dim": head_dim,
            "hidden_size": embed, "max_position_embeddings": int(g("context_length") or 0)}
