"""The engine's web face: the UI's files and a small JSON API, on 127.0.0.1 only.

UI files are read from disk on every request, so a change to them is live
on the next refresh — no rebuild, no restart. A POST must carry the header
`X-Eki: 1`, which a page from another site can't send without asking
first (and eki never answers that ask), so only eki's own page can act.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from . import api, db, observe

log = logging.getLogger("eki.server")

WEB = Path(__file__).resolve().parent / "web"


def port() -> int:
    return int(os.environ.get("EKI_PORT") or 7788)


class Handler(BaseHTTPRequestHandler):
    server_version = "eki"

    def log_message(self, fmt: str, *args: Any) -> None:     # quiet: the engine log is for the engine
        pass

    # ---- plumbing ------------------------------------------------------------------------

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel: str) -> None:
        path = (WEB / rel).resolve()
        if WEB not in path.parents or not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _served_file(self, path: str) -> None:
        from . import files
        conn = db.connect()
        try:
            got = files.read(conn, path)
        finally:
            conn.close()
        if got is None:
            return self._json({"error": "not a file a run made"}, 404)
        body, ctype = got
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # whatever an agent wrote runs with no rights of eki's own
        self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-popups")
        self.end_headers()
        self.wfile.write(body)

    def _call(self, fn: Callable[..., Any], *args: Any) -> None:
        conn = db.connect()
        try:
            self._json(fn(conn, *args))
        except KeyError as e:
            self._json({"error": str(e).strip("'\"")}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:                       # noqa: BLE001
            log.exception("api error")
            observe.fault(None, f"server {self.path}")
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            conn.close()

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _upload(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20 * 1024 * 1024:
            self.close_connection = True           # the body is left unread
            return self._json({"error": "picture over 20 MB"}, 400)
        data = self.rfile.read(n)
        self._call(lambda _c: api.upload(data, self.headers.get("X-Filename") or ""))

    # ---- routes --------------------------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        if p == "/" or p == "/index.html":
            return self._file("index.html")
        if p.startswith("/ui/"):
            return self._file(p[len("/ui/"):])
        if p == "/api/status":
            return self._call(api.status)
        if p == "/api/threads":
            return self._call(api.threads)
        m = re.fullmatch(r"/api/threads/(\w+)", p)
        if m:
            return self._call(api.thread, m.group(1))
        m = re.fullmatch(r"/api/threads/(\w+)/events", p)
        if m:
            return self._call(api.events, m.group(1), _num(q.get("after"), 0, int),
                              min(_num(q.get("wait"), 20, float), 25))
        if p == "/api/file":
            return self._served_file(q.get("path") or "")
        if p == "/api/asks":
            return self._call(api.open_asks)
        if p == "/api/providers":
            return self._call(api.provider_list)
        if p == "/api/route":
            return self._call(api.route, q.get("q"))
        if p == "/api/pictures":
            return self._call(api.pictures, max(1, min(_num(q.get("limit"), 60, int), 500)),
                              _num(q.get("before"), None, float))
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.headers.get("X-Eki") != "1":
            return self._json({"error": "missing X-Eki header"}, 403)
        p = urlparse(self.path).path
        if p == "/api/attachments":
            return self._upload()
        body = self._body()
        if p == "/api/ask":
            return self._call(api.ask, body)
        m = re.fullmatch(r"/api/asks/(\w+)/answer", p)
        if m:
            return self._call(api.answer, m.group(1), body)
        m = re.fullmatch(r"/api/runs/(\w+)/cancel", p)
        if m:
            return self._call(api.cancel, m.group(1))
        m = re.fullmatch(r"/api/models/(\w+)/(start|stop)", p)
        if m:
            name, action = m.group(1), m.group(2)
            return self._call(lambda _c: api.model_action(name, action, body))
        self._json({"error": "not found"}, 404)


def _num(value: Optional[str], default: Any, kind: Callable[[str], Any]) -> Any:
    try:
        return kind(value) if value else default
    except ValueError:
        return default


def serve(listen_port: Optional[int] = None) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port() if listen_port is None else listen_port), Handler)
    srv.daemon_threads = True
    return srv


def start_in_background() -> Optional[ThreadingHTTPServer]:
    """Run the server on a thread of the engine. A port in use is logged, not fatal:
    the engine's real job is the runs."""
    if port() == 0 and os.environ.get("EKI_PORT") == "0":
        return None
    try:
        srv = serve()
    except OSError as e:
        log.error("web UI not served: port %s: %s", port(), e)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    log.info("web UI on http://127.0.0.1:%s", srv.server_address[1])
    return srv
