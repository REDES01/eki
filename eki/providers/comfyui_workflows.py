"""ComfyUI workflows, listed under the ComfyUI provider the way a program lists its models.

The graphs are API-format JSON files ("Save (API)" in ComfyUI), kept as
visible files in `~/.eki/comfyui/workflows/`; the list, in display order, is
`~/.eki/comfyui/workflows.json`:

    {"workflows": [{"id": "draft", "label": "FLUX.2 klein — draft", "file": "draft.json",
                    "inputs": {"steps": {"node": "8", "field": "steps"}},
                    "defaults": {"width": 768}}, ...]}

`file` is relative to the folder or absolute. The inputs an agent may set are
the graph's `{{name}}` placeholders plus the ones `inputs` declares by node id
and field; `defaults` gives their values when a call doesn't. The standard
names are prompt, negative, seed, width, height and image (the source
picture): seed is random, width and height 1024 and negative "" unless
`defaults` says otherwise.

The first time, ensure() copies the five graphs eki ships
(eki/providers/comfyui_graphs/) in as the starting list; nothing here ever
overwrites a file the person may have edited. A target `comfyui:<id>` picks a
workflow like `claude:<model>` picks a model (modellist.listed); a routing
row with no workflow named draws with its own (ROW_WORKFLOWS).
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from . import comfyui_read as rd

SHIPPED = Path(__file__).with_name("comfyui_graphs")
DEFAULT = "draft"
STARTING = [("draft", "FLUX.2 klein — draft"), ("hq", "Qwen-Image 2.1 — high quality"),
            ("anime", "NoobAI-XL — anime"), ("edit", "klein — edit"), ("upscale", "klein — upscale")]
ROW_WORKFLOWS = {"image": "draft", "image-hq": "hq", "image-anime": "anime",
                 "image-edit": "edit", "image-upscale": "upscale"}
STANDARD = {"negative": "", "width": 1024, "height": 1024}
NUMBERS = ("seed", "width", "height")
RANDOM = "random"
API_HINT = "export it with Save (API) in ComfyUI"


def folder() -> Path:
    return paths.home() / "comfyui" / "workflows"


def index_path() -> Path:
    return paths.home() / "comfyui" / "workflows.json"


def ensure() -> None:
    """The starting list, the first time: the shipped graphs copied in."""
    if index_path().exists():
        return
    folder().mkdir(parents=True, exist_ok=True)
    for wid, _ in STARTING:
        dest = folder() / f"{wid}.json"
        if not dest.exists():
            shutil.copyfile(SHIPPED / f"{wid}.json", dest)
    _save([{"id": wid, "label": label, "file": f"{wid}.json", "inputs": {}, "defaults": {}}
           for wid, label in STARTING])


def _save(items: List[Dict[str, Any]]) -> None:
    index_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"workflows": items}, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, index_path())


def entries() -> List[Dict[str, Any]]:
    ensure()
    try:
        got = json.loads(index_path().read_text())
    except (OSError, ValueError):
        return []
    items = got.get("workflows") if isinstance(got, dict) else None
    return [e for e in items or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]


def entry(wid: str) -> Dict[str, Any]:
    for e in entries():
        if e["id"] == wid:
            return e
    raise KeyError(f"no ComfyUI workflow named {wid!r}")


def _path(e: Dict[str, Any]) -> Path:
    p = Path(str(e.get("file") or "")).expanduser()
    return p if p.is_absolute() else folder() / p


def _graph(e: Dict[str, Any]) -> Dict[str, Any]:
    got = json.loads(_path(e).read_text())
    if not isinstance(got, dict):
        raise ValueError("not a ComfyUI graph")
    return got


def _declared(e: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    got = e.get("inputs") if isinstance(e.get("inputs"), dict) else {}
    return {str(k): {"node": str(v.get("node")), "field": str(v.get("field"))}
            for k, v in got.items() if isinstance(v, dict) and v.get("node") is not None and v.get("field")}


def _default(name: str, e: Dict[str, Any], graph: Dict[str, Any], where: Optional[Dict[str, str]]) -> Any:
    own = e.get("defaults") if isinstance(e.get("defaults"), dict) else {}
    if name in own:
        return own[name]
    if name == "seed":
        return RANDOM
    if name in STANDARD:
        return STANDARD[name]
    if where:                                     # a declared input: what the graph has there
        return ((graph.get(where["node"]) or {}).get("inputs") or {}).get(where["field"])
    return ""


def _inputs(e: Dict[str, Any], graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    declared = _declared(e)
    out = [{"name": n, "node": nid, "field": f, "default": _default(n, e, graph, None), "source": "placeholder"}
           for n, nid, f in rd.placeholders(graph) if n not in declared]
    out += [{"name": n, "node": w["node"], "field": w["field"], "default": _default(n, e, graph, w),
             "source": "declared"} for n, w in declared.items()]
    return out


def _size(graph: Dict[str, Any], inputs: List[Dict[str, Any]], side: str) -> Optional[int]:
    v = rd.first(graph, side)
    if isinstance(v, str):
        m = rd.PLACEHOLDER.fullmatch(v.strip())
        v = next((i["default"] for i in inputs if m and i["name"] == m.group(1)), None)
    if v is None:
        v = rd.first(graph, "resolution")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def describe(wid: str, look: bool = True) -> Dict[str, Any]:
    """What the workflow is and what its graph says; `look` checks ComfyUI's folders for its models."""
    from . import comfyui_dir
    e = entry(wid)
    out: Dict[str, Any] = {"id": e["id"], "label": str(e.get("label") or e["id"]), "file": str(e.get("file") or ""),
                           "path": str(_path(e)), "error": "", "inputs": [], "models": [], "sampler": None,
                           "steps": None, "width": None, "height": None, "needs_source": False}
    try:
        graph = _graph(e)
    except (OSError, ValueError) as err:
        out["error"] = f"can't read {_path(e)}: {err}"
        return out
    files = rd.model_files(comfyui_dir()) if look else None
    inputs = _inputs(e, graph)
    steps = rd.first(graph, "steps")
    out.update(inputs=inputs, sampler=rd.first(graph, "sampler_name"),
               steps=steps if isinstance(steps, int) else None,
               width=_size(graph, inputs, "width"), height=_size(graph, inputs, "height"),
               needs_source=any(i["name"] == "image" for i in inputs),
               models=[{"name": m, "node": nid, "field": f, "found": None if files is None else m in files}
                       for m, nid, f in rd.models(graph)])
    if not any(True for _ in rd.nodes(graph)):
        out["error"] = f"{_path(e)} has no ComfyUI nodes; {API_HINT}"
    return out


