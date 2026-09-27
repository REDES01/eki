"""Settings for the web page: read, check and write routing.json and providers.json.

`read` gives the file as written plus what eki makes of it (`effective`):
the picture rows routing adds in memory, the ComfyUI default providers
adds in memory, what each provider can do. `write` checks first, refuses
a file changed on disk since it was read, keeps the old one as
`<name>.json.bak` and swaps the new one in atomically. Nothing is reloaded:
routing and providers read their files on every use.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from . import paths, providers, queue
from .routing import table

NAMES = ("routing", "providers")


class Stale(ValueError):
    """The file changed on disk since the page read it."""


class Invalid(ValueError):
    """The data has problems; `problems` lists each in words."""

    def __init__(self, problems: List[str]) -> None:
        super().__init__(f"{len(problems)} problem{'s' if len(problems) != 1 else ''}: " + "; ".join(problems))
        self.problems = problems


def _known(name: str) -> None:
    if name not in NAMES:
        raise KeyError(f"no settings named {name!r}")


def _ensure(name: str) -> None:
    """Make the file exist as eki does on first use."""
    if name == "routing":
        table.settings()
    else:
        providers.config()


def read(conn: sqlite3.Connection, name: str) -> Dict[str, Any]:
    _known(name)
    _ensure(name)
    path = paths.config(name)
    data = json.loads(path.read_text())
    if name == "routing":
        written = {r.get("key") for r in (data.get("rows") or []) if isinstance(r, dict)}
        rows = [{**r, "added": True} if r.get("key") not in written else dict(r) for r in table.rows()]
        effective: Dict[str, Any] = {"rows": rows}
    else:
        entries = {}
        for n, cfg in providers.config().items():
            entries[n] = {**cfg, "can_effective": providers.capabilities(n, cfg),
                          "unwritten": n in providers.UNWRITTEN and n not in data,
                          "builtin": n in providers.BUILTIN}
        effective = {"entries": entries}
    return {"data": data, "effective": effective, "mtime": path.stat().st_mtime, "path": str(path)}


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_routing(data: Any, known: set) -> List[str]:
    if not isinstance(data, dict):
        return ["routing settings must be an object"]
    rows = data.get("rows")
    if not isinstance(rows, list):
        return ["rows must be a list"]
    out: List[str] = []
    seen: set = set()
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            out.append(f"row {i} is not an object")
            continue
        key = r.get("key")
        label = f"row {key!r}" if isinstance(key, str) and key else f"row {i}"
        if not isinstance(key, str) or not key.strip():
            out.append(f"row {i} has no key")
        elif key in seen:
            out.append(f"row key {key!r} is used twice")
        seen.add(key)
        targets = r.get("targets")
        if not _strings(targets):
            out.append(f"{label}: targets must be a list of provider names")
        else:
            out += [f"{label}: no provider named {t!r}" for t in targets if t not in known]
        needs = r.get("needs", [])
        if not _strings(needs):
            out.append(f"{label}: needs must be a list")
        else:
            out += [f"{label}: {n!r} is not an ability (one of {', '.join(providers.ABILITIES)})"
                    for n in needs if n not in providers.ABILITIES]
    if "general" not in seen:
        out.append("there must be a 'general' row")
    me = data.get("self")
    if me is not None and not isinstance(me, dict):
        out.append("self must be an object")
    elif me and "autonomy" in me and me["autonomy"] not in queue.MODES:
        out.append(f"self.autonomy is one of {', '.join(queue.MODES)}, not {me['autonomy']!r}")
    return out


def _check_providers(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return ["providers settings must be an object of entries"]
    out: List[str] = []
    for n, cfg in data.items():
        if n in providers.BUILTIN:
            out.append(f"{n!r} is built in and can't be written")
            continue
        if not isinstance(cfg, dict):
            out.append(f"{n!r} is not an object")
            continue
        kind = cfg.get("kind")
        if kind == "command":
            out.append(f"{n!r}: kind 'command' can't be written")
        elif kind not in providers.KINDS:
            out.append(f"{n!r}: unknown kind {kind!r}")
        if "can" in cfg:
            can = cfg["can"]
            if not _strings(can):
                out.append(f"{n!r}: can must be a list")
            else:
                out += [f"{n!r}: {a!r} is not an ability (one of {', '.join(providers.ABILITIES)})"
                        for a in can if a not in providers.ABILITIES]
    return out


def check(name: str, data: Any, provider_data: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every problem with `data` as settings `name`, in words; [] when it can be written.
    Routing targets may name providers in providers.config() or in `provider_data`."""
    _known(name)
    if name == "providers":
        return _check_providers(data)
    known = set(providers.config())
    if isinstance(provider_data, dict):
        known |= set(provider_data)
    return _check_routing(data, known)


def write(conn: sqlite3.Connection, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _known(name)
    _ensure(name)
    path = paths.config(name)
    mtime = body.get("mtime")
    if mtime is not None and float(mtime) != path.stat().st_mtime:
        raise Stale(f"{path.name} changed on disk since it was loaded; reload it first")
    data = body.get("data")
    problems = check(name, data)
    if problems:
        raise Invalid(problems)
    backup = path.with_name(path.name + ".bak")
    old = path.read_bytes()
    tmp = backup.with_name(f".{backup.name}.tmp-{os.getpid()}")
    tmp.write_bytes(old)
    os.replace(tmp, backup)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return {"ok": True, "path": str(path), "backup": str(backup)}
