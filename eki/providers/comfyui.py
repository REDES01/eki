"""ComfyUI, running on this Mac, for pictures.

eki doesn't start or stop ComfyUI; it posts a workflow in API format to
`/prompt`, polls `/history/<id>` and fetches what was made through `/view`
into `~/.eki/images/<run>/`. The workflow is `comfyui_default.json` beside
this file (FLUX.2-klein, text to image) unless the entry names its own:

    {"kind": "comfyui", "base_url": "http://127.0.0.1:8188",
     "workflow": "~/my-graph.json", "width": 1024, "height": 1024}

A workflow's strings "{{prompt}}", "{{seed}}", "{{width}}" and "{{height}}"
are filled in (the last three as numbers). The prompt id is the session: a
run eki restarted mid-drawing polls it again instead of drawing twice.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from .base import Emit, Outcome, Provider, Turn

DEFAULT_WORKFLOW = Path(__file__).with_name("comfyui_default.json")
POLL = 1.0


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

    def _queued(self, pid: str) -> bool:
        """Still waiting or drawing (ComfyUI may have lost it in a restart of its own)."""
        try:
            q = self._get("/queue")
        except (OSError, ValueError):
            return True
        return any(len(item) > 1 and item[1] == pid
                   for key in ("queue_running", "queue_pending") for item in q.get(key) or [])

    # ---- the workflow --------------------------------------------------------------------

    def graph(self, prompt: str) -> Dict[str, Any]:
        path = Path(self.cfg["workflow"]).expanduser() if self.cfg.get("workflow") else DEFAULT_WORKFLOW
        values = {"{{prompt}}": prompt, "{{seed}}": random.randint(0, 2 ** 32 - 1),
                  "{{width}}": int(self.cfg.get("width", 1024)),
                  "{{height}}": int(self.cfg.get("height", 1024))}
        return _fill(json.loads(path.read_text()), values)

    # ---- a turn --------------------------------------------------------------------------

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        from ..worker import CARRY_ON         # the worker imports the providers
        pid: Optional[str] = None
        detail = turn.prompt
        try:
            if turn.resume and (turn.prompt == CARRY_ON or not turn.prompt.strip()):
                # eki restarted mid-drawing: the same picture, not a second one
                if self._queued(turn.resume) or self._entry(turn.resume):
                    pid, detail = turn.resume, "picture"
                else:
                    emit("note", {"text": "ComfyUI no longer knows that drawing; nothing to carry on"})
                    return Outcome("failed", "ComfyUI lost the drawing (was it restarted?)")
            if pid is None:
                pid = self._post_prompt(self.graph(turn.prompt))
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
        for path in made:
            emit("tool", {"name": "image", "detail": detail, "path": str(path)})
        names = ", ".join(p.name for p in made)
        emit("text", {"text": f"Drew {len(made)} picture{'s' if len(made) > 1 else ''}: {names}\n"
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


def _fill(value: Any, values: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value in values:
            return values[value]
        for key, v in values.items():
            value = value.replace(key, str(v))
        return value
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    return value


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
