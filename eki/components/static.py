"""eki's own small static file server: one folder, under one URL prefix, on 127.0.0.1.

    python -m eki.components.static --root <folder> --prefix /chapter1/ --port <n>
        [--header 'Cache-Control: no-store']… [--index index.html]

Threaded; files under --root are served at --prefix (anything else is 404,
the prefix without its trailing slash redirects to it). Symlinks inside the
folder are followed — an assets/ link to the project's pool is served
through — but a '..' in a path is refused. Range requests (206, 416) so
audio and video seek; MIME types right for the web's newer files; every
response carries the configured headers; never a directory listing.
`GET /__eki_static__` answers the served root, so eki can tell its server
for this folder is the one on the port.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HEALTH = "/__eki_static__"
CHUNK = 256 * 1024
TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json",
    ".txt": "text/plain; charset=utf-8", ".wasm": "application/wasm",
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
    ".ktx2": "image/ktx2", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".avif": "image/avif", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac", ".mp4": "video/mp4",
    ".webm": "video/webm", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".otf": "font/otf", ".hdr": "application/octet-stream", ".exr": "image/x-exr",
}
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def content_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def byte_range(header: str, size: int) -> Optional[Tuple[int, int]]:
    """(first, last) for a single `bytes=` range; None when it's not one we serve
    (then the whole file); ValueError when it is out of range."""
    m = RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    a, b = m.group(1), m.group(2)
    if not a:                                    # the last n bytes
        n = int(b)
        if n == 0 or size == 0:
            raise ValueError("empty suffix")
        return max(0, size - n), size - 1
    first = int(a)
    last = min(int(b), size - 1) if b else size - 1
    if first >= size or (b and int(b) < first):
        raise ValueError("out of range")
    return first, last


class Handler(BaseHTTPRequestHandler):
    root: Path = Path(".")
    prefix: str = "/"
    index: str = "index.html"
    extra: Dict[str, str] = {}
    server_version = "eki-static"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):          # quiet: a page asks for hundreds of files
        pass

    def _head(self, code: int, headers: Dict[str, str]) -> None:
        self.send_response(code)
        for k, v in {**headers, **self.extra}.items():
            self.send_header(k, v)
        self.end_headers()

    def _plain(self, code: int, text: str, headers: Optional[Dict[str, str]] = None) -> None:
        body = text.encode()
        self._head(code, {"Content-Type": "text/plain; charset=utf-8",
                          "Content-Length": str(len(body)), **(headers or {})})
        if self.command != "HEAD":
            self.wfile.write(body)

    def _target(self, path: str) -> Tuple[Optional[Path], str]:
        """The file a URL path names, or (None, a redirect location or '')."""
        if path + "/" == self.prefix:
            return None, self.prefix
        if not path.startswith(self.prefix):
            return None, ""
        rel = path[len(self.prefix):]
        parts = [p for p in rel.split("/") if p]
        if any(p in ("..", ".") or "\\" in p or "\0" in p for p in parts):
            return None, ""
        f = self.root.joinpath(*parts) if parts else self.root
        if f.is_dir():
            if not path.endswith("/"):
                return None, path + "/"
            f = f / self.index
        return (f, "") if f.is_file() else (None, "")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if path == HEALTH:
            return self._plain(200, str(self.root))
        f, where = self._target(path)
        if f is None:
            if where:
                loc = urllib.parse.quote(where) + (f"?{url.query}" if url.query else "")
                return self._plain(301, f"moved to {loc}\n", {"Location": loc})
            return self._plain(404, "not found\n")
        try:
            fh = open(f, "rb")
        except OSError:
            return self._plain(404, "not found\n")
        with fh:
            size = os.fstat(fh.fileno()).st_size
            headers = {"Content-Type": content_type(f.name), "Accept-Ranges": "bytes"}
            try:
                rng = byte_range(self.headers.get("Range", ""), size) if self.headers.get("Range") else None
            except ValueError:
                return self._plain(416, "range not satisfiable\n", {"Content-Range": f"bytes */{size}"})
            first, last = rng if rng else (0, size - 1)
            length = max(0, last - first + 1)
            headers["Content-Length"] = str(length)
            if rng:
                headers["Content-Range"] = f"bytes {first}-{last}/{size}"
            self._head(206 if rng else 200, headers)
            if self.command == "HEAD":
                return
            fh.seek(first)
            left = length
            while left > 0:
                chunk = fh.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):  # a seek drops the old request
            self.close_connection = True


def header(text: str) -> Tuple[str, str]:
    k, sep, v = text.partition(":")
    if not sep or not k.strip():
        raise argparse.ArgumentTypeError(f"a header is 'Name: value' (got {text!r})")
    return k.strip(), v.strip()


def serve(root: Path, prefix: str, port: int, headers: List[Tuple[str, str]], index: str) -> None:
    attrs = {"root": root.resolve(), "prefix": prefix, "index": index, "extra": dict(headers)}
    handler = type("Served", (Handler,), attrs)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    print(f"serving {attrs['root']} at http://127.0.0.1:{port}{prefix}", flush=True)
    httpd.serve_forever()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="eki.components.static")
    ap.add_argument("--root", required=True)
    ap.add_argument("--prefix", default="/")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--header", type=header, action="append", default=[])
    ap.add_argument("--index", default="index.html")
    a = ap.parse_args(argv)
    if not a.prefix.startswith("/") or not a.prefix.endswith("/"):
        ap.error("--prefix must start and end with '/'")
    root = Path(a.root)
    if not root.is_dir():
        ap.error(f"--root {root} is not a folder")
    serve(root, a.prefix, a.port, a.header, a.index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
