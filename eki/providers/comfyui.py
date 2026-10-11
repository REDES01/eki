"""ComfyUI, running on this Mac, for pictures.

eki may start ComfyUI for a run and stop it again when idle, as it does the
local model (see models.py); one the person started is left alone. This
module only talks HTTP: it posts a graph in API format to `/prompt`, polls
`/history/<id>` and fetches what was made through `/view` into
`~/.eki/images/<run>/`.

It draws with a workflow from the list under the provider
(`comfyui_workflows.py`, `~/.eki/comfyui/workflows.json`): the run's target
names it (`comfyui:hq` → `turn.extra["model"]`), else the run's row picks it
(`image-hq` → hq). The prompt is the workflow's `prompt` input, or a JSON
object `{"inputs": {"prompt": ..., "seed": 7}}` sets several; an input the
workflow doesn't have fails the run. A workflow with an `image` input works
on a source picture, `turn.images[0]`, uploaded to `/upload/image` first.

The prompt id is the session: a run eki restarted mid-drawing polls it
again instead of uploading or drawing twice.
"""
from __future__ import annotations

import json
import mimetypes
import re
import uuid
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from . import comfyui_workflows as wf
from .base import Emit, Outcome, Provider, Turn

POLL = 1.0
NO_SOURCE = "no picture to edit — attach one or draw one first"
VERBS = {"edit": "Edited", "upscale": "Upscaled"}           # by workflow id; the rest "Drew"
# what an upscale request says when it says nothing about the picture itself
UPSCALE_ONLY = re.compile(r"\b(please|can|could|you|would|it|this|that|the|a|an|picture|image|photo|"
                          r"upscale|upscaled|enlarge|make|higher|resolution|bigger|version|of|to|by|"
                          r"\d+(\.\d+)?x|x\d+|times|twice|double|size)\b", re.I)


class ComfyError(Exception):
    """ComfyUI said no: its own message."""


