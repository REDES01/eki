"""Does an mcp card's server start? A quick MCP handshake and its tool list, on demand.

`probe(server)` runs `initialize`, `notifications/initialized` and `tools/list` (following
`nextCursor`) against a stdio server (command + args, env added to the environment, in its own
process group, always killed at the end) or a Streamable HTTP one (`{"url"}`: JSON or
text/event-stream replies, the `Mcp-Session-Id` header carried). Nothing is stored and it never
raises: a failure is a short error saying what failed, with any env value masked out of it.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .. import mcp

PROTOCOL = "2025-06-18"
CLIENT = {"name": "eki", "version": "1"}
MAX_PAGES = 50


class Failed(Exception):
    """A step of the handshake failed; the message is what the card shows."""


def _short(text: str, n: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else "…" + text[-n:]


def _hide(text: str, env: Dict[str, str]) -> str:
    for v in sorted((str(v) for v in env.values() if str(v)), key=len, reverse=True):
        text = text.replace(v, mcp.mask(v))
    return text


def _result(reply: Any, what: str) -> dict:
    if not isinstance(reply, dict):
        raise Failed(f"{what}: no JSON-RPC reply")
    if "error" in reply:
        err = reply["error"]
        msg = err.get("message", err) if isinstance(err, dict) else err
        raise Failed(f"{what} refused: {_short(str(msg), 200)}")
    res = reply.get("result")
    if not isinstance(res, dict):
        raise Failed(f"{what}: reply has no result")
    return res


def _handshake(ask: Callable[[str, dict], Any], tell: Callable[[str], None], out: dict) -> None:
    """The MCP conversation, over any transport: fills out's name, version and tools."""
    init = _result(ask("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                      "clientInfo": CLIENT}), "initialize")
    info = init.get("serverInfo") if isinstance(init.get("serverInfo"), dict) else {}
    out["name"], out["version"] = str(info.get("name", "")), str(info.get("version", ""))
    tell("notifications/initialized")
    tools: List[Dict[str, str]] = []
    cursor: Optional[str] = None
    for _ in range(MAX_PAGES):
        res = _result(ask("tools/list", {"cursor": cursor} if cursor else {}), "tools/list")
        for t in res.get("tools") or []:
            if isinstance(t, dict) and t.get("name"):
                tools.append({"name": str(t["name"]), "description": str(t.get("description") or "")})
        cursor = res.get("nextCursor")
        if not cursor:
            break
    out["tools"] = tools


# ---- stdio --------------------------------------------------------------------------------------

class _Stdio:
    def __init__(self, server: dict, deadline: float):
        self.deadline, self.next_id = deadline, 0
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (server.get("env") or {}).items()})
        argv = [str(server["command"])] + [str(a) for a in server.get("args") or []]
        self.err = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
                                         env=env, start_new_session=True)
        except FileNotFoundError:
            self.err.close()
            raise Failed(f"no such command: {argv[0]}")
        except OSError as e:
            self.err.close()
            raise Failed(f"can't start {argv[0]}: {e.strerror or e}")
        self.lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                self.lines.put(line)
        except (OSError, ValueError):
            pass
        self.lines.put(None)

    def _stderr(self) -> str:
        try:
            self.err.seek(0)
            return _short(self.err.read()[-2000:].decode("utf-8", "replace"), 200)
        except (OSError, ValueError):
            return ""

    def _exited(self) -> Failed:
        try:
            code = self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            code = None
        tail = self._stderr()
        said = f"exited ({code})" if code is not None else "closed its output"
        return Failed(f"{said}{': ' + tail if tail else ' with nothing on stderr'}")

    def _send(self, msg: dict) -> None:
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError):
            raise self._exited()

    def tell(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def ask(self, method: str, params: dict) -> Any:
        self.next_id += 1
        want = self.next_id
        self._send({"jsonrpc": "2.0", "id": want, "method": method, "params": params})
        while True:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise Failed(f"timed out waiting for {method}")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                raise Failed(f"timed out waiting for {method}")
            if line is None:
                raise self._exited()
            try:
                msg = json.loads(line)
            except ValueError:
                continue                                  # a log line on stdout: not ours
            if isinstance(msg, dict) and msg.get("id") == want and ("result" in msg or "error" in msg):
                return msg
            if isinstance(msg, dict) and "method" in msg and "id" in msg:   # the server asks us: say no
                self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})

    def close(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for f in (self.proc.stdin, self.proc.stdout, self.err):
            try:
                f.close()  # type: ignore[union-attr]
            except (OSError, ValueError, AttributeError):
                pass


# ---- Streamable HTTP ----------------------------------------------------------------------------

class _Http:
    def __init__(self, url: str, deadline: float):
        self.url, self.deadline, self.next_id, self.session = url, deadline, 0, ""

    def _post(self, msg: dict, what: str) -> Optional[Any]:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Failed(f"timed out waiting for {what}")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=left) as r:
                self.session = r.headers.get("Mcp-Session-Id") or self.session
                if "id" not in msg:
                    return None
                kind = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if kind == "text/event-stream":
                    return self._events(r, msg["id"], what)
                body = r.read()
        except urllib.error.HTTPError as e:
            raise Failed(f"{what}: HTTP {e.code} {e.reason}")
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, (TimeoutError, OSError)) and "timed out" in str(reason):
                raise Failed(f"timed out waiting for {what}")
            raise Failed(f"can't reach {self.url}: {reason}")
        except (TimeoutError, OSError) as e:
            raise Failed(f"timed out waiting for {what}" if "timed out" in str(e) else f"{what}: {e}")
        try:
            got = json.loads(body)
        except ValueError:
            raise Failed(f"{what}: the reply isn't JSON")
        for one in got if isinstance(got, list) else [got]:
            if isinstance(one, dict) and one.get("id") == msg["id"]:
                return one
        raise Failed(f"{what}: no reply to it")

    def _events(self, r: Any, want: int, what: str) -> Any:
        data: List[str] = []
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line or not data:
                continue
            try:
                msg = json.loads("\n".join(data))
            except ValueError:
                msg = None
            data = []
            if isinstance(msg, dict) and msg.get("id") == want and ("result" in msg or "error" in msg):
                return msg
        raise Failed(f"{what}: the event stream ended without a reply")

    def tell(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method}, method)

    def ask(self, method: str, params: dict) -> Any:
        self.next_id += 1
        return self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}, method)

    def close(self) -> None:
        if not self.session:
            return
        req = urllib.request.Request(self.url, headers={"Mcp-Session-Id": self.session}, method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=2).close()
        except Exception:  # ending the session is a courtesy
            pass


