"""Local models: started, stopped and kept up by eki, on this Mac's terms.

A local provider with a `serve` block can be started by eki:

    "local": {"kind": "local", "base_url": "http://127.0.0.1:8080",
              "serve": {"command": ["./.venv/bin/python", "-m", "mlx_lm", "server", …],
                        "cwd": "~/ai/llm", "env": {"HF_HUB_OFFLINE": "1"}},
              "keep_up": true}

`keep_up` means the engine keeps it running while the Mac has memory to
spare, and steps it out (stops it) when memory comes under pressure and
nothing is using it — then brings it back once pressure has eased. A model
you stop yourself stays stopped until you start it. eki only ever stops a
server it started; one you started yourself is left alone.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import machine, paths, providers, store

log = logging.getLogger("eki.models")

#: after stepping out for memory, wait this long before trying again
STEP_OUT_PAUSE = 5 * 60


def _state_path(name: str) -> Path:
    d = paths.home() / "models"
    d.mkdir(exist_ok=True)
    return d / f"{name}.json"


def _read(name: str) -> Dict[str, Any]:
    try:
        return json.loads(_state_path(name).read_text())
    except (OSError, ValueError):
        return {}


def _write(name: str, **fields: Any) -> None:
    st = _read(name)
    st.update(fields)
    _state_path(name).write_text(json.dumps(st))


def local_models() -> Dict[str, Dict[str, Any]]:
    return {n: c for n, c in providers.config().items() if c.get("kind") == "local"}


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status(name: str) -> Dict[str, Any]:
    cfg = local_models().get(name)
    if cfg is None:
        raise KeyError(f"no local model named {name!r}")
    st = _read(name)
    up, why = providers.build(name, cfg).available()
    ours = _alive(st.get("pid"))
    return {"name": name, "label": cfg.get("label") or name, "up": up, "why": why,
            "starting": ours and not up, "managed": ours, "startable": bool(cfg.get("serve")),
            "keep_up": bool(cfg.get("keep_up")), "held": bool(st.get("held")),
            "stepped_out_at": st.get("stepped_out_at"), "pid": st.get("pid") if ours else None}


def start(name: str, *, by: str = "you") -> Dict[str, Any]:
    cfg = local_models().get(name)
    if cfg is None:
        raise KeyError(f"no local model named {name!r}")
    st = status(name)
    if by == "you":
        _write(name, held=False)
    if st["up"] or st["starting"]:
        return status(name)
    serve = cfg.get("serve")
    if not serve:
        raise ValueError(f"{name} has no `serve` command; start it yourself")
    env = dict(os.environ)
    env.update({k: os.path.expanduser(str(v)) for k, v in (serve.get("env") or {}).items()})
    logf = open(paths.logs() / f"model-{name}.log", "ab")
    proc = subprocess.Popen([os.path.expanduser(a) if a.startswith("~") else a for a in serve["command"]],
                            cwd=os.path.expanduser(serve.get("cwd") or "~"), env=env,
                            stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, start_new_session=True)
    logf.close()
    _write(name, pid=proc.pid, started_at=time.time(), by=by)
    log.info("started %s (pid %s) for %s", name, proc.pid, by)
    return status(name)


def stop(name: str, *, by: str = "you") -> Dict[str, Any]:
    st = _read(name)
    pid = st.get("pid")
    if by == "you":
        _write(name, held=True)
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.1)
        log.info("stopped %s (pid %s) for %s", name, pid, by)
    _write(name, pid=None)
    return status(name)


def ensure(name: str, timeout: float = 180) -> bool:
    """Up, or started and waited for. For a run that was sent to this model."""
    st = status(name)
    if st["up"]:
        return True
    if not st["startable"]:
        return False
    start(name, by="run")
    end = time.time() + timeout
    while time.time() < end:
        if status(name)["up"]:
            return True
        time.sleep(1)
    return False


def in_use(conn, name: str) -> bool:
    return any(r["provider"] == name for r in store.runs_in(conn, ("starting", "running")))


def duty(conn) -> List[str]:
    """The engine's rounds: keep models up while there's room, step out when there isn't."""
    did: List[str] = []
    pressure = machine.memory_pressure()
    for name, cfg in local_models().items():
        if not cfg.get("serve"):
            continue
        st = status(name)
        if st["managed"] and pressure > 1 and not in_use(conn, name):
            stop(name, by="memory")
            _write(name, stepped_out_at=time.time())
            did.append(f"stepped {name} out: memory under pressure")
        elif (cfg.get("keep_up") and not st["up"] and not st["starting"] and not st["held"]
              and pressure == 1 and time.time() - (st["stepped_out_at"] or 0) > STEP_OUT_PAUSE):
            start(name, by="keep_up")
            did.append(f"started {name}: kept up")
    return did
