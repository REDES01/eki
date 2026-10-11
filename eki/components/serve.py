"""The `serve` kind: your own copy of the project in a browser, one static server per folder.

    {"kind": "serve", "about": "…", "path": "/chapter1/",
     "headers": {"Cache-Control": "no-store"}, "index": "index.html", "idle_stop": 30}

Each folder served — a worktree, or the person's own project folder — gets
its own port (ports.py, recorded, so the same folder gets the same one after
a restart) and its own server: eki's static.py run detached as a component
server (servers.py, state keyed by card + served folder), started on first
use and stopped after `idle_stop`. `path` is the URL prefix the folder is
served under, so a project's absolute paths keep their shape. A run in a
folder that reaches a serve card gets the link (`for_run`); `eki use <name>`
starts it for the caller's checkout and prints it. When a served folder is
gone (its worktree removed), `tick` stops its server and frees its port.

An optional `build` (e.g. "npm run build") runs in the served folder before
its server starts and whenever its sources changed since the last build
(servebuild.py); a failed build serves nothing and the call fails with the
output's tail.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

from .. import paths
from . import Call, Component, Result, find, ports, servebuild, servers
from .static import HEALTH

log = logging.getLogger("eki.components.serve")

ABOUT = "a running copy of the project for each agent, so it can test its change without touching yours"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Preview links"
EXPLAIN = ("Every agent working on this project gets its own running copy of it (a preview link), so it can try "
           "its change in a browser without touching yours. A copy starts when an agent first opens it and stops "
           "on its own when nobody has looked at it for a while. Your own copy keeps running where it always "
           "does.")
DEFAULT_HEADERS = {"Cache-Control": "no-store"}
#: how long a static server gets to answer after it is started
START_WAIT = 10.0
FIELD_HELP = {"build": "a shell command run in the served folder before serving it and whenever its sources "
                       "changed since the last build (e.g. npm run build); empty: none"}


def validate(spec: dict, folder: Path) -> List[str]:
    errs = []
    p = spec.get("path", "/")
    if not isinstance(p, str) or not p.startswith("/") or not p.endswith("/") or "//" in p or ".." in p:
        errs.append(f'"path" must be a URL prefix starting and ending with "/" (e.g. "/chapter1/"), got {p!r}')
    h = spec.get("headers", DEFAULT_HEADERS)
    if not isinstance(h, dict) or not all(isinstance(k, str) and k.strip() and ":" not in k
                                          and isinstance(v, str) and "\n" not in v for k, v in h.items()):
        errs.append('"headers" must be an object of header names to string values')
    o = spec.get("open", "")
    if not isinstance(o, str) or o.startswith("/") or ".." in o.split("/") or "://" in o:
        errs.append('"open" must be a page inside the folder, e.g. "slice/index.html"')
    i = spec.get("index", "index.html")
    if not isinstance(i, str) or not i.strip() or "/" in i:
        errs.append('"index" must be a file name, e.g. "index.html"')
    if not isinstance(spec.get("build", ""), str):
        errs.append('"build" must be a shell command, e.g. "npm run build"')
    return errs


def _prefix(comp: Component) -> str:
    return comp.spec.get("path") or "/"


def _headers(comp: Component) -> Dict[str, str]:
    h = comp.spec.get("headers")
    return dict(h) if isinstance(h, dict) else dict(DEFAULT_HEADERS)


def link(comp: Component, folder) -> str:
    """The URL `folder` is (or will be) served at; allots and records its port. Starts nothing."""
    return f"http://127.0.0.1:{ports.allot(folder)}{_prefix(comp)}"


def entry(comp: Component, url: str) -> str:
    """The page a run opens: the served base plus the card's `open` page, when it names one."""
    o = str(comp.spec.get("open") or "")
    return url + o if url and o else url


