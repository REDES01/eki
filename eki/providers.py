# SPDX-License-Identifier: Apache-2.0
"""Providers: everything eki can send work to, stored in the database.

A provider is one record that serves as a routing backend, a quota source
if it has one, and, for a local server, a runtime eki can start and stop.
v1 kept these in `config.yaml`; now the app adds, tests and removes them,
and the YAML is only a seed for a fresh install.

Secrets never touch this table. A provider that needs an API key has
`options.secret = true`, and the key itself lives in the Keychain.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adapters.base import BackendInfo, Capabilities, Cost

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    key          TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    label        TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    tier         INTEGER NOT NULL DEFAULT 50,
    note         TEXT NOT NULL DEFAULT '',
    quota_source TEXT,
    capabilities TEXT NOT NULL DEFAULT '{}',
    options      TEXT NOT NULL DEFAULT '{}',
    runtime      TEXT NOT NULL DEFAULT '{}',
    position     INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
"""

CAPABILITY_FIELDS = ("context_tokens", "text", "vision", "tools", "repo",
                     "images_out", "web", "streaming", "produces", "needs")


@dataclass
class Provider:
    key: str
    kind: str
    label: str
    enabled: bool = True
    tier: int = 50
    note: str = ""
    quota_source: Optional[str] = None
    capabilities: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    #: a local server eki manages: {port, start, stop, gb, idle_minutes, kind}
    runtime: Dict[str, Any] = field(default_factory=dict)
    position: int = 0

    def info(self) -> BackendInfo:
        caps = {k: v for k, v in self.capabilities.items() if k in CAPABILITY_FIELDS}
        # what a kind has by nature, whatever the row says: the CLIs bring
        # their own hosted search (Codex's once eki turns it on), and a
        # search server from eki's registry gives it to whichever side has it
        if self.kind in ("claude_code", "codex") and "web" not in self.capabilities:
            from . import mcpregistry, settings as settings_mod
            side = "claude" if self.kind == "claude_code" else "codex"
            hosted = self.kind == "claude_code" or bool(settings_mod.load().get("codex_web_search", True))
            caps["web"] = hosted or mcpregistry.provides(side, "web")
        return BackendInfo(key=self.key, kind=self.kind, label=self.label,
                           capabilities=Capabilities(**caps),
                           cost=Cost(tier=self.tier, note=self.note),
                           quota_source=self.quota_source)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def from_json(data: Dict[str, Any]) -> Provider:
    known = {f for f in Provider.__dataclass_fields__}
    clean = {k: v for k, v in data.items() if k in known}
    if not clean.get("key") or not clean.get("kind"):
        raise ValueError("a provider needs a key and a kind")
    clean.setdefault("label", clean["key"])
    return Provider(**clean)


class ProviderStore:
    def __init__(self, path: Path | str):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def all(self) -> List[Provider]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM providers ORDER BY position, created_at").fetchall()
        return [self._row(r) for r in rows]

    def get(self, key: str) -> Optional[Provider]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM providers WHERE key = ?",
                                     (key,)).fetchone()
        return self._row(row) if row else None

    def empty(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0] == 0

    def upsert(self, provider: Provider) -> Provider:
        now = int(time.time())
        with self._lock:
            if provider.position == 0:
                top = self._conn.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM providers").fetchone()[0]
                existing = self._conn.execute(
                    "SELECT position FROM providers WHERE key = ?",
                    (provider.key,)).fetchone()
                provider.position = existing[0] if existing else top + 1
            self._conn.execute(
                "INSERT INTO providers (key, kind, label, enabled, tier, note, quota_source,"
                " capabilities, options, runtime, position, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, label=excluded.label,"
                " enabled=excluded.enabled, tier=excluded.tier, note=excluded.note,"
                " quota_source=excluded.quota_source, capabilities=excluded.capabilities,"
                " options=excluded.options, runtime=excluded.runtime,"
                " position=excluded.position, updated_at=excluded.updated_at",
                (provider.key, provider.kind, provider.label, int(provider.enabled),
                 provider.tier, provider.note, provider.quota_source,
                 json.dumps(provider.capabilities), json.dumps(_no_secrets(provider.options)),
                 json.dumps(provider.runtime), provider.position, now, now))
            self._conn.commit()
        return provider

    def delete(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM providers WHERE key = ?", (key,))
            self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> Provider:
        return Provider(
            key=row["key"], kind=row["kind"], label=row["label"],
            enabled=bool(row["enabled"]), tier=row["tier"], note=row["note"],
            quota_source=row["quota_source"],
            capabilities=json.loads(row["capabilities"] or "{}"),
            options=json.loads(row["options"] or "{}"),
            runtime=json.loads(row["runtime"] or "{}"),
            position=row["position"])


def _no_secrets(options: Dict[str, Any]) -> Dict[str, Any]:
    """Belt and braces: an API key must never land in SQLite, even by mistake."""
    return {k: v for k, v in options.items()
            if k not in ("api_key", "token", "secret_value", "password")}


def seed_from_config(cfg) -> List[Provider]:
    """v1's config.yaml, as provider records: backends plus their runtimes."""
    runtimes = {m.backend or m.key: m for m in getattr(cfg, "local_models", [])}
    out: List[Provider] = []
    for i, info in enumerate(cfg.backends, start=1):
        caps = {name: getattr(info.capabilities, name) for name in CAPABILITY_FIELDS}
        runtime: Dict[str, Any] = {}
        model = runtimes.get(info.key)
        if model:
            runtime = {"port": model.port, "start": model.start, "stop": model.stop,
                       "gb": model.gb, "note": model.note, "kind": model.kind,
                       "label": model.label}
        out.append(Provider(key=info.key, kind=info.kind, label=info.label,
                            tier=info.cost.tier, note=info.cost.note,
                            quota_source=info.quota_source, capabilities=caps,
                            options=dict(cfg.options.get(info.key, {})),
                            runtime=runtime, position=i))
    return out
