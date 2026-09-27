"""Codex's harness on the local model (rung 2): Codex does the tool work,
the local model does the thinking, through eki's Responses adapter.

    "codex-local": {"kind": "codex_local", "local": "local",
                    "context_tokens": 32768, "handoff_after": 20}

The program is the same `codex app-server` as the Codex provider, told by
`-c` flags to use a model provider of eki's own: the adapter at
`http://127.0.0.1:<port>/v1/responses` (eki/responses.py), which turns
Codex's requests into chat completions on the `local` entry's server.

It is the first try, not the last word. The turn hands off to Claude
(`worker.finish` makes the follow-up on row `code`, without this provider)
when the model says `HANDOFF: <why>`, when the turn fails, or when it runs
past `handoff_after` minutes (the turn is interrupted first). The files are
left as they are, so the next program sees what was done.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional

from .base import Emit, Outcome, Turn
from .codex import Codex
from .program import Channel

#: what the local model is told, besides the request
NOTE = ("You are a small local model. If this is beyond you, stop and end your "
        "answer with a line `HANDOFF: <why>`; a stronger model will take over "
        "with your work so far.")
#: how often Codex retries a dropped stream: an engine restart mid-stream is survived
STREAM_RETRIES = 20
#: after asking the turn to stop, how long before it's cut off
INTERRUPT_GRACE = 30.0
LEFT = "the files are left as they are"
HANDOFF_LINE = re.compile(r"^\s*HANDOFF:\s*(.*)$", re.M)


def handoff_in(text: str) -> Optional[str]:
    """The reason on the message's last `HANDOFF:` line, or None."""
    found = HANDOFF_LINE.findall(text or "")
    return (found[-1].strip() or "no reason given") if found else None


class CodexLocal(Codex):
    kind = "codex_local"

    def _local(self) -> str:
        return str(self.cfg.get("local") or "local")

    def available(self) -> tuple:
        """Codex is here and the local entry exists; an on-demand model is started by the turn."""
        from . import config
        ok, why = super().available()
        cfg = config().get(self._local())
        if ok and (cfg is None or cfg.get("off") or cfg.get("kind") != "local"):
            return False, f"no local model named {self._local()!r}"
        return ok, why

    def local_model(self) -> str:
        """The local entry's model id: its `model`, else what its server has loaded."""
        from . import get
        return get(self._local()).model()     # type: ignore[attr-defined]

    def provider_flags(self) -> List[str]:
        from .. import server
        key = "model_providers.eki_local"
        return ["-c", 'model_provider="eki_local"',
                "-c", f'{key}.name="eki local"',
                "-c", f'{key}.base_url="http://127.0.0.1:{server.port()}/v1"',
                "-c", f'{key}.wire_api="responses"',
                "-c", f'{key}.http_headers={{"X-Eki"="1"}}',
                "-c", f"{key}.stream_max_retries={STREAM_RETRIES}",
                "-c", f"model_context_window={int(self.cfg.get('context_tokens', 32768))}"]

    def argv(self, turn: Turn) -> List[str]:
        argv = super().argv(turn)
        model = turn.extra.get("model")
        extra = self.provider_flags() + (["-m", str(model)] if model else [])
        return argv[:-1] + extra + argv[-1:]            # before `app-server`

    # ---- the turn --------------------------------------------------------------------------

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        from .. import models
        local = self._local()
        if not models.ensure(local):
            return self._handoff(f"local model {local} is not up")
        try:
            turn.extra["model"] = self.local_model()
        except (OSError, ValueError, KeyError) as e:
            return self._handoff(f"local model {local}: {type(e).__name__}")
        turn.extra["note"] = NOTE + ("\n\n" + turn.extra["note"] if turn.extra.get("note") else "")
        # our own stop: the run's (you cancelled it) or ours (overtime, the interrupt unheard)
        outer, inner, overtime = turn.stop, threading.Event(), threading.Event()
        done = threading.Event()
        limit = float(self.cfg.get("handoff_after", 20)) * 60

        def watch() -> None:
            if not done.wait(limit) and not outer.is_set():
                overtime.set()
                ch = turn.extra.get("_channel")
                if ch is not None:
                    self._interrupt(ch)
                if done.wait(INTERRUPT_GRACE):
                    return
                inner.set()

        def relay() -> None:
            while not done.is_set():
                if outer.wait(0.3):
                    inner.set()
                    return

        turn.stop = inner
        threading.Thread(target=watch, daemon=True).start()
        threading.Thread(target=relay, daemon=True).start()
        try:
            out = super().take(turn, emit)
        finally:
            done.set()
            turn.stop = outer
        ch = turn.extra.pop("_channel", None)
        said = (ch.state.get("last_message") if ch is not None else "") or ""
        if outer.is_set():
            return out
        if overtime.is_set():
            return self._handoff(f"ran past {self.cfg.get('handoff_after', 20)} min")
        if out.state != "done":
            return self._handoff(f"the turn failed: {out.error or out.state}")
        why = handoff_in(said)
        if why is not None:
            return self._handoff(why)
        return out

    def _handoff(self, why: str) -> Outcome:
        return Outcome(state="handed_off", reason=f"{why} ({LEFT})", finished=True)

    def _interrupt(self, ch: Channel) -> None:
        tid, uid = ch.state.get("thread"), ch.state.get("turn_id")
        if tid and uid:
            self._call(ch, "turn/interrupt", {"threadId": tid, "turnId": uid}, "interrupt")

    # ---- the conversation: remember the turn and what was said last ---------------------

    def opening(self, ch: Channel, turn: Turn) -> None:
        lock, write = threading.Lock(), ch.write

        def locked(obj: Dict[str, Any]) -> None:          # the watcher writes from its own thread
            with lock:
                write(obj)

        ch.write = locked                                 # type: ignore[method-assign]
        turn.extra["_channel"] = ch
        super().opening(ch, turn)

    def _replied(self, event: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        purpose = ch.state.get("calls", {}).get(event.get("id"))
        if purpose == "turn":
            turn = (event.get("result") or {}).get("turn") or {}
            ch.state["turn_id"] = turn.get("id")
        if purpose == "interrupt":
            ch.state.get("calls", {}).pop(event.get("id"), None)
            return                                        # its error, if any, isn't the turn's
        super()._replied(event, emit, out, ch)

    def _notified(self, method: str, params: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        item = params.get("item") or {}
        if method == "item/started" and item.get("type") == "agentMessage":
            ch.state["message"] = []
        elif method == "item/agentMessage/delta":
            ch.state.setdefault("message", []).append(params.get("delta") or "")
        elif method == "item/completed" and item.get("type") == "agentMessage":
            ch.state["last_message"] = item.get("text") or "".join(ch.state.get("message") or [])
        super()._notified(method, params, emit, out, ch)


def default_entry(local: str) -> Dict[str, Any]:
    """The entry config() joins in memory when Codex and a local model are both here."""
    return {"kind": "codex_local", "label": "Codex on the local model", "local": local,
            "context_tokens": 32768, "handoff_after": 20}


__all__ = ["CodexLocal", "default_entry", "handoff_in"]
