"""Model lists: what each program itself says it offers, kept in SQLite.

Each program is asked in its own words, through the official program and
never with a token or a vendor API of eki's own:

    Claude Code  `claude --input-format stream-json --output-format stream-json
                 --verbose`, the `initialize` control_request (as INIT in
                 eki/providers/claude_code.py); the models are in the
                 control_response's `response.models`
    Codex        `codex app-server`: initialize, initialized, `model/list`
    local        GET /v1/models (Local.models())

A read replaces that provider's rows in provider_models in one transaction.
suggest() maps a list onto the tiers (eki/targets.py); a suggestion is only
shown — nothing here writes providers.json. The person picks with
`eki providers tier` or the Settings page.
"""
from __future__ import annotations

import json
import queue
import sqlite3
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import db, providers
from .providers.base import find_binary

#: the kinds whose program can say what models it offers
KINDS = ("claude_code", "codex", "local")
#: how long a read may take before the program is killed
TIMEOUT = 20.0
#: a list older than this is read again by housekeeping
STALE = 24 * 3600.0
#: names that sound like a provider's smaller, quicker model
SMALL = ("mini", "nano", "small", "lite")
INIT = "eki-init"                    # the handshake id eki/providers/claude_code.py uses


# ---- talking to a program -------------------------------------------------------------

def _converse(argv: List[str], first: List[Dict[str, Any]],
              on_line: Callable[[Dict[str, Any], Callable[[Dict[str, Any]], None]], Optional[list]],
              timeout: float) -> list:
    """Write `first`, then feed each JSON line read to `on_line` until it
    returns a list; the process is killed whatever happens."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:     # type: ignore[union-attr]
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def write(obj: Dict[str, Any]) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")    # type: ignore[union-attr]
        proc.stdin.flush()                          # type: ignore[union-attr]

    deadline = time.monotonic() + timeout
    try:
        for obj in first:
            write(obj)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{argv[0]} gave no model list in {timeout:.0f} s")
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(f"{argv[0]} ended without a model list")
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                got = on_line(event, write)
                if got is not None:
                    return got
    finally:
        try:
            proc.stdin.close()                      # type: ignore[union-attr]
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _claude(cfg: Dict[str, Any], timeout: float) -> List[Dict[str, Any]]:
    binary = find_binary(str(cfg.get("binary") or "claude"))
    if not binary:
        raise RuntimeError("claude is not installed")

    def on_line(event: Dict[str, Any], write) -> Optional[list]:
        resp = event.get("response") or {}
        if event.get("type") != "control_response" or resp.get("request_id") != INIT:
            return None
        if resp.get("subtype") == "error":
            raise RuntimeError(f"claude: {resp.get('error') or 'initialize failed'}")
        models = (resp.get("response") or {}).get("models") or resp.get("models") or []
        models = [m for m in models if isinstance(m, dict) and m.get("value")]
        mark = _claude_default(models)
        return [{"model": str(m["value"]), "label": str(m.get("displayName") or m["value"]),
                 "is_default": m is mark} for m in models]

    argv = [binary, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    return _converse(argv, [{"type": "control_request", "request_id": INIT,
                             "request": {"subtype": "initialize"}}], on_line, timeout)


def _claude_default(models: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The entry the program itself calls its default: a flag when it gives
    one, else its `default` alias ("Default (recommended)"), else an entry
    whose name or description says default or recommended."""
    for test in (lambda m: m.get("isDefault") is True or m.get("default") is True,
                 lambda m: m["value"] == "default",
                 lambda m: any(w in f"{m.get('displayName', '')} {m.get('description', '')}".lower()
                               for w in ("default", "recommended"))):
        hit = next((m for m in models if test(m)), None)
        if hit is not None:
            return hit
    return None


def _codex(cfg: Dict[str, Any], timeout: float) -> List[Dict[str, Any]]:
    binary = find_binary(str(cfg.get("binary") or "codex"))
    if not binary:
        raise RuntimeError("codex is not installed")
    out: List[Dict[str, Any]] = []
    asked = {"n": 1}

    def ask_list(write, cursor=None) -> None:
        asked["n"] += 1
        write({"jsonrpc": "2.0", "id": asked["n"], "method": "model/list",
               "params": {"cursor": cursor} if cursor else {}})

    def on_line(event: Dict[str, Any], write) -> Optional[list]:
        if "method" in event or "id" not in event:
            return None                              # a notification or a request of its own
        if "error" in event:
            raise RuntimeError(f"codex: {(event['error'] or {}).get('message') or event['error']}")
        result = event.get("result") or {}
        if event["id"] == 1:
            write({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            ask_list(write)
            return None
        for m in result.get("data") or []:
            mid = isinstance(m, dict) and (m.get("id") or m.get("model"))
            if mid and not m.get("hidden"):
                out.append({"model": str(mid), "label": str(m.get("displayName") or mid),
                            "is_default": bool(m.get("isDefault"))})
        if result.get("nextCursor") and asked["n"] < 10:
            ask_list(write, result["nextCursor"])
            return None
        return out

    argv = [binary, "-c", "suppress_unstable_features_warning=true", "app-server"]
    return _converse(argv, [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {"experimentalApi": True},
        "clientInfo": {"name": "eki", "title": "eki", "version": "0.3.0"}}}], on_line, timeout)


