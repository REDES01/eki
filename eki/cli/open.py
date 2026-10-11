"""Open the web UI (in the Mac app if it's built, else the browser); with a path, the file in
its side panel too: queued straight in SQLite (eki/docs.py), so it works while the engine restarts."""
from __future__ import annotations

import os
import subprocess
import webbrowser

from .. import docs, paths, server, shellbuild
from .common import conn, ensure_engine, err

NAME = "open"
HELP = "open eki's window, or a file in its side panel"


def add(p) -> None:
    p.add_argument("path", nargs="?", help="a .md, .markdown or .txt file to show beside the chat")
    p.add_argument("--browser", action="store_true", help="use the browser even if the app is built")


def absolute(path: str) -> str:
    """`~` is your home as eki knows it (`paths.user_home()`); a relative path is from here."""
    if path == "~" or path.startswith("~/"):
        return str(paths.user_home() / path[2:])
    return os.path.abspath(os.path.expanduser(path))


def window(browser: bool) -> None:
    ensure_engine()
    app = shellbuild.app_path()
    if app.exists() and not browser:
        subprocess.run(["open", str(app)])
    else:
        webbrowser.open(f"http://127.0.0.1:{server.port()}/")


def run(args) -> int:
    if getattr(args, "path", None):
        where = absolute(args.path)
        try:
            docs.request_open(conn(), where)
        except (ValueError, KeyError) as e:
            err(f"can't open {where}: {e.args[0] if e.args else e}")
            return 1
    window(args.browser)
    return 0
