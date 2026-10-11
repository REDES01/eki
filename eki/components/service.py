"""The `service` kind: a command per call, a server kept up while used, or one of eki's providers.

    {"kind": "service", "about": "…", "run": ["tts", "--out", "x.wav"]}
        a command per call: the args appended, cwd the caller's folder,
        env EKI_CALLER_DIR. Its stdout is the text; printed lines naming
        existing files are the output paths; a non-zero exit is an error.
    {"kind": "service", "about": "…", "serve": [argv], "cwd": "…", "env": {…},
     "url": "http://127.0.0.1:9000", "health": "/health", "start_timeout": 120,
     (`health` is a path under `url`, or a full URL when there is no `url`)
     "call": [argv]}
        a server started on the first call (servers.py), the call argv run as
        above with EKI_COMPONENT_URL set; stopped after `idle_stop` minutes.
    {"kind": "service", "about": "…", "provider": "comfyui"}
        one turn of that eki provider; the pictures it makes are the paths.
    {"kind": "service", "about": "…", "target": "comfyui:<workflow>", "out": "assets/generated/<card>/"}
        a picture card: a ComfyUI workflow (eki/providers/comfyui_workflows.py)
        and the folder, relative to the project, its pictures are saved in.
Any form may add `"mcp": {"command", "args", "env"}` or `{"url"}` (an mcp card's
"server"): this project's runs get that server under the card's name, and a run
using a command server holds the card like a call does (mcpserver.servers_of).
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from . import Call, Component, Result, servers

ABOUT = "a program the project's agents can call, like making a picture or reading a line aloud"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Tool"
EXPLAIN = ("A program the project's agents can call to get something done that a model can't do by itself, such "
           "as making a picture, reading a line aloud or checking a build. It is set up once here, so every "
           "agent calls it the same way and sees what came out. You can also run it yourself from this card.")
#: how much of stderr a failed command reports
TAIL = 800


def _argv(v: Any) -> List[str]:
    if isinstance(v, str):
        return shlex.split(v)
    return [str(a) for a in v] if isinstance(v, list) else []


def _picture(spec: dict) -> List[str]:
    from .. import targets
    errs = []
    try:
        targets.parse(spec.get("target"))
    except ValueError as e:
        errs.append(f'"target" must name a workflow, e.g. "comfyui:draft" ({e})')
    out = spec.get("out")
    if not isinstance(out, str) or not out.strip() or Path(out).is_absolute() or ".." in Path(out).parts \
            or out.strip().startswith("~"):
        errs.append('"out" must be a folder inside the project, e.g. "assets/generated/portrait/"')
    return errs


def provider_name(spec: dict) -> str:
    """The eki provider a provider-form or picture card runs on ("" for the others)."""
    if isinstance(spec.get("provider"), str):
        return spec["provider"]
    if "target" in spec:
        from .. import targets
        return targets.provider_of(spec["target"])
    return ""


def validate(spec: dict, folder: Path) -> List[str]:
    errs = _calls(spec)
    if "mcp" in spec:
        from . import mcpserver
        errs += [f'"mcp": {e}' for e in mcpserver.validate({"server": spec["mcp"]}, folder)]
    return errs


def _calls(spec: dict) -> List[str]:
    if "target" in spec and not any(k in spec for k in ("run", "serve", "provider")):
        return _picture(spec)
    forms = [k for k in ("run", "serve", "provider") if k in spec]
    if not forms:
        return ['a service needs "run", or "serve" + "health" + "call", or "provider", '
                'or "target" + "out" (a picture card)']
    form = "provider" if "provider" in forms else forms[0]     # the form a call takes: provider, run, serve
    if form == "provider":
        return [] if isinstance(spec["provider"], str) and spec["provider"].strip() else \
            ['"provider" must name one of eki\'s providers']
    errs = []
    for k in ("run",) if form == "run" else ("serve", "call"):
        if not _argv(spec.get(k)):
            errs.append(f'"{k}" must be a command: a list of words')
    if form == "serve":
        if not isinstance(spec.get("health"), str) or not spec.get("health"):
            errs.append('a served component needs serve, health and call (missing health)')
        if "url" in spec and not isinstance(spec["url"], str):
            errs.append('"url" must be a string')
        if "env" in spec and not isinstance(spec["env"], dict):
            errs.append('"env" must be an object')
    return errs


def _placeholder(comp: Component, argv: List[str], key: str) -> str:
    if any(a.startswith("<") for a in argv):
        return f'fill in "{key}" in {comp.folder / "component.json"}'
    return ""


def _command(comp: Component, argv: List[str], c: Call, env_extra: Dict[str, str]) -> Result:
    cwd = c.caller_dir if c.caller_dir and os.path.isdir(c.caller_dir) else str(comp.folder)
    env = dict(os.environ, EKI_CALLER_DIR=c.caller_dir or cwd, **env_extra)
    argv = [os.path.expanduser(a) if a.startswith("~") else a for a in argv] + list(c.args)
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as e:
        return Result(error=f"{comp.name}: can't run {argv[0]}: {e}")
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip()[-TAIL:]
        return Result(text=p.stdout, error=f"{comp.name} exited {p.returncode}" + (f": {tail}" if tail else ""))
    found = []
    for line in p.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        f = Path(os.path.expanduser(s))
        f = f if f.is_absolute() else Path(cwd) / f
        if f.is_file() and str(f.resolve()) not in found:
            found.append(str(f.resolve()))
    return Result(text=p.stdout, paths=found)


def _provider(comp: Component, c: Call) -> Result:
    from .. import models, providers
    from ..providers.base import Turn
    name = comp.spec["provider"]
    try:
        prov = providers.get(name)
    except KeyError:
        return Result(error=f"{comp.name}: no provider named {name!r}")
    if name in models.managed() and not models.ensure(name):
        return Result(error=f"{comp.name}: {name} isn't up and couldn't be started")
    texts: List[str] = []
    made: List[str] = []

    def emit(kind: str, data: Dict[str, Any]) -> None:
        if kind == "text":
            texts.append(data.get("text", ""))
        elif kind == "tool" and data.get("path"):
            made.append(str(data["path"]))
        if c.emit:
            c.emit(kind, data)

    turn = Turn(prompt=" ".join(c.args), history=[], run_id=c.run_id,
                thread_id=str((c.extra or {}).get("thread_id") or ""), cwd=c.caller_dir,
                extra={k: v for k, v in (c.extra or {}).items() if k != "thread_id"})
    out = prov.take(turn, emit)
    text = "\n".join(t for t in texts if t)
    if out.state != "done" or out.error:
        return Result(text=text, paths=made, error=f"{comp.name}: {out.error or out.state}")
    return Result(text=text, paths=made)


def call(comp: Component, c: Call) -> Result:
    spec = comp.spec
    if "provider" in spec:
        return _provider(comp, c)
    if "target" in spec and "run" not in spec and "serve" not in spec:
        return Result(error="picture cards: not callable yet")
    if "run" in spec:
        argv = _argv(spec["run"])
        why = _placeholder(comp, argv, "run")
        return Result(error=why) if why else _command(comp, argv, c, {})
    argv = _argv(spec["call"])
    why = _placeholder(comp, argv, "call") or _placeholder(comp, _argv(spec["serve"]), "serve")
    if why:
        return Result(error=why)
    got = servers.start(comp, by=c.run_id)
    if not got["up"]:
        return Result(error=got["why"])
    if servers.ours(comp):
        servers.used(comp, c.run_id)
    return _command(comp, argv, c, {"EKI_COMPONENT_URL": str(spec.get("url") or "")})


def _managed(comp: Component) -> str:
    from .. import models
    name = provider_name(comp.spec)
    return name if name and name in models.managed() else ""


def is_up(comp: Component) -> bool:
    spec = comp.spec
    if provider_name(spec):
        from .. import models, providers
        name = _managed(comp)
        if name:
            return bool(models.status(name)["up"])
        try:
            return bool(providers.get(provider_name(spec)).available()[0])
        except KeyError:
            return False
    if "serve" in spec:
        return servers.is_up(comp)
    return False


def stop(comp: Component) -> bool:
    if provider_name(comp.spec):
        from .. import models
        name = _managed(comp)
        if not name or not models.status(name)["managed"]:
            return False
        models.stop(name, by="component")
        return True
    if "serve" in comp.spec:
        return servers.stop(comp)
    return False


def notes(comp: Component) -> List[str]:
    spec = comp.spec
    out = []
    for k in ("run", "call", "serve"):
        if k in spec and _placeholder(comp, _argv(spec[k]), k):
            out.append(f'"{k}" is a placeholder: fill it in before calling')
    if provider_name(spec):
        out.append(f"runs on eki's {provider_name(spec)} provider")
    if "target" in spec and "out" in spec:
        out.append(f"draws with {spec['target']}, saves into {spec['out']}")
    m = spec.get("mcp")
    if isinstance(m, dict):
        out.append(f"MCP over url: {m.get('url')}, not held" if "url" in m else
                   "MCP: " + " ".join([str(m.get("command", ""))] + [str(a) for a in m.get("args") or []]))
    if "serve" in spec:
        out.append(f"server at {spec.get('url') or servers.health_url(spec)}, up" if servers.is_up(comp) else
                   f"server at {spec.get('url') or servers.health_url(spec)}, started on the first call")
    return out
