# SPDX-License-Identifier: Apache-2.0
"""Routing preferences you can change without editing config.

config.yaml describes what exists; this describes what you currently want.
Keeping them apart means the app can turn a backend off for the afternoon
without rewriting a file full of comments — and turning it back on is one
click rather than an undo.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Policy:
    #: never chosen automatically (still reachable by name)
    disabled: List[str] = field(default_factory=list)
    #: cost tier overrides, key -> tier
    tiers: Dict[str, int] = field(default_factory=dict)
    #: preferred order among equal tiers; anything unlisted sorts after
    order: List[str] = field(default_factory=list)
    #: a quota window at or above this counts as spent (None = use config)
    quota_ceiling: Optional[float] = None

    def tier_for(self, key: str, declared: int) -> int:
        return int(self.tiers.get(key, declared))

    def rank(self, key: str) -> int:
        return self.order.index(key) if key in self.order else len(self.order)

    def is_disabled(self, key: str) -> bool:
        return key in self.disabled

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def default_path() -> Path:
    return Path(os.path.expanduser(os.environ.get("EKI_POLICY", "~/.eki/policy.json")))


def load(path: Optional[Path] = None) -> Policy:
    p = path or default_path()
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return Policy()                    # absent or unreadable: no preferences
    ceiling = raw.get("quota_ceiling")
    return Policy(
        disabled=[str(k) for k in raw.get("disabled") or []],
        tiers={str(k): int(v) for k, v in (raw.get("tiers") or {}).items()},
        order=[str(k) for k in raw.get("order") or []],
        quota_ceiling=float(ceiling) if ceiling is not None else None,
    )


def save(policy: Policy, path: Optional[Path] = None) -> Path:
    p = path or default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy.to_json(), indent=2))
    tmp.replace(p)                         # atomic: never leave a half-written policy
    return p
