"""How much of each subscription is left — read from the programs themselves.

Two ways in, neither touching a credential:

- every run reports what it sees (Claude's `rate_limit_event`, Codex's
  `account/rateLimits/updated`), recorded as it arrives;
- the engine asks each program now and then without spending anything:
  Claude Code's `get_usage` control request, Codex's `account/rateLimits/read`
  (`probe_claude`, `probe_codex`).

A window is {"used": 0..1, "resets_at": epoch}.
"""
from __future__ import annotations

import json
import os
import select
import sqlite3
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import providers
from .db import dumps, now

#: how often the engine asks, when no run has said anything more recent
PROBE_EVERY = float(os.environ.get("EKI_QUOTA_EVERY") or 10 * 60)
LABELS = {"five_hour": "5h", "seven_day": "week", "thirty_day": "30 days"}


def record(conn: sqlite3.Connection, provider: str, windows: Dict[str, Dict[str, Any]],
           plan: Optional[str] = None) -> None:
    if not windows:
        return
    old = reading(conn, provider)
    conn.execute("INSERT INTO quota(provider, windows, plan, observed_at) VALUES (?,?,?,?) "
                 "ON CONFLICT(provider) DO UPDATE SET windows=excluded.windows, "
                 "plan=COALESCE(excluded.plan, quota.plan), observed_at=excluded.observed_at",
                 (provider, dumps({**(old or {}).get("windows", {}), **windows}), plan, now()))


def reading(conn: sqlite3.Connection, provider: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM quota WHERE provider=?", (provider,)).fetchone()
    if row is None:
        return None
    windows = json.loads(row["windows"])
    t = time.time()
    for w in windows.values():                      # a window that has reset since is empty again
        if w.get("resets_at") and w["resets_at"] < t:
            w["used"], w["reset_since"] = 0.0, True
    return {"provider": provider, "plan": row["plan"], "observed_at": row["observed_at"],
            "windows": windows}


def fullest(conn: sqlite3.Connection, provider: str) -> Optional[Dict[str, Any]]:
    """The window closest to its limit, with its name."""
    r = reading(conn, provider)
    if not r or not r["windows"]:
        return None
    name, w = max(r["windows"].items(), key=lambda kv: kv[1].get("used", 0))
    return {"window": name, "label": LABELS.get(name, name), **w}


def due(conn: sqlite3.Connection, provider: str) -> bool:
    r = reading(conn, provider)
    return r is None or time.time() - r["observed_at"] > PROBE_EVERY


# ---- asking the programs ---------------------------------------------------------------

def _lines(proc: subprocess.Popen, timeout: float):
    end = time.time() + timeout
    while time.time() < end:
        ready, _, _ = select.select([proc.stdout], [], [], 0.5)
        if ready:
            line = proc.stdout.readline()
            if not line:
                return
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _talk(argv: List[str], first: List[Dict[str, Any]]) -> subprocess.Popen:
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1, cwd=os.path.expanduser("~"))
    for msg in first:
        proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return proc


def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / (1000 if value > 1e12 else 1)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def probe_claude(binary: str) -> Dict[str, Any]:
    proc = _talk([binary, "--output-format", "stream-json", "--verbose", "--input-format", "stream-json"], [
        {"type": "control_request", "request_id": "q-init", "request": {"subtype": "initialize"}},
        {"type": "control_request", "request_id": "q-usage",
         "request": {"subtype": "get_usage", "skip_behaviors": True}}])
    try:
        for ev in _lines(proc, 40):
            resp = ev.get("response") or {}
            if ev.get("type") == "control_response" and resp.get("request_id") == "q-usage":
                body = resp.get("response") or {}
                windows = {k: {"used": float(v.get("utilization") or 0) / 100, "resets_at": _epoch(v.get("resets_at"))}
                           for k, v in (body.get("rate_limits") or {}).items()
                           if k in LABELS and isinstance(v, dict) and v.get("utilization") is not None}
                return {"windows": windows, "plan": body.get("subscription_type")}
    finally:
        proc.kill()
    return {}


def probe_codex(binary: str) -> Dict[str, Any]:
    from .providers.codex import quota_windows
    proc = _talk([binary, "app-server"], [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {"experimentalApi": True},
        "clientInfo": {"name": "eki", "title": "eki", "version": "0.3.0"}}}])
    try:
        for ev in _lines(proc, 40):
            if ev.get("id") == 1:
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}}) + "\n")
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read",
                                             "params": {}}) + "\n")
                proc.stdin.flush()
            elif ev.get("id") == 2:
                limits = (ev.get("result") or {}).get("rateLimits") or {}
                return {"windows": quota_windows(limits), "plan": limits.get("planType")}
    finally:
        proc.kill()
    return {}


def refresh(conn: sqlite3.Connection, force: bool = False) -> List[str]:
    """Ask every subscription program whose reading is old. Returns what was read."""
    did = []
    for name, cfg in providers.config().items():
        kind = cfg.get("kind")
        if kind not in ("claude_code", "codex") or cfg.get("off") or not (force or due(conn, name)):
            continue
        prov = providers.build(name, cfg)
        if not getattr(prov, "bin", None):
            continue
        try:
            got = (probe_claude if kind == "claude_code" else probe_codex)(prov.bin)
        except (OSError, ValueError):
            got = {}
        if got.get("windows"):
            record(conn, name, got["windows"], got.get("plan"))
            did.append(name)
    return did
