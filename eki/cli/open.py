"""Open the web UI (in the Mac app if it's built, else the browser)."""
from __future__ import annotations

import subprocess
import webbrowser

from .. import server, shellbuild
from .common import ensure_engine

NAME = "open"
HELP = "open eki's window"


def add(p) -> None:
    p.add_argument("--browser", action="store_true", help="use the browser even if the app is built")


def run(args) -> int:
    ensure_engine()
    app = shellbuild.app_path()
    if app.exists() and not args.browser:
        subprocess.run(["open", str(app)])
    else:
        webbrowser.open(f"http://127.0.0.1:{server.port()}/")
    return 0
