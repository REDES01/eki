"""Roles: eki's own jobs, and which target does each.

    router     the prompt check and narrow (eki/routing/check.py)
    planner    draft and plan runs (eki/selfdraft.py)
    reviewer   the review chore (eki/review.py)
    chores     digest prose, fault briefs, PR-comment triage

The setting is routing.json `"roles": {"router": "claude:fast", ...}`,
holding only the roles you set. With no entry a role reads the old settings,
which are never rewritten: router the top-level `checker` (else "local");
planner `self.planner` {provider, model} (else the `code` row's first
target); reviewer and chores `self.local` (else the first `local` provider).
"off" is allowed for reviewer and chores.

A pick is recomputed from the settings at every use: when the target can't
run, the role falls back to its default and says why; when that can't run
either, the pick has no provider. Timings of the calls made for a role live
in the role_calls table.

This module reads routing.json itself and never imports eki.routing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import paths, providers, targets

ROLES = ("router", "planner", "reviewer", "chores")
#: roles that may be "off"
OFFABLE = ("reviewer", "chores")
#: roles that only need text back: their provider must have complete()
TEXT_ROLES = ("router", "reviewer", "chores")
#: kinds whose provider answers a one-shot text call
TEXT_KINDS = ("local", "claude_code", "codex")
#: the code row's first target when routing.json has no rows (eki/routing/table.py)
CODE_FIRST = "claude"


@dataclass
class Pick:
    provider: Optional[str]
    model: Optional[str]
    target: str
    why: str


def _settings() -> Dict[str, Any]:
    try:
        got = json.loads(paths.config("routing").read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _known(role: str) -> None:
    if role not in ROLES:
        raise KeyError(f"no role named {role!r}; the roles are {', '.join(ROLES)}")


def _code_first(data: Dict[str, Any]) -> str:
    """The `code` row's first target (else `general`'s), as table.row() picks it."""
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    for key in ("code", "general"):
        for r in rows:
            if r.get("key") == key and r.get("targets"):
                return str(r["targets"][0])
    return CODE_FIRST


def default(role: str) -> str:
    """What the role reads when routing.json has no `roles` entry for it ("" for none)."""
    _known(role)
    data = _settings()
    own = data.get("self") if isinstance(data.get("self"), dict) else {}
    if role == "router":
        return str(data.get("checker") or "local")
    if role == "planner":
        want = own.get("planner") if isinstance(own.get("planner"), dict) else {}
        provider = str(want.get("provider") or "").strip()
        model = str(want.get("model") or "").strip()
        if not provider:
            return _code_first(data)
        return f"{provider}:{model}" if model else provider
    chosen = str(own.get("local") or "").strip()
    if chosen:
        return chosen
    return next((n for n, c in providers.config().items() if c.get("kind") == "local"), "")


def get(role: str) -> str:
    _known(role)
    set_ = _settings().get("roles")
    got = str((set_ or {}).get(role) or "").strip() if isinstance(set_, dict) else ""
    return got or default(role)


def set_role(role: str, target: Optional[str]) -> None:
    """Write `roles[role]` into routing.json, keeping every other key;
    "default" or None removes the entry."""
    _known(role)
    path = paths.config("routing")
    data = _settings()
    roles = dict(data.get("roles") or {}) if isinstance(data.get("roles"), dict) else {}
    if target is None or str(target).strip() in ("", "default"):
        roles.pop(role, None)
    else:
        roles[role] = str(target).strip()
    if roles:
        data["roles"] = roles
    else:
        data.pop("roles", None)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _listed(conn: Optional[sqlite3.Connection], name: str) -> Optional[List[str]]:
    """The provider's read model list, or None when it was never read."""
    own = conn is None
    try:
        if own:
            from . import db
            conn = db.connect()
        got = [r[0] for r in conn.execute("SELECT model FROM provider_models WHERE provider=?", (name,))]
    except sqlite3.Error:
        return None
    finally:
        if own and conn is not None:
            conn.close()
    return got or None


