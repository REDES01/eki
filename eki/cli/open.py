"""Open the web UI (in the Mac app if it's built, else the browser)."""
from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path

from .. import server
from .common import ensure_engine

NAME = "open"
HELP = "open eki's window"

APP = Path(__file__).resolve().parents[2] / "Eki.app"


def add(p) -> None:
    p.add_argument("--browser", action="store_true", help="use the browser even if the app is built")


def run(args) -> int:
    ensure_engine()
    if APP.exists() and not args.browser:
        subprocess.run(["open", str(APP)])
    else:
        webbrowser.open(f"http://127.0.0.1:{server.port()}/")
    return 0
