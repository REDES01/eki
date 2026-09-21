# SPDX-License-Identifier: Apache-2.0
"""Every model eki can reach, and what each is actually good at.

A provider is a door; behind it are models — Claude Code can run fable, opus
or sonnet, Codex has its own list, a local server has the one it loaded.
Routing that stops at the door leaves the real choice unmade, so this keeps
one record per (provider, model): where it came from, its context, what it
costs, how fast it runs, and a score per kind of work.

Scores start as priors by class (a 27B open model, a frontier agent) and are
replaced by measurement: eki can run each model through a small battery of
tasks with checkable answers and keep what it scored. Later, outcomes from
real use — redos, overrides — adjust the same numbers. Nothing here guesses
silently: every score says whether it's a prior or a measurement.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import priors
from .classify import TASKS

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,              -- '' = the provider's default
    label          TEXT NOT NULL DEFAULT '',
    enabled        INTEGER NOT NULL DEFAULT 1,
    context_tokens INTEGER NOT NULL DEFAULT 0,
    price_in       REAL,                       -- $ per million input tokens, if metered
    price_out      REAL,
    speed_tok_s    REAL,
    class          TEXT NOT NULL DEFAULT '',
    measured       TEXT NOT NULL DEFAULT '{}', -- {task: {score, n, at}}
    source         TEXT NOT NULL DEFAULT '',   -- listed | seeded | user
    updated_at     INTEGER NOT NULL,
    PRIMARY KEY (provider, model)
);
"""

#: below this many measured items a score is still the prior
MIN_ITEMS = 3


@dataclass
class ModelRecord:
    provider: str
    model: str
    label: str = ""
    enabled: bool = True
    context_tokens: int = 0
    price_in: Optional[float] = None
    price_out: Optional[float] = None
    speed_tok_s: Optional[float] = None
    klass: str = ""
    measured: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    def quality(self, task: str, difficulty: str = "") -> float:
        """Measured when there's enough of it at this difficulty, the class's
        prior otherwise. A score from easy items says nothing about hard
        work, so it never stands in for one."""
        got = self.measured.get(f"{task}/{difficulty}") if difficulty else None
        if got and got.get("n", 0) >= MIN_ITEMS:
            return float(got["score"])
        return priors.QUALITY.get(self.klass, {}).get(task, 0.0)

    def basis(self, task: str, difficulty: str = "") -> str:
        got = self.measured.get(f"{task}/{difficulty}") if difficulty else None
        return "measured" if got and got.get("n", 0) >= MIN_ITEMS else "prior"

    def to_json(self) -> Dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model, "label": self.label,
            "enabled": self.enabled, "context_tokens": self.context_tokens,
            "price_in": self.price_in, "price_out": self.price_out,
            "speed_tok_s": self.speed_tok_s, "class": self.klass, "source": self.source,
            "scores": {t: {
                "prior": round(priors.QUALITY.get(self.klass, {}).get(t, 0.0), 2),
                "measured": {d: {"score": round(m["score"], 2), "n": m["n"]}
                             for d in ("easy", "medium", "hard")
                             if (m := self.measured.get(f"{t}/{d}"))},
            } for t in TASKS},
        }


class Registry:
    def __init__(self, path: Path | str):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---- reading ---------------------------------------------------------

    def all(self) -> List[ModelRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM models ORDER BY provider, model").fetchall()
        return [self._row(r) for r in rows]

    def for_provider(self, provider: str, enabled_only: bool = True) -> List[ModelRecord]:
        return [m for m in self.all()
                if m.provider == provider and (m.enabled or not enabled_only)]

    def get(self, provider: str, model: str = "") -> Optional[ModelRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM models WHERE provider = ? AND model = ?",
                (provider, model)).fetchone()
        return self._row(row) if row else None

    # ---- writing ---------------------------------------------------------

    def upsert(self, rec: ModelRecord) -> ModelRecord:
        with self._lock:
            self._conn.execute(
                "INSERT INTO models (provider, model, label, enabled, context_tokens,"
                " price_in, price_out, speed_tok_s, class, measured, source, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(provider, model) DO UPDATE SET label=excluded.label,"
                " enabled=excluded.enabled, context_tokens=excluded.context_tokens,"
                " price_in=excluded.price_in, price_out=excluded.price_out,"
                " speed_tok_s=excluded.speed_tok_s, class=excluded.class,"
                " measured=excluded.measured, source=excluded.source,"
                " updated_at=excluded.updated_at",
                (rec.provider, rec.model, rec.label, int(rec.enabled), rec.context_tokens,
                 rec.price_in, rec.price_out, rec.speed_tok_s, rec.klass,
                 json.dumps(rec.measured), rec.source, int(time.time())))
            self._conn.commit()
        return rec

    def seen(self, provider: str, model: str, label: str = "", context_tokens: int = 0,
             klass: str = "", source: str = "listed") -> ModelRecord:
        """A model a provider listed: recorded once, and what was measured
        or set by hand about it is kept across listings."""
        have = self.get(provider, model)
        if have is not None:
            changed = False
            if label and have.label != label:
                have.label, changed = label, True
            if context_tokens and have.context_tokens != context_tokens:
                have.context_tokens, changed = context_tokens, True
            if klass and not have.klass:
                have.klass, changed = klass, True
            return self.upsert(have) if changed else have
        return self.upsert(ModelRecord(provider=provider, model=model, label=label or model,
                                       context_tokens=context_tokens, klass=klass,
                                       source=source))

    def record(self, provider: str, model: str, task: str, score: float, items: int,
               speed_tok_s: Optional[float] = None, difficulty: str = "easy"
               ) -> Optional[ModelRecord]:
        """Keep a measurement. Items add up across runs; the score is the
        item-weighted mean, so a second run refines rather than replaces."""
        rec = self.get(provider, model)
        if rec is None:
            return None
        slot = f"{task}/{difficulty}"
        old = rec.measured.get(slot) or {"score": 0.0, "n": 0}
        n = old["n"] + items
        blended = (old["score"] * old["n"] + score * items) / n if n else score
        rec.measured[slot] = {"score": round(blended, 3), "n": n, "at": int(time.time())}
        if speed_tok_s:
            rec.speed_tok_s = round(speed_tok_s, 1)
        return self.upsert(rec)

    def set_enabled(self, provider: str, model: str, enabled: bool) -> bool:
        rec = self.get(provider, model)
        if rec is None:
            return False
        rec.enabled = enabled
        self.upsert(rec)
        return True

    def forget_measurements(self, provider: str, model: str) -> bool:
        rec = self.get(provider, model)
        if rec is None:
            return False
        rec.measured = {}
        self.upsert(rec)
        return True

    def remove_provider(self, provider: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM models WHERE provider = ?", (provider,))
            self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> ModelRecord:
        return ModelRecord(
            provider=row["provider"], model=row["model"], label=row["label"],
            enabled=bool(row["enabled"]), context_tokens=row["context_tokens"],
            price_in=row["price_in"], price_out=row["price_out"],
            speed_tok_s=row["speed_tok_s"], klass=row["class"],
            measured=json.loads(row["measured"] or "{}"), source=row["source"])
