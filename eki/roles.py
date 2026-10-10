"""Roles: eki's own jobs, and which target does each.

    router     the prompt check and narrow (eki/routing/check.py)
    planner    draft and plan runs (eki/selfdraft.py)
    reviewer   the review chore (eki/review.py)
    chores     digest prose, fault briefs, PR-comment triage

The setting is routing.json `"roles": {"router": "claude:fast", ...}`,
holding only the roles you set; a target with an effort is an object,
`{"provider": "claude", "model": "opus", "effort": "high"}` (eki/targets.py). With no entry a role reads the old settings,
which are never rewritten: router the top-level `checker` (else "local");
planner `self.planner` {provider, model} (else the `code` row's first
target); reviewer and chores `self.local` (else the first `local` provider).
"off" is allowed for reviewer and chores.

A pick is recomputed from the settings at every use, and its model and
effort are checked against the read model list (targets.choose()): when the
target can't run, the role falls back to its default and says why; when that can't run
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
from typing import Any, Dict, List, Optional, Tuple, Union

from . import modellist, paths, providers, targets

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
    target: Union[str, Dict[str, str]]   # as written: text, or an object with an effort
    why: str
    effort: Optional[str] = None          # None: the model's own default


def _settings() -> Dict[str, Any]:
    try:
        got = json.loads(paths.config("routing").read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _known(role: str) -> None:
    if role not in ROLES:
        raise KeyError(f"no role named {role!r}; the roles are {', '.join(ROLES)}")


def _code_first(data: Dict[str, Any]) -> Union[str, Dict[str, str]]:
    """The `code` row's first target (else `general`'s), as table.row() picks it;
    an object target is kept as it is."""
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    for key in ("code", "general"):
        for r in rows:
            if r.get("key") == key and r.get("targets"):
                first = r["targets"][0]
                return first if isinstance(first, dict) else str(first)
    return CODE_FIRST


def default(role: str) -> Union[str, Dict[str, str]]:
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
        if not provider:                 # a model alone goes with the code row's first choice
            first = _code_first(data)
            return f"{targets.provider_of(first)}:{model}" if model else first
        return f"{provider}:{model}" if model else provider
    chosen = str(own.get("local") or "").strip()
    if chosen:
        return chosen
    return next((n for n, c in providers.config().items() if c.get("kind") == "local"), "")


def get(role: str) -> Union[str, Dict[str, str]]:
    """The role's stored target as written (text, or an object), else its default."""
    _known(role)
    set_ = _settings().get("roles")
    got = (set_ or {}).get(role) if isinstance(set_, dict) else None
    if isinstance(got, dict) and got:
        return got
    got = str(got or "").strip()
    return got or default(role)


def shown(target: Any) -> str:
    """A target as one line: "off", or targets.key() (`claude:opus@high`)."""
    return target if target in ("", "off") else targets.key(target)


def set_role(role: str, target: Any) -> None:
    """Write `roles[role]` into routing.json as targets.fmt()'s shortest form,
    keeping every other key; "default" or None removes the entry."""
    _known(role)
    path = paths.config("routing")
    data = _settings()
    roles = dict(data.get("roles") or {}) if isinstance(data.get("roles"), dict) else {}
    if target is None or (isinstance(target, str) and target.strip() in ("", "default")):
        roles.pop(role, None)
    elif isinstance(target, str) and target.strip() == "off":
        roles[role] = "off"
    else:
        roles[role] = targets.fmt(targets.parse(target.strip() if isinstance(target, str) else target))
    if roles:
        data["roles"] = roles
    else:
        data.pop("roles", None)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _listed(conn: Optional[sqlite3.Connection], name: str) -> Optional[List[Dict[str, Any]]]:
    """The provider's read model list (modellist.listed()), or None when it was never read."""
    own = conn is None
    try:
        if own:
            from . import db
            conn = db.connect()
        return modellist.listed(conn, name)
    except sqlite3.Error:
        return None
    finally:
        if own and conn is not None:
            conn.close()


def _cannot(role: str, target: Any, status: Optional[Dict[str, Tuple[bool, str]]]) -> Optional[str]:
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
    if not want:                         # no setting and no local model to default to
        return Pick(None, None, "", "no local model" if role in OFFABLE else f"no {role} set")
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
            return Pick(None, None, want, f"{role} {shown(want) or '(none)'} can't run ({cause}) → {last}")
        why = f"{role} {shown(want)} can't run ({cause}) → {shown(fallback) or '(none)'}"
        target = fallback
        again = _cannot(role, fallback, status)
        if again is not None:
            return Pick(None, None, fallback, f"{why}, which can't run either ({again})")
    name = targets.parse(target).provider
    c = targets.choose(target, providers.config().get(name) or {}, _listed(conn, name))
    if c.note:
        why = f"{why}; {c.note}" if why else c.note
    return Pick(name, c.model, target, why, c.effort)


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


def record(conn: sqlite3.Connection, role: str, target: Any, model: Optional[str],
           ms: float, ok: bool) -> None:
    """One call made for `role`, keyed by targets.key(target)."""
    conn.execute("INSERT INTO role_calls (role, target, model, ms, ok, at) VALUES (?,?,?,?,?,?)",
                 (role, targets.key(target), model, float(ms), 1 if ok else 0, time.time()))


def timings(conn: sqlite3.Connection, role: str, n: int = 20) -> Dict[str, Tuple[float, int]]:
    """{targets.key(target): (median ms, count)} over each target's last `n` calls for `role`."""
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
