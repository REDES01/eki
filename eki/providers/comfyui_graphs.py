"""The ComfyUI graphs eki ships, one per picture row.

Each row the router can choose has its own API-format graph in the
`comfyui_graphs/` folder beside this file:

    image          draft.json    FLUX.2-klein 4B, text to picture, 4 steps
    image-hq       hq.json       Qwen-Image 2.1 Q8 (GGUF), 30 steps, 1024²
    image-anime    anime.json    NoobAI-XL (SDXL), a fixed anime negative prompt
    image-edit     edit.json     klein 4B shown the source picture, told what to change
    image-upscale  upscale.json  the source at 2×, a light klein pass over it

A provider entry may name its own graph for any row, `"graphs": {"image-hq":
"~/my.json"}`; the old single `"workflow"` key still means the `image` graph.
A row eki doesn't know draws a draft.

In a graph, the strings "{{prompt}}", "{{seed}}", "{{width}}", "{{height}}"
and "{{image}}" (the uploaded source's name) are filled in; a string that is
exactly a placeholder becomes the value itself, so numbers stay numbers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

FOLDER = Path(__file__).with_name("comfyui_graphs")
DEFAULT_ROW = "image"

#: row key -> (graph file, kind label, model)
GRAPHS: Dict[str, Tuple[str, str, str]] = {
    "image": ("draft.json", "draft", "flux-2-klein-4b"),
    "image-hq": ("hq.json", "hq", "qwen-image-2.1-Q8"),
    "image-anime": ("anime.json", "anime", "NoobAI-XL-v1.1"),
    "image-edit": ("edit.json", "edit", "flux-2-klein-4b"),
    "image-upscale": ("upscale.json", "upscale", "flux-2-klein-4b"),
}


def _known(row: Optional[str]) -> str:
    return row if row in GRAPHS else DEFAULT_ROW


def kind_of(row: Optional[str]) -> Tuple[str, str]:
    """(kind label, model) for a row, e.g. ("draft", "flux-2-klein-4b")."""
    _, kind, model = GRAPHS[_known(row)]
    return kind, model


def graph_path(row: Optional[str], cfg: Dict[str, Any]) -> Path:
    """The graph a row draws with: the entry's own for it, else the shipped one."""
    own = cfg.get("graphs") or {}
    if row and own.get(row):
        return Path(own[row]).expanduser()
    key = _known(row)
    if key != row and own.get(key):
        return Path(own[key]).expanduser()
    if key == DEFAULT_ROW and cfg.get("workflow"):
        return Path(cfg["workflow"]).expanduser()
    return FOLDER / GRAPHS[key][0]


def load(row: Optional[str], cfg: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """The row's graph, read and filled in."""
    return fill(json.loads(graph_path(row, cfg).read_text()), values)


def fill(value: Any, values: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value in values:
            return values[value]
        for key, v in values.items():
            value = value.replace(key, str(v))
        return value
    if isinstance(value, dict):
        return {k: fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, values) for v in value]
    return value
