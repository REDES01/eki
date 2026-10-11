"""`eki mcp-hold` — a stdio MCP proxy that holds its card before the first tool call.

An agent's MCP server for a card runs behind this proxy. Lines of
JSON-RPC pass both ways unchanged (stdin→server, server→stdout; stderr is
the proxy's own). The first `tools/call` is held back while the proxy takes
the card the way `eki use` does: a component call run (prompt `hold: true`,
priority now, parent the agent's run) through the engine, so the card's
access rule and conflicts cover the MCP use exactly like a script call.
Done: the request goes on and the proxy never asks again. Anything else
(or EKI_MCP_HOLD_TIMEOUT seconds): the caller gets a JSON-RPC error with
the reason, and the next `tools/call` tries again. Other messages keep
flowing while it waits. With no run there is nothing to hold for: it relays.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Callable, List, Optional, Tuple

from .. import db, engine, store

TERMINAL = ("done", "failed", "cancelled", "handed_off")
TIMEOUT = 900.0
POLL = 0.3
ERROR = -32000


def timeout_default() -> float:
    try:
        return float(os.environ.get("EKI_MCP_HOLD_TIMEOUT") or TIMEOUT)
    except ValueError:
        return TIMEOUT


def ensure_engine() -> None:
    if engine.running_pid() is None:
        engine.start_detached()


def create(conn, card: str, caller_dir: str, caller_run: str) -> str:
    """The hold call, written down before anything starts it (as eki/cli/use.py create())."""
    prompt = json.dumps({"component": card, "args": [], "caller_dir": caller_dir,
                         "caller_run": caller_run, "hold": True})
    with db.tx(conn):
        tid = store.create_thread(conn, f"use {card}", None, by="eki")
        return store.create_run(conn, tid, prompt, provider="component", priority="now", parent=caller_run)


def acquire(conn, card: str, caller_dir: str, caller_run: str, timeout: float,
            poll: float = POLL, stop: Optional[Callable[[], bool]] = None) -> Tuple[bool, str]:
    """Take the card for caller_run: (True, run id) once the hold call is done, else (False, why)."""
    rid = create(conn, card, caller_dir, caller_run)
    ensure_engine()
    end = time.monotonic() + timeout
    while True:
        r = store.run(conn, rid)
        if r["state"] == "done":
            return True, rid
        if r["state"] in TERMINAL:
            return False, f"{card}: {r['state']}: {r['error'] or r['why'] or 'no reason given'}"
        if time.monotonic() >= end or (stop and stop()):
            store.cancel(conn, rid)
            why = f": {r['why']}" if r["why"] else ""
            return False, f"{card}: not held after {timeout:g}s{why}"
        time.sleep(poll)


def _tool_call(line: bytes) -> Optional[dict]:
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    if isinstance(msg, dict) and msg.get("method") == "tools/call":
        return msg
    return None


class Proxy:
    """One relay: the server child, the two directions, and the hold."""

    def __init__(self, argv: List[str], card: str, run_id: str, folder: str,
                 stdin: BinaryIO, stdout: BinaryIO, timeout: float):
        self.card, self.run_id, self.folder, self.timeout = card, run_id, folder, timeout
        self.stdin, self.stdout = stdin, stdout
        self.held = not run_id
        self.closed = False
        self.out_lock = threading.Lock()
        self.in_lock = threading.Lock()
        self.pending: "queue.Queue[Optional[Tuple[bytes, dict]]]" = queue.Queue()
        self.child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      env=os.environ.copy())

    def to_child(self, line: bytes) -> None:
        with self.in_lock:
            if self.closed:
                return
            try:
                self.child.stdin.write(line)
                self.child.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass

    def to_caller(self, line: bytes) -> None:
        with self.out_lock:
            try:
                self.stdout.write(line)
                self.stdout.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass

    def refuse(self, msg: dict, why: str) -> None:
        if "id" not in msg:
            return
        err = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": ERROR, "message": why}}
        self.to_caller((json.dumps(err) + "\n").encode())

    def caller_side(self) -> None:
        """stdin → server; a tools/call waits for the card on the hold thread."""
        for line in iter(self.stdin.readline, b""):
            msg = None if self.held and self.pending.empty() else _tool_call(line)
            if msg is not None:
                self.pending.put((line, msg))
            else:
                self.to_child(line)
        self.pending.put(None)

    def hold_side(self) -> None:
        conn = db.connect() if self.run_id else None
        while True:
            got = self.pending.get()
            if got is None:
                break
            line, msg = got
            if not self.held:
                ok, why = acquire(conn, self.card, self.folder, self.run_id, self.timeout,
                                  stop=lambda: self.child.poll() is not None)
                if not ok:
                    self.refuse(msg, f"eki: can't hold {why}")
                    continue
                self.held = True
            self.to_child(line)
        with self.in_lock:          # the caller is gone: so is the server's input
            self.closed = True
            try:
                self.child.stdin.close()
            except OSError:
                pass

    def server_side(self) -> None:
        for line in iter(self.child.stdout.readline, b""):
            self.to_caller(line)

    def run(self) -> int:
        for side in (self.caller_side, self.hold_side):
            threading.Thread(target=side, daemon=True).start()
        self.server_side()          # ends when the server closes its output
        try:
            return self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            return self.child.wait()


def main(argv: List[str], card: str = "", run_id: str = "", folder: str = "",
         stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None,
         timeout: Optional[float] = None) -> int:
    """Relay to the server argv, holding `card` for `run_id`; the server's exit code."""
    if not argv:
        print("eki mcp-hold: no server command (after --)", file=sys.stderr)
        return 2
    try:
        p = Proxy(argv, card, run_id or "", os.path.abspath(folder or os.getcwd()),
                  stdin or sys.stdin.buffer, stdout or sys.stdout.buffer,
                  timeout_default() if timeout is None else timeout)
    except OSError as e:
        print(f"eki mcp-hold: can't start {argv[0]}: {e}", file=sys.stderr)
        return 127
    return p.run()
