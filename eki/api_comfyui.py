"""The ComfyUI workflows under the ComfyUI provider, for Settings: routes for apiroutes.

Everything is eki.providers.comfyui_workflows; this only turns it into JSON. A folder
given to add imports every API-format graph in it and its sub-folders, and says why it
skipped the rest (the editor's format, unreadable). Open starts ComfyUI on demand and
copies the graph into its own workflows folder (user/default/workflows/eki-<id>.json) so
it shows in ComfyUI's browser. Try is a new thread with one run pinned to the ComfyUI
provider and the workflow as its model: the engine runs it like any run, held like any
ComfyUI call. Errors are ValueErrors, so 400 {"error"}.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from . import models, providers, store
from .providers import comfyui_workflows as wf


def _provider() -> Tuple[str, Dict[str, Any]]:
    """The ComfyUI provider's name and entry; ValueError when there is none."""
    for name, cfg in providers.config().items():
        if cfg.get("kind") == "comfyui" and not cfg.get("off"):
            return name, cfg
    raise ValueError("there is no ComfyUI provider; add one in Settings → Providers")


def _id(body: dict) -> str:
    wid = body.get("id")
    if not isinstance(wid, str) or not wid:
        raise ValueError('"id" names a workflow')
    if wid not in {e["id"] for e in wf.entries()}:
        raise ValueError(f"no ComfyUI workflow named {wid!r}")
    return wid


def _all() -> List[Dict[str, Any]]:
    return [wf.describe(e["id"]) for e in wf.entries()]


def listing(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    where = providers.comfyui_dir()
    return {"workflows": _all(), "folder": str(wf.folder()), "comfyui_dir": str(where) if where else None}


def add(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    path = body.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError('"path" is a .json file or a folder')
    src = Path(path).expanduser()
    added: List[str] = []
    skipped: List[Dict[str, str]] = []
    if src.is_dir():
        for p in sorted(src.rglob("*.json")):
            try:
                added.append(wf.add(str(p)))
            except ValueError as e:
                skipped.append({"path": str(p), "why": str(e)})
    elif src.is_file():
        added.append(wf.add(str(src), wid=body.get("id") or None, label=body.get("label") or None))
    else:
        raise ValueError(f"{src} is neither a file nor a folder")
    return {"added": added, "skipped": skipped, "workflows": _all()}


def edit(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    inputs, defaults = body.get("inputs"), body.get("defaults")
    if inputs is not None and not isinstance(inputs, dict):
        raise ValueError('"inputs" is an object of {"node", "field"}')
    return wf.edit(_id(body), label=body.get("label"), inputs=inputs, defaults=defaults)


def reload(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return wf.describe(_id(body))                 # read again from its file; a bad file is its "error"


def remove(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    wf.remove(_id(body))
    return {"workflows": _all()}


def open_in_comfyui(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    wid = _id(body)
    name, cfg = _provider()
    where = providers.comfyui_dir()
    if where is None:
        raise ValueError("ComfyUI isn't on this Mac (no ComfyUI folder found)")
    src = Path(wf.describe(wid, look=False)["path"])
    if not src.is_file():
        raise ValueError(f"the workflow file {src} is missing")
    dest = where / "user" / "default" / "workflows" / f"eki-{wid}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    if not models.ensure(name):
        raise ValueError(f"ComfyUI ({name}) didn't start; the workflow is copied to {dest}")
    return {"url": str(cfg.get("base_url") or "http://127.0.0.1:8188").rstrip("/") + "/", "copied": str(dest)}


def try_it(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    wid = _id(body)
    inputs = body.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ValueError('"inputs" is an object')
    wf.check_inputs(wid, inputs)
    name, _ = _provider()
    label = wf.describe(wid, look=False)["label"]
    tid = store.create_thread(conn, f"try {label}", None)
    rid = store.create_run(conn, tid, json.dumps({"inputs": inputs}), provider=name, model=wid)
    return {"run": rid, "thread": tid}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/comfyui/workflows", listing),
    ("POST", r"/api/comfyui/workflows/add", add),
    ("POST", r"/api/comfyui/workflows/edit", edit),
    ("POST", r"/api/comfyui/workflows/reload", reload),
    ("POST", r"/api/comfyui/workflows/remove", remove),
    ("POST", r"/api/comfyui/workflows/open", open_in_comfyui),
    ("POST", r"/api/comfyui/workflows/try", try_it),
]