def _local(name: str, cfg: Dict[str, Any], timeout: float) -> List[Dict[str, Any]]:
    ids = providers.build(name, cfg).models(timeout=min(timeout, 5.0))
    return [{"model": i, "label": i, "is_default": i == cfg.get("model")} for i in ids]


def read(name: str, cfg: Dict[str, Any], timeout: float = TIMEOUT) -> List[Dict[str, Any]]:
    """Ask the program behind provider `name` for its models (nothing is stored)."""
    kind = cfg.get("kind")
    if kind == "claude_code":
        return _claude(cfg, timeout)
    if kind == "codex":
        return _codex(cfg, timeout)
    if kind == "local":
        return _local(name, cfg, timeout)
    raise ValueError(f"{name} ({kind}) has no model list to read")


# ---- the cache ------------------------------------------------------------------------

def refresh(conn: sqlite3.Connection, name: str, timeout: float = TIMEOUT) -> List[Dict[str, Any]]:
    """Read `name`'s list and replace its rows in one transaction."""
    cfg = providers.config().get(name)
    if cfg is None:
        raise KeyError(f"no provider named {name!r}")
    rows, seen = [], set()
    for r in read(name, cfg, timeout):
        if r["model"] not in seen:
            seen.add(r["model"])
            rows.append({"model": r["model"], "label": r["label"], "is_default": bool(r["is_default"])})
    now = db.now()
    with db.tx(conn):
        conn.execute("DELETE FROM provider_models WHERE provider=?", (name,))
        conn.executemany(
            "INSERT INTO provider_models(provider, model, label, is_default, read_at) VALUES (?,?,?,?,?)",
            [(name, r["model"], r["label"], int(r["is_default"]), now) for r in rows])
    return rows


def listed(conn: sqlite3.Connection, name: str) -> Optional[List[Dict[str, Any]]]:
    """`name`'s read list in the program's order, or None when it was never read.
    A model is `floating` when it is an alias for a pinned one, or "default"."""
    got = conn.execute("SELECT model, label, description, resolved, efforts, default_effort, is_default"
                       " FROM provider_models WHERE provider=? ORDER BY rowid", (name,)).fetchall()
    if not got:
        return None
    out = []
    for r in got:
        try:
            efforts = json.loads(r["efforts"]) if r["efforts"] else None
        except ValueError:
            efforts = None
        out.append({"model": r["model"], "label": r["label"] or r["model"],
                    "description": r["description"], "resolved": r["resolved"],
                    "efforts": efforts if isinstance(efforts, list) else None,
                    "default_effort": r["default_effort"], "is_default": bool(r["is_default"]),
                    "floating": bool(r["resolved"] and r["resolved"] != r["model"]) or r["model"] == "default"})
    return out


def read_at(conn: sqlite3.Connection, name: str) -> Optional[float]:
    got = conn.execute("SELECT MAX(read_at) AS t FROM provider_models WHERE provider=?", (name,)).fetchone()
    return got["t"] if got and got["t"] is not None else None


def stale(conn: sqlite3.Connection, now: Optional[float] = None) -> List[str]:
    """Providers whose list is unread or older than STALE and whose program is
    installed (a local server is tried as it is: it has no binary)."""
    now = db.now() if now is None else now
    out = []
    for name, cfg in providers.config().items():
        kind = cfg.get("kind")
        if kind not in KINDS or cfg.get("off"):
            continue
        if kind != "local" and not find_binary(str(cfg.get("binary") or ("claude" if kind == "claude_code"
                                                                          else "codex"))):
            continue
        t = read_at(conn, name)
        if t is None or now - t > STALE:
            out.append(name)
    return out


# ---- tiers ----------------------------------------------------------------------------

def _says(m: Dict[str, Any], word: str) -> bool:
    return word in f"{m.get('model', '')} {m.get('label', '')}".lower()


def suggest(name: str, kind: str, models: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """Tier → model id, guessed from the names in a read list (only ever shown)."""
    models = [m for m in models or [] if m.get("model")]
    out: Dict[str, str] = {}
    if kind == "claude_code":
        for tier, word in (("fast", "haiku"), ("default", "sonnet"), ("top", "opus")):
            hit = next((m for m in models if _says(m, word)), None)
            if hit:
                out[tier] = hit["model"]
    elif kind == "codex":
        small = [m for m in models if any(_says(m, w) for w in SMALL)]
        dflt = next((m for m in models if m.get("is_default")), None)
        if dflt:
            out["default"] = dflt["model"]
        if small:
            out["fast"] = small[0]["model"]
        top = next((m for m in models if not m.get("is_default") and m not in small), None)
        if top:
            out["top"] = top["model"]
    return out
