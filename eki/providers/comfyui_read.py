"""What an API-format ComfyUI graph says of itself, read without running it.

A graph is `{"<node id>": {"class_type": ..., "inputs": {...}}}`. From it eki
reads the inputs an agent may set (`{{name}}` placeholders in any string),
the models it loads (loader fields such as `unet_name` or `ckpt_name` naming
a .safetensors/.gguf/… file), its sampler and steps, and its size. fill()
puts values in: a string that is exactly a placeholder becomes the value
itself, so numbers stay numbers. Nothing here touches ComfyUI or the disk
beyond looking for model files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")
MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin")
#: the loader fields that name the model a graph draws with (others: text encoders, VAE, LoRA …)
MAIN_MODEL = ("unet_name", "ckpt_name")


def nodes(graph: Any) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """(id, node) for each node that is one (has a class_type)."""
    for nid, node in (graph.items() if isinstance(graph, dict) else []):
        if isinstance(node, dict) and isinstance(node.get("class_type"), str):
            yield str(nid), node


def _fields(graph: Any) -> Iterator[Tuple[str, str, Any]]:
    for nid, node in nodes(graph):
        ins = node.get("inputs")
        for field, value in (ins.items() if isinstance(ins, dict) else []):
            yield nid, field, value


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def placeholders(graph: Any) -> List[Tuple[str, str, str]]:
    """(name, node, field) for each `{{name}}`, first place it appears; graph order."""
    out: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()
    for nid, field, value in _fields(graph):
        for s in _strings(value):
            for name in PLACEHOLDER.findall(s):
                if name not in seen:
                    seen.add(name)
                    out.append((name, nid, field))
    return out


def models(graph: Any) -> List[Tuple[str, str, str]]:
    """(file name, node, field) for each model file a loader names."""
    return [(value, nid, field) for nid, field, value in _fields(graph)
            if field.endswith("_name") and isinstance(value, str) and value.lower().endswith(MODEL_EXT)]


def first(graph: Any, field: str) -> Any:
    """The first literal value of `field` in the graph (a link or None skipped)."""
    for _, f, value in _fields(graph):
        if f == field and value is not None and not isinstance(value, list):
            return value
    return None


def model_files(root: Optional[Path]) -> Optional[Set[str]]:
    """Every file name under ComfyUI's models folder; None when there is no ComfyUI here."""
    if root is None:
        return None
    folder = root / "models"
    if not folder.is_dir():
        return set()
    return {p.name for p in folder.rglob("*") if p.is_file() or p.is_symlink()}


def fill(value: Any, values: Dict[str, Any]) -> Any:
    """`value` with each `{{name}}` in `values` filled in."""
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value.strip())
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), value)
    if isinstance(value, dict):
        return {k: fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, values) for v in value]
    return value


def short(model: str) -> str:
    """`flux-2-klein-4b.safetensors` → `flux-2-klein-4b`."""
    for ext in MODEL_EXT:
        if model.lower().endswith(ext):
            return model[: -len(ext)]
    return model
