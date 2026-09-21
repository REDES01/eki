# SPDX-License-Identifier: Apache-2.0
"""ComfyUI workflows as the model.

ComfyUI runs whatever graph it's handed; the checkpoint, the sampler and
the wiring are the workflow's business, not eki's. So an image model in
eki is a workflow — exported from ComfyUI in API format — plus a small
set of bindings: which node input takes the prompt, which the size, seed
and steps, which loads a source picture, which node saves the result.
eki works the bindings out from the graph itself (a CLIPTextEncode whose
text feeds a sampler's `positive` is the prompt; an Empty…LatentImage's
width/height is the size), checks the graph against what that ComfyUI
reports it has (node classes, checkpoint files), and at request time
fills the slots and posts it. Nothing about where ComfyUI is installed
or which model it uses is eki's to know.

A few built-in graphs cover the common families so a person who has a
checkpoint but no workflow can start from one (see TEMPLATES).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOME = Path("~/.eki/workflows").expanduser()

Graph = Dict[str, Dict[str, Any]]


@dataclass
class Slot:
    node: str
    input: str


@dataclass
class Bindings:
    prompt: Optional[Slot] = None
    negative: Optional[Slot] = None
    width: Optional[Slot] = None
    height: Optional[Slot] = None
    seed: List[Slot] = field(default_factory=list)
    steps: Optional[Slot] = None
    image: Optional[Slot] = None            # LoadImage: makes edits possible
    output: Optional[Slot] = None           # SaveImage.filename_prefix

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Bindings":
        def slot(v):
            return Slot(**v) if isinstance(v, dict) and v.get("node") else None
        return cls(prompt=slot(d.get("prompt")), negative=slot(d.get("negative")),
                   width=slot(d.get("width")), height=slot(d.get("height")),
                   seed=[Slot(**s) for s in d.get("seed") or [] if isinstance(s, dict)],
                   steps=slot(d.get("steps")), image=slot(d.get("image")), output=slot(d.get("output")))

    def describe(self) -> str:
        parts = []
        if self.prompt:
            parts.append("prompt")
        if self.negative:
            parts.append("negative prompt")
        if self.width and self.height:
            parts.append("size")
        if self.seed:
            parts.append("seed")
        if self.steps:
            parts.append("steps")
        if self.image:
            parts.append("source picture (edits)")
        return ", ".join(parts) or "nothing eki can fill"


# ---- reading a graph ---------------------------------------------------------

def _is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _feeds(graph: Graph, node: str) -> List[Tuple[str, str]]:
    """(node, input) pairs that take `node`'s output: where it goes."""
    out = []
    for nid, n in graph.items():
        for k, v in (n.get("inputs") or {}).items():
            if _is_link(v) and v[0] == node:
                out.append((nid, k))
    return out


def _downstream_role(graph: Graph, node: str, depth: int = 4) -> str:
    """Does this conditioning end up as a sampler's positive or negative?"""
    frontier = [node]
    for _ in range(depth):
        nxt = []
        for n in frontier:
            for nid, k in _feeds(graph, n):
                if k in ("positive", "negative"):
                    return k
                nxt.append(nid)
        frontier = nxt
        if not frontier:
            break
    return ""


def infer(graph: Graph) -> Bindings:
    """The slots eki can fill, read off the graph's shape."""
    b = Bindings()
    texts: List[Tuple[str, str, str]] = []                 # (node, input, role)
    for nid, n in graph.items():
        cls = str(n.get("class_type") or "")
        inputs = n.get("inputs") or {}
        for k, v in inputs.items():
            if isinstance(v, str) and k in ("text", "prompt", "positive_prompt", "string") \
                    and ("TextEncode" in cls or "Prompt" in cls or "Text" in cls):
                texts.append((nid, k, _downstream_role(graph, nid)))
        if cls == "LoadImage" and "image" in inputs and b.image is None:
            b.image = Slot(nid, "image")
        if cls in ("SaveImage", "SaveAnimatedWEBP", "SaveAnimatedPNG") and b.output is None:
            b.output = Slot(nid, "filename_prefix")
        if "width" in inputs and "height" in inputs and not _is_link(inputs["width"]) \
                and ("Latent" in cls or "Empty" in cls) and b.width is None:
            b.width, b.height = Slot(nid, "width"), Slot(nid, "height")
        for k in ("seed", "noise_seed"):
            if k in inputs and not _is_link(inputs[k]):
                b.seed.append(Slot(nid, k))
        if "steps" in inputs and not _is_link(inputs["steps"]) and b.steps is None:
            b.steps = Slot(nid, "steps")
    positives = [t for t in texts if t[2] == "positive"]
    negatives = [t for t in texts if t[2] == "negative"]
    others = [t for t in texts if not t[2]]
    if positives:
        b.prompt = Slot(positives[0][0], positives[0][1])
    elif others:
        b.prompt = Slot(others[0][0], others[0][1])
    if negatives:
        b.negative = Slot(negatives[0][0], negatives[0][1])
    if b.width is None:                                     # a scheduler carrying the size (FLUX.2)
        for nid, n in graph.items():
            inputs = n.get("inputs") or {}
            if "width" in inputs and "height" in inputs and not _is_link(inputs["width"]):
                b.width, b.height = Slot(nid, "width"), Slot(nid, "height")
                break
    return b


def size_slots(graph: Graph) -> List[Tuple[str, str]]:
    """Every literal width/height pair: an edit graph may carry several."""
    out = []
    for nid, n in graph.items():
        inputs = n.get("inputs") or {}
        if "width" in inputs and "height" in inputs and not _is_link(inputs["width"]):
            out.append((nid, "width"))
    return out