def check_inputs(wid: str, values: Dict[str, Any]) -> None:
    d = describe(wid, look=False)
    known = [i["name"] for i in d["inputs"]]
    unknown = [k for k in values if k not in known]
    if unknown:
        raise ValueError(f"{d['label']} has no input {', '.join(unknown)}; "
                         f"it takes {', '.join(known) or 'none'}")


def _number(name: str, v: Any) -> Any:
    if name in NUMBERS and isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    return v


def build(wid: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """The workflow's graph filled in: its defaults, then `values` (seed random when neither gives one)."""
    check_inputs(wid, values)
    e = entry(wid)
    graph = _graph(e)
    filled: Dict[str, Any] = {}
    for i in _inputs(e, graph):
        n = i["name"]
        v = values[n] if n in values else i["default"]
        if n == "seed" and (v is None or v == RANDOM or v == ""):
            v = random.randint(0, 2 ** 32 - 1)
        filled[n] = _number(n, v)
    graph = rd.fill(graph, {n: ("" if v is None else v) for n, v in filled.items()})
    own = e.get("defaults") if isinstance(e.get("defaults"), dict) else {}
    for n, w in _declared(e).items():
        node = graph.get(w["node"])
        if n not in values and n not in own and n != "seed":
            continue                              # the graph's own value stays
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
            node["inputs"][w["field"]] = filled[n]
    return graph


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "workflow"


def add(path: str, wid: Optional[str] = None, label: Optional[str] = None) -> str:
    """Copy an API-format graph in; its id (from the file name, made unique)."""
    src = Path(path).expanduser()
    try:
        graph = json.loads(src.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"can't read {src}: {e}") from e
    if isinstance(graph, dict) and isinstance(graph.get("prompt"), dict) and not any(True for _ in rd.nodes(graph)):
        graph = graph["prompt"]                   # the body /prompt takes
    if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
        raise ValueError(f"{src.name} is ComfyUI's editor format; {API_HINT}")
    if not any(True for _ in rd.nodes(graph)):
        raise ValueError(f"{src.name} has no ComfyUI nodes (class_type); {API_HINT}")
    items = entries()
    taken = {e["id"] for e in items}
    base = _slug(wid or src.stem)
    new, n = base, 2
    while new in taken or (folder() / f"{new}.json").exists():
        new, n = f"{base}-{n}", n + 1
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / f"{new}.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n")
    items.append({"id": new, "label": label or src.stem, "file": f"{new}.json", "inputs": {}, "defaults": {}})
    _save(items)
    return new