class Comfyui(Provider):
    kind = "comfyui"

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.base = cfg.get("base_url", "http://127.0.0.1:8188").rstrip("/")
        self.poll = float(cfg.get("poll", POLL))

    def available(self) -> tuple:
        try:
            self._get("/system_stats", timeout=1.5)
            return True, ""
        except (OSError, ValueError):
            return False, f"ComfyUI isn't running at {self.base}"

    # ---- http ----------------------------------------------------------------------------

    def _get(self, path: str, timeout: float = 10) -> Any:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.load(r)

    def _post_prompt(self, graph: Dict[str, Any]) -> str:
        req = urllib.request.Request(self.base + "/prompt", data=json.dumps({"prompt": graph}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                got = json.load(r)
        except urllib.error.HTTPError as e:
            raise ComfyError(_refusal(e.read())) from e
        if got.get("node_errors"):
            raise ComfyError(_refusal(json.dumps(got).encode()))
        return str(got["prompt_id"])

    def _upload(self, path: str, run_id: str) -> str:
        """Send the source picture to ComfyUI's input folder; its name there for LoadImage."""
        src = Path(path)
        name = f"eki_src_{run_id}_{src.name}"
        kind = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: {kind}\r\n\r\n").encode() + src.read_bytes() + (
                f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
                f"true\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request(self.base + "/upload/image", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                got = json.load(r)
        except urllib.error.HTTPError as e:
            raise ComfyError(_refusal(e.read())) from e
        sub = got.get("subfolder") or ""
        return f"{sub}/{got['name']}" if sub else str(got["name"])

    def _queued(self, pid: str) -> bool:
        """Still waiting or drawing (ComfyUI may have lost it in a restart of its own)."""
        try:
            q = self._get("/queue")
        except (OSError, ValueError):
            return True
        return any(len(item) > 1 and item[1] == pid
                   for key in ("queue_running", "queue_pending") for item in q.get(key) or [])

    # ---- the workflow --------------------------------------------------------------------

    def workflow(self, turn: Turn) -> str:
        """The workflow this turn draws with: the target's, else the row's."""
        return str(turn.extra.get("model") or "") or wf.for_row(turn.extra.get("row"))

    def graph(self, wid: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """The workflow's graph filled in (`image` is the uploaded source's name)."""
        prompt = str(inputs.get("prompt") or "")
        if wid == wf.ROW_WORKFLOWS["image-upscale"] and "prompt" in inputs \
                and not UPSCALE_ONLY.sub("", prompt).strip(" \t\n.,!?"):
            inputs = {**inputs, "prompt": "high detail"}
        return wf.build(wid, inputs)

    # ---- a turn --------------------------------------------------------------------------

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        from ..worker import CARRY_ON         # the worker imports the providers
        pid: Optional[str] = None
        wid = self.workflow(turn)
        inputs = _inputs(turn.prompt)
        detail = str(inputs.get("prompt") or turn.prompt)
        try:
            info = wf.describe(wid, look=False)
        except KeyError:
            return Outcome("failed", f"no ComfyUI workflow named {wid!r}")
        label = info["label"]
        try:
            if turn.resume and (turn.prompt == CARRY_ON or not turn.prompt.strip()):
                # eki restarted mid-drawing: the same picture, not a second one
                if self._queued(turn.resume) or self._entry(turn.resume):
                    pid, detail, inputs = turn.resume, "picture", {}
                else:
                    emit("note", {"text": "ComfyUI no longer knows that drawing; nothing to carry on"})
                    return Outcome("failed", "ComfyUI lost the drawing (was it restarted?)")
            if pid is None:
                if info["error"]:
                    return Outcome("failed", f"ComfyUI workflow {wid}: {info['error']}")
                try:
                    wf.check_inputs(wid, inputs)
                except ValueError as e:
                    return Outcome("failed", str(e))
                if info["needs_source"]:
                    src = inputs.get("image")
                    src = src if isinstance(src, str) and Path(src).expanduser().is_file() else \
                        (turn.images[0] if turn.images else None)
                    if src is None and "image" not in inputs:
                        return Outcome("failed", NO_SOURCE)
                    if src is not None:
                        inputs = {**inputs, "image": self._upload(str(Path(src).expanduser()),
                                                                  turn.run_id or "run")}
                pid = self._post_prompt(self.graph(wid, inputs))
                emit("session", {"id": pid})
            entry = self._wait(pid, turn)
            if entry is None:
                return Outcome("cancelled", "stopped")
            made = self._download(entry, turn.run_id or pid)
        except ComfyError as e:
            return Outcome("failed", f"ComfyUI: {e}")
        except (OSError, ValueError, KeyError) as e:
            return Outcome("failed", f"ComfyUI at {self.base}: {e}")
        if not made:
            return Outcome("failed", "ComfyUI finished but made no picture")
        row = turn.extra.get("row")
        for path in made:
            emit("tool", {"name": "image", "detail": f"{detail} ({label})", "path": str(path),
                          "prompt": detail, "workflow": wid, "inputs": inputs, "row": row or "image"})
        verb = VERBS.get(wid, "Drew")
        emit("text", {"text": f"{verb} {len(made)} picture{'s' if len(made) > 1 else ''} ({label}):\n"
                              + "\n".join(str(p) for p in made)})
        return Outcome("done", finished=True)

    def _entry(self, pid: str) -> Optional[Dict[str, Any]]:
        got = self._get("/history/" + urllib.parse.quote(pid))
        return got.get(pid) if isinstance(got, dict) else None

    def _wait(self, pid: str, turn: Turn) -> Optional[Dict[str, Any]]:
        """Poll until the drawing is finished (its history entry) or the run is stopped (None)."""
        while not turn.stop.is_set():
            entry = self._entry(pid)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyError(_failure(status))
                if status.get("completed", True) or entry.get("outputs"):
                    return entry
            turn.stop.wait(self.poll)
        return None

    def _download(self, entry: Dict[str, Any], run_id: str) -> List[Path]:
        folder = paths.home() / "images" / run_id
        made: List[Path] = []
        for node in (entry.get("outputs") or {}).values():
            for img in node.get("images") or []:
                if img.get("type") == "temp":          # previews, not results
                    continue
                name = Path(img["filename"]).name
                query = urllib.parse.urlencode({"filename": img["filename"],
                                                "subfolder": img.get("subfolder", ""),
                                                "type": img.get("type", "output")})
                with urllib.request.urlopen(f"{self.base}/view?{query}", timeout=60) as r:
                    data = r.read()
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / name
                dest.write_bytes(data)
                made.append(dest.resolve())
        return made


def _inputs(prompt: str) -> Dict[str, Any]:
    """`{"inputs": {...}}` sets several inputs; any other prompt is the prompt."""
    text = (prompt or "").strip()
    if text.startswith("{"):
        try:
            got = json.loads(text)
        except ValueError:
            got = None
        if isinstance(got, dict) and isinstance(got.get("inputs"), dict):
            return dict(got["inputs"])
    return {"prompt": prompt}


def _refusal(body: bytes) -> str:
    """ComfyUI's answer to a workflow it won't run, in a line."""
    try:
        got = json.loads(body or b"{}")
    except ValueError:
        return body.decode("utf-8", "replace")[:300] or "refused the workflow"
    parts: List[str] = []
    err = got.get("error")
    if isinstance(err, dict):
        parts.append(err.get("message") or "")
    elif err:
        parts.append(str(err))
    for node, info in (got.get("node_errors") or {}).items():
        for e in info.get("errors") or []:
            parts.append(f"{info.get('class_type', node)}: {e.get('message', '')}"
                         + (f" ({e['details']})" if e.get("details") else ""))
    return "; ".join(p for p in parts if p) or "refused the workflow"


def _failure(status: Dict[str, Any]) -> str:
    """Why a drawing failed, from its history status."""
    for msg in status.get("messages") or []:
        if len(msg) > 1 and msg[0] == "execution_error":
            info = msg[1] or {}
            where = info.get("node_type") or info.get("node_id") or ""
            return f"{where}: {info.get('exception_message', '').strip()}".strip(": ")
    return "the drawing failed"