def probe(server: dict, timeout: float = 15) -> Dict[str, Any]:
    """Start `server`, shake hands, list its tools, stop it. Never raises."""
    start = time.monotonic()
    out: Dict[str, Any] = {"ok": False, "error": "", "name": "", "version": "", "tools": [], "seconds": 0.0}
    env = server.get("env") if isinstance(server, dict) and isinstance(server.get("env"), dict) else {}
    conn: Any = None
    try:
        if not isinstance(server, dict):
            raise Failed("server must be {\"command\": …} or {\"url\": …}")
        deadline = start + max(0.1, float(timeout))
        if isinstance(server.get("url"), str) and server["url"]:
            conn = _Http(server["url"], deadline)
        elif isinstance(server.get("command"), str) and server["command"].strip():
            conn = _Stdio(server, deadline)
        else:
            raise Failed("server has neither a command nor a url")
        _handshake(conn.ask, conn.tell, out)
        out["ok"] = True
    except Failed as e:
        out["error"] = str(e)
    except Exception as e:  # the page must get an answer, whatever the server did
        out["error"] = f"{type(e).__name__}: {_short(str(e), 200)}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    for k in ("error", "name", "version"):
        out[k] = _hide(out[k], env)
    out["tools"] = [{k: _hide(v, env) for k, v in t.items()} for t in out["tools"]]
    out["seconds"] = round(time.monotonic() - start, 2)
    return out