def answers(port: int, folder) -> bool:
    """Our static server for `folder` is the one on `port`."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{HEALTH}", timeout=1.0) as r:
            return r.status == 200 and r.read().decode(errors="replace").strip() == ports.norm(folder)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _spawn(comp: Component, folder: str, port: int, by: str) -> int:
    argv = [sys.executable, "-m", "eki.components.static", "--root", folder,
            "--prefix", _prefix(comp), "--port", str(port),
            "--index", str(comp.spec.get("index") or "index.html")]
    for k, v in _headers(comp).items():
        argv += ["--header", f"{k}: {v}"]
    env = dict(os.environ)
    here = str(Path(__file__).resolve().parents[2])           # the folder eki is imported from
    env["PYTHONPATH"] = here + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    logf = open(paths.logs() / f"component-{servers.key(comp.name, comp.folder, folder)}.log", "ab")
    try:
        proc = subprocess.Popen(argv, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=logf,
                                stderr=logf, start_new_session=True)
    finally:
        logf.close()
    servers.write(comp, folder, pid=proc.pid, port=port, started_at=time.time(), by=by,
                  **({"last_run": by} if by else {}))
    log.info("started %s for %s on port %s (pid %s) for %s", comp.name, folder, port, proc.pid, by or "a call")
    return proc.pid


def ensure(comp: Component, folder, by: str = "") -> dict:
    """Start the static server for `folder` unless it is up; record `by` as its latest use.
    The card's build runs first when the server starts or the folder's sources changed."""
    folder = ports.norm(folder)
    if not Path(folder).is_dir():
        return {"up": False, "url": "", "why": f"{folder} is not a folder"}
    url = link(comp, folder)
    port = ports.get(folder) or 0
    if answers(port, folder):
        failed = servebuild.build(comp, folder) if servebuild.changed(comp, folder) else None
        if failed:
            return {"up": False, "url": url, "why": failed}
        if servers.ours(comp, folder):
            servers.write(comp, folder, last_call_at=time.time(), **({"last_run": by} if by else {}))
        return {"up": True, "url": url, "why": ""}
    servers.stop(comp, folder)                    # a server of ours that doesn't answer: start afresh
    failed = servebuild.build(comp, folder)
    if failed:
        return {"up": False, "url": url, "why": failed}
    if not ports.bindable(port):                  # someone else took the recorded port: take another
        ports.free(folder)
        url = link(comp, folder)
        port = ports.get(folder) or 0
    pid = _spawn(comp, folder, port, by)
    end = time.time() + START_WAIT
    while time.time() < end:
        if answers(port, folder):
            return {"up": True, "url": url, "why": ""}
        if not servers.alive(pid):
            return {"up": False, "url": url, "why": f"{comp.name}'s server for {folder} exited while starting "
                                                    f"(see the log in {paths.logs()})"}
        time.sleep(servers.POLL)
    return {"up": False, "url": url, "why": f"{comp.name}'s server for {folder} didn't answer "
                                            f"within {START_WAIT:g} s"}


def checkout(folder) -> str:
    """The top of the git checkout containing `folder`; the folder itself when not in git."""
    from .. import workspace
    return ports.norm(workspace.repo_of(folder) or folder)


def link_for(folder, conn=None, by: str = "") -> Tuple[str, str]:
    """(the serving URL for `folder`, why there is none): ("", "") when it reaches no serve card. Never raises."""
    if not folder:
        return "", ""
    try:
        found, _ = find(folder, conn)
        cards = [c for _, c in sorted(found.items()) if c.kind == "serve"]
        if not cards:
            return "", ""
        got = ensure(cards[0], checkout(folder), by=by)
        return (entry(cards[0], got["url"]), "") if got["up"] else ("", got["why"])
    except Exception as e:                        # noqa: BLE001 — a run never fails for its link
        return "", str(e) or type(e).__name__


def for_run(folder, conn=None, by: str = "") -> str:
    """The serving URL for a run working in `folder`; '' when there is none. Never raises."""
    url, why = link_for(folder, conn, by)
    if why:
        log.warning("no serving link for %s: %s", folder, why)
    return url


def _mine(comp: Component) -> List[dict]:
    return [st for st in servers.states()
            if st.get("name") == comp.name and st.get("folder") == str(comp.folder) and st.get("served")]


def links(comp: Component) -> List[dict]:
    """[{"folder", "url"}] for each folder whose server for this card is up now."""
    out = []
    for st in _mine(comp):
        folder, port = st["served"], ports.get(st["served"])
        if port and servers.alive(st.get("pid")) and answers(port, folder):
            out.append({"folder": folder, "url": entry(comp, f"http://127.0.0.1:{port}{_prefix(comp)}")})
    return out


def call(comp: Component, c: Call) -> Result:
    if c.args:
        return Result(error=f"{comp.name}: `eki use {comp.name}` takes no arguments; it prints your link")
    if not c.caller_dir or not os.path.isdir(c.caller_dir):
        return Result(error=f"{comp.name}: call it from the folder you want served")
    got = ensure(comp, checkout(c.caller_dir), by=c.caller_run or c.run_id)
    return Result(text=entry(comp, got["url"])) if got["up"] else Result(error=got["why"])


def is_up(comp: Component) -> bool:
    return bool(links(comp))


def stop(comp: Component) -> bool:
    stopped = False
    for st in _mine(comp):
        stopped = servers.stop(comp, st["served"]) or stopped
    return stopped


def notes(comp: Component) -> List[str]:
    up = [f'{x["folder"]} → {x["url"]}' for x in links(comp)]
    return up or [f"serves each worktree under {_prefix(comp)}, started on first use"]


def tick(conn=None) -> List[str]:
    """Housekeeping: a served folder that is gone has its server stopped and its port freed."""
    said = []
    for st in servers.states():
        served = st.get("served")
        if served and not Path(served).is_dir():
            servers.stop_pid(st.get("pid"), Path(st["state"]), st.get("name", "serve"))
            said.append(f"stopped {st.get('name', 'serve')} for {served}: the folder is gone")
    for folder, port in ports.allotted().items():
        if not Path(folder).is_dir():
            ports.free(folder)
            said.append(f"freed port {port}: {folder} is gone")
    return said