def batch_slots(graph: Graph) -> List[Tuple[str, str]]:
    """Every literal batch_size: where "four of them" goes. Read off the graph
    each time rather than bound once, so workflows added before eki drew in
    batches need nothing done to them. A graph without one (it starts from a
    loaded picture, say) can still be queued that many times."""
    return [(nid, "batch_size") for nid, n in graph.items()
            if isinstance((n.get("inputs") or {}).get("batch_size"), int)]


# ---- checking against a ComfyUI ---------------------------------------------

def problems(graph: Graph, object_info: Dict[str, Any]) -> List[str]:
    """What this ComfyUI would refuse: node classes it doesn't have, and
    model files a loader names that aren't in its folders."""
    out = []
    for nid, n in graph.items():
        cls = str(n.get("class_type") or "")
        spec = object_info.get(cls)
        if spec is None:
            out.append(f"node {nid}: no {cls} in this ComfyUI (a missing custom node?)")
            continue
        required = ((spec.get("input") or {}).get("required") or {})
        for k, v in (n.get("inputs") or {}).items():
            choices = required.get(k)
            if isinstance(choices, list) and choices and isinstance(choices[0], list) and \
                    isinstance(v, str) and choices[0] and isinstance(choices[0][0], str) and v not in choices[0]:
                out.append(f"node {nid}: {cls} has no {k} called {v!r}")
    return out


def files_available(object_info: Dict[str, Any]) -> Dict[str, List[str]]:
    """The model files ComfyUI can see, by loader kind."""
    def choices(cls: str, key: str) -> List[str]:
        spec = ((object_info.get(cls) or {}).get("input") or {}).get("required") or {}
        v = spec.get(key)
        return list(v[0]) if isinstance(v, list) and v and isinstance(v[0], list) else []
    return {"checkpoints": choices("CheckpointLoaderSimple", "ckpt_name"),
            "unets": choices("UNETLoader", "unet_name"),
            "clips": choices("CLIPLoader", "clip_name"),
            "vaes": choices("VAELoader", "vae_name"),
            "loras": choices("LoraLoader", "lora_name")}


# ---- filling a graph ---------------------------------------------------------

def fill(graph: Graph, b: Bindings, *, prompt: str, negative: str = "", width: int = 0,
         height: int = 0, seed: Optional[int] = None, steps: int = 0, image: str = "",
         prefix: str = "eki", batch: int = 0) -> Graph:
    g = json.loads(json.dumps(graph))
    if batch:
        for nid, k in batch_slots(g):
            g[nid]["inputs"][k] = batch
    if b.prompt:
        g[b.prompt.node]["inputs"][b.prompt.input] = prompt
    if b.negative and negative:
        g[b.negative.node]["inputs"][b.negative.input] = negative
    if width and height:
        for nid, _ in size_slots(g):
            g[nid]["inputs"]["width"], g[nid]["inputs"]["height"] = width, height
    if seed is not None:
        for s in b.seed:
            g[s.node]["inputs"][s.input] = seed
    if steps and b.steps:
        g[b.steps.node]["inputs"][b.steps.input] = steps
    if image and b.image:
        g[b.image.node]["inputs"][b.image.input] = image
    if b.output:
        g[b.output.node]["inputs"][b.output.input] = prefix
    return g


# ---- built-in graphs ---------------------------------------------------------

def sdxl_graph(ckpt: str) -> Graph:
    """The classic checkpoint graph: SD 1.5, SDXL and their fine-tunes."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, watermark", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                                                  "latent_image": ["4", 0], "seed": 0, "steps": 25, "cfg": 6.0,
                                                  "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "eki", "images": ["6", 0]}},
    }


def flux2_klein_graph(unet: str = "flux-2-klein-4b.safetensors", clip: str = "qwen_3_4b.safetensors",
                      vae: str = "flux2-vae.safetensors") -> Graph:
    from .adapters.comfyui import flux2_graph
    g = flux2_graph("", 1024, 1024, 4, 0, "eki")
    g["1"]["inputs"]["unet_name"] = unet
    g["2"]["inputs"]["clip_name"] = clip
    g["3"]["inputs"]["vae_name"] = vae
    return g


TEMPLATES = {
    "checkpoint": {"title": "Checkpoint (SD 1.5 / SDXL)", "make": sdxl_graph, "needs": "checkpoints",
                   "note": "25 steps, cfg 6, a negative prompt: the standard graph for a .safetensors checkpoint"},
    "flux2-klein": {"title": "FLUX.2-klein", "make": flux2_klein_graph, "needs": "unets",
                    "note": "4 steps, distilled: fast and good; needs the klein UNet, its Qwen text encoder and VAE"},
}


def family_of(filename: str) -> str:
    """A guess at which template a model file wants, from its name."""
    n = filename.lower()
    if "flux-2-klein" in n or "flux2-klein" in n:
        return "flux2-klein"
    return "checkpoint"


# ---- storage -----------------------------------------------------------------

def save(key: str, graph: Graph) -> str:
    HOME.mkdir(parents=True, exist_ok=True)
    path = HOME / f"{key}.json"
    path.write_text(json.dumps(graph, indent=1))
    return str(path)


def load(path: str) -> Graph:
    return json.loads(Path(path).expanduser().read_text())


def parse(text: str) -> Graph:
    """An API-format export; the UI format (with `nodes`/`links`) is refused
    with a pointer to the right export."""
    data = json.loads(text)
    if isinstance(data, dict) and "nodes" in data and "links" in data:
        raise ValueError("this is the editor's own format — in ComfyUI use Workflow → Export (API)")
    if not isinstance(data, dict) or not all(isinstance(v, dict) and "class_type" in v for v in data.values()):
        raise ValueError("not a ComfyUI API-format workflow")
    return data
