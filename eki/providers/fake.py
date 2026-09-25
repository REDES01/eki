"""A stand-in program for tests and the restart drill.

It behaves like a real agent CLI — a session that can be resumed, output
over time, limits and failures on request — without spending anything.
Words in the prompt steer it: `steps=5`, `delay=0.5`, `limit`, `fail`,
`handoff`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from .base import Emit, Outcome, Turn, with_history
from .program import Channel, ProgramProvider


class Fake(ProgramProvider):
    kind = "fake"
    interactive = True

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.harness = bool(cfg.get("harness", True))

    def argv(self, turn: Turn) -> List[str]:
        argv = [sys.executable, "-m", "eki.providers.fake", "--name", self.name]
        if turn.resume:
            argv += ["--resume", turn.resume]
        return argv + [with_history(turn)]

    def env(self, turn: Turn) -> Dict[str, str]:
        env = super().env(turn)
        root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        kind = event.get("type")
        if kind == "ask":
            ch.ask("question", {"questions": [{"question": event["question"], "header": "Pick",
                                               "multiSelect": False,
                                               "options": [{"label": o} for o in event["options"]]}]},
                   event["id"])
            return
        if kind == "done":
            out.finished = True
            return
        if kind == "session":
            emit("session", {"id": event["id"]})
        elif kind == "text":
            emit("text", {"text": event["text"]})
        elif kind == "tool":
            emit("tool", {k: v for k, v in event.items() if k != "type"})
        elif kind == "limit":
            out.error, out.reset_at = "usage limit reached", time.time() + float(event.get("in", 60))
        elif kind == "handoff":
            out.state, out.reason = "handed_off", event.get("reason", "")
        elif kind == "error":
            out.error = event.get("message", "error")

    def answer(self, ch: Channel, key: Any, response: Dict[str, Any]) -> None:
        answers = response.get("answers") or {}
        ch.write({"id": key, "answer": next(iter(answers.values()), "") if response.get("allow", True) else None})


# ---- the program itself -------------------------------------------------------------------

def _say(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main(argv: List[str]) -> int:
    name, resume = "fake", None
    while argv and argv[0].startswith("--"):
        flag, value, argv = argv[0], argv[1], argv[2:]
        if flag == "--name":
            name = value
        elif flag == "--resume":
            resume = value
    prompt = argv[-1] if argv else ""
    state_dir = Path(os.environ.get("EKI_HOME", ".")) / "fake-sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    if resume and (state_dir / resume).exists():
        sid = resume
        state = json.loads((state_dir / sid).read_text())
        if state["done"] >= state["steps"]:
            state = _fresh(prompt)      # a new turn in the same session
    else:
        sid = uuid.uuid4().hex
        state = _fresh(prompt)
    (state_dir / sid).write_text(json.dumps(state))
    _say({"type": "session", "id": sid})
    ask = state["prompt"]
    if "fail" in ask.split():
        _say({"type": "error", "message": "fake failure"})
        return 1
    if "limit" in ask.split() and name in os.environ.get("EKI_FAKE_LIMITED", name).split(","):
        _say({"type": "limit", "in": 60})
        return 1
    if "handoff" in ask.split():
        _say({"type": "handoff", "reason": "needs tools"})
        return 0
    if "ask" in ask.split():
        _say({"type": "ask", "id": "q1", "question": "Which colour?", "options": ["red", "blue"]})
        reply = json.loads(sys.stdin.readline() or "{}")
        _say({"type": "text", "text": f"picked {reply.get('answer')}\n"})
    if "write" in ask.split():
        path = Path(os.environ.get("EKI_HOME", ".")) / "made.html"
        path.write_text("<h1>made by fake</h1>")
        _say({"type": "tool", "name": "Write", "detail": str(path), "path": str(path)})
    delay = float(_num(ask, "delay", 0.05))
    while state["done"] < state["steps"]:
        time.sleep(delay)
        state["done"] += 1
        (state_dir / sid).write_text(json.dumps(state))
        _say({"type": "text", "text": f"step {state['done']}/{state['steps']}\n"})
    _say({"type": "text", "text": f"{name} done: {ask.splitlines()[-1][:80]}"})
    _say({"type": "done"})
    return 0


def _fresh(prompt: str) -> Dict[str, Any]:
    return {"prompt": prompt, "steps": int(_num(prompt, "steps", 3)), "done": 0}


def _num(text: str, key: str, default: float) -> float:
    m = re.search(rf"\b{key}=([\d.]+)", text)
    return float(m.group(1)) if m else default


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