def edit(wid: str, label: Optional[str] = None, inputs: Optional[Dict[str, Any]] = None,
         defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    items = entries()
    e = next((x for x in items if x["id"] == wid), None)
    if e is None:
        raise KeyError(f"no ComfyUI workflow named {wid!r}")
    if inputs is not None:
        graph = _graph(e)
        for name, w in inputs.items():
            if not isinstance(w, dict) or str(w.get("node")) not in graph or not w.get("field"):
                raise ValueError(f"input {name!r} needs a node of this graph and a field")
        e["inputs"] = {str(k): {"node": str(v["node"]), "field": str(v["field"])} for k, v in inputs.items()}
    if defaults is not None:
        if not isinstance(defaults, dict):
            raise ValueError("defaults must be an object")
        e["defaults"] = dict(defaults)
    if label is not None and label.strip():
        e["label"] = label.strip()
    _save(items)
    return describe(wid)


def remove(wid: str) -> None:
    """Drop it from the list; a file in the folder goes to removed/ (never deleted)."""
    items = entries()
    e = next((x for x in items if x["id"] == wid), None)
    if e is None:
        raise KeyError(f"no ComfyUI workflow named {wid!r}")
    _save([x for x in items if x["id"] != wid])
    src = _path(e)
    if src.is_file() and src.resolve().parent == folder().resolve():
        dest_dir = folder() / "removed"
        dest_dir.mkdir(exist_ok=True)
        dest, n = dest_dir / src.name, 2
        while dest.exists():
            dest, n = dest_dir / f"{src.stem}-{n}{src.suffix}", n + 1
        shutil.move(str(src), str(dest))


def _line(d: Dict[str, Any]) -> str:
    if d["error"]:
        return d["error"]
    main = [m["name"] for m in d["models"] if m["field"] in rd.MAIN_MODEL] or [m["name"] for m in d["models"]]
    parts = [", ".join(rd.short(m) for m in main), d["sampler"] or "",
             f"{d['steps']} steps" if d["steps"] else "",
             f"{d['width']}×{d['height']}" if d["width"] and d["height"] else ""]
    return " · ".join(p for p in parts if p)


def listed() -> List[Dict[str, Any]]:
    """The workflows as modellist.listed() rows."""
    out = []
    for e in entries():
        d = describe(e["id"], look=False)
        out.append({"model": d["id"], "label": d["label"], "description": _line(d), "resolved": None,
                    "efforts": None, "default_effort": None, "is_default": d["id"] == DEFAULT,
                    "floating": False})
    return out


def for_row(row: Optional[str]) -> str:
    """The workflow a picture row draws with when its target names none."""
    known = [e["id"] for e in entries()]
    wid = ROW_WORKFLOWS.get(row or "", DEFAULT)
    if wid in known:
        return wid
    return DEFAULT if DEFAULT in known or not known else known[0]