def _cannot(role: str, target: str, status: Optional[Dict[str, Tuple[bool, str]]]) -> Optional[str]:
    """Why `target` can't do `role` right now, or None when it can."""
    if not target:
        return "nothing set"
    try:
        name = targets.parse(target).provider
    except ValueError as e:
        return str(e)
    cfg = providers.config().get(name)
    if cfg is None:
        return f"{name}: no such provider"
    if cfg.get("off"):
        return f"{name}: switched off"
    kind = str(cfg.get("kind", ""))
    if role in TEXT_ROLES:
        cls = providers.KINDS.get(kind)
        if kind not in TEXT_KINDS and not (cls is not None and hasattr(cls, "complete")):
            return f"{name}: can't answer a one-shot text call"
    if role == "planner" and "tools" not in providers.capabilities(name, cfg):
        return f"{name}: has no tools"
    if status is not None:
        ok, why = status.get(name, (False, "not available"))
        if not ok:
            return f"{name}: {why}"
    return None


def pick(role: str, conn: Optional[sqlite3.Connection] = None, *, background: bool = False) -> Pick:
    """Who does `role` now: its target, else its default, else nobody, and why."""
    _known(role)
    want, fallback = get(role), default(role)
    if want == "off" and role in OFFABLE:
        return Pick(None, None, "off", "off")
    status = None
    if conn is not None:
        from . import capacity        # capacity reads quota and models: import it at use
        status = capacity.status(conn, background=background)
    why = ""
    target = want
    cause = _cannot(role, want, status)
    if cause is not None:
        if fallback == want or (fallback == "off" and role in OFFABLE):
            last = "nobody" if fallback == want else "off"
            return Pick(None, None, want, f"{role} {want or '(none)'} can't run ({cause}) → {last}")
        why = f"{role} {want} can't run ({cause}) → {fallback or '(none)'}"
        target = fallback
        again = _cannot(role, fallback, status)
        if again is not None:
            return Pick(None, None, fallback, f"{why}, which can't run either ({again})")
    name = targets.parse(target).provider
    model, note = targets.resolve(target, providers.config().get(name) or {}, _listed(conn, name))
    if note:
        why = f"{why}; {note}" if why else note
    return Pick(name, model, target, why)


def problems(data: Any, known: set) -> List[str]:
    """What's wrong with a routing.json `roles` dict ([] when nothing)."""
    if data is None:
        return []
    if not isinstance(data, dict):
        return ["roles must be an object of role → target"]
    out: List[str] = []
    for role, target in data.items():
        if role not in ROLES:
            out.append(f"roles: unknown role {role!r} (the roles are {', '.join(ROLES)})")
        elif target == "off":
            if role not in OFFABLE:
                out.append(f"roles: {role} can't be off")
        else:
            out += [f"roles.{role}: {p}" for p in targets.problems(target, known)]
    return out


def record(conn: sqlite3.Connection, role: str, target: str, model: Optional[str],
           ms: float, ok: bool) -> None:
    conn.execute("INSERT INTO role_calls (role, target, model, ms, ok, at) VALUES (?,?,?,?,?,?)",
                 (role, target, model, float(ms), 1 if ok else 0, time.time()))


def timings(conn: sqlite3.Connection, role: str, n: int = 20) -> Dict[str, Tuple[float, int]]:
    """{target: (median ms, count)} over each target's last `n` calls for `role`."""
    out: Dict[str, Tuple[float, int]] = {}
    names = [r[0] for r in conn.execute(
        "SELECT target FROM role_calls WHERE role=? GROUP BY target ORDER BY MAX(id) DESC", (role,))]
    for target in names:
        ms = [r[0] for r in conn.execute(
            "SELECT ms FROM role_calls WHERE role=? AND target=? AND ms IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (role, target, n))]
        if ms:
            out[target] = (float(statistics.median(ms)), len(ms))
    return out
