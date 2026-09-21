# SPDX-License-Identifier: Apache-2.0
"""What a local model is, read from its own files.

A model on this Mac is a particular build — Qwen3.8-27B at 4 bits, group
size 64 — not the name on the box, and everything eki decides about it
follows from that build: what it costs in memory, how much context it can
take, whether a harness can call tools through it, how it wants to be
sampled, whether thinking can be switched off. None of that is typed in.
The profile is rebuilt from the cached files every time the providers
load, so a model added by hand is described exactly like one eki set up,
and a build swapped under the same name is noticed.

Quality is the one thing a profile never claims: a 4-bit and an 8-bit
build of the same base share a public prior and are measured separately
(see eki/measure.py) — there is no honest "×0.97 for 4-bit".
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from . import deploy, public_scores

#: overhead the server itself adds to the weights once loaded (runtime,
#: compiled kernels, the tokenizer): what `gb` means before it's measured
OVERHEAD_GB = 1.5


@dataclass
class Profile:
    repo: str
    base: str                       # the build's family name, quant stripped
    family: str                     # model_type from the config
    params_b: float                 # from the name; 0 when it doesn't say
    quant_bits: int                 # 0 = not quantized (bf16/fp16)
    quant_group: int
    weights_gb: float               # safetensors on disk
    native_context: int
    layers: int
    attention_layers: int           # the ones whose cache grows (hybrid models)
    kv_per_token_kb: float
    sampling: Dict[str, Any] = field(default_factory=dict)
    thinking_switch: bool = False   # chat template takes enable_thinking
    tools: Optional[bool] = None    # chat template renders tools / tool calls; None = no template read
    vision_weights: bool = False    # the build has a vision tower (the server may not serve it)
    config: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def hybrid(self) -> bool:
        return 0 < self.attention_layers < self.layers

    def describe(self) -> str:
        parts = []
        if self.params_b:
            parts.append(f"{self.params_b:g}B")
        parts.append(f"{self.quant_bits}-bit" + (f" (group {self.quant_group})" if self.quant_group else "")
                     if self.quant_bits else "16-bit")
        parts.append(f"{self.weights_gb:.1f} GB of weights")
        if self.hybrid:
            parts.append(f"hybrid · {self.attention_layers} of {self.layers} layers keep a cache")
        if self.tools is not None:
            parts.append("tool calling" if self.tools else "no tool calling in its template")
        if self.thinking_switch:
            parts.append("thinking switch")
        if self.vision_weights:
            parts.append("vision weights")
        return " · ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("config", None)
        d["hybrid"] = self.hybrid
        d["summary"] = self.describe()
        return d


_PARAMS = re.compile(r"(\d+(?:\.\d+)?)[bB](?![a-zA-Z])")


def params_from_name(repo: str) -> float:
    m = _PARAMS.findall(repo.split("/")[-1])
    return float(m[-1]) if m else 0.0


def _text(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("text_config") or config.get("llm_config") or config


def read(repo: str) -> Optional[Profile]:
    """The profile of a downloaded build, or None when its files aren't here."""
    snap = deploy.snapshot_dir(repo)
    if snap is None:
        return None
    try:
        config = json.loads((snap / "config.json").read_text())
    except (OSError, ValueError):
        return None
    return from_files(repo, config, snap)


def from_files(repo: str, config: Dict[str, Any], snap: Optional[Path]) -> Profile:
    text = _text(config)
    quant = config.get("quantization") or config.get("quantization_config") or {}
    layers = int(text.get("num_hidden_layers") or 0)
    types = text.get("layer_types")
    attention = (sum(1 for t in types if t == "full_attention")
                 if isinstance(types, list) and types else layers)
    gen: Dict[str, Any] = {}
    template: Optional[str] = None
    weights = 0.0
    if snap is not None:
        try:
            gen = json.loads((snap / "generation_config.json").read_text())
        except (OSError, ValueError):
            pass
        for name in ("chat_template.jinja", "tokenizer_config.json"):
            try:
                template = (template or "") + (snap / name).read_text(errors="replace")
            except OSError:
                pass
        for f in snap.glob("*.safetensors"):
            try:
                weights += f.stat().st_size
            except OSError:
                pass
    return Profile(
        repo=repo, base=public_scores.base_name(repo), family=str(config.get("model_type") or text.get("model_type") or ""),
        params_b=params_from_name(repo),
        quant_bits=int(quant.get("bits") or 0), quant_group=int(quant.get("group_size") or 0),
        weights_gb=round(weights / 1024**3, 2),
        native_context=int(text.get("max_position_embeddings") or 0),
        layers=layers, attention_layers=attention,
        kv_per_token_kb=round(deploy.kv_gb(config, 1 << 20) / (1 << 20) * 1024 * 1024, 1),
        sampling=deploy.sampling(gen),
        thinking_switch=bool(template and "enable_thinking" in template),
        tools=None if template is None else ("tool_call" in template or "tools" in template),
        vision_weights=bool(config.get("vision_config")),
        config=config,
    )
