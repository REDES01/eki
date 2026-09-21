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
from typing import Any, Dict, List, Optional, Tuple

from . import priors
from . import public_scores
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
    measured       TEXT NOT NULL DEFAULT '{}', -- {task/difficulty: {score, n, at}}
    source         TEXT NOT NULL DEFAULT '',   -- listed | seeded | user
    updated_at     INTEGER NOT NULL,
    PRIMARY KEY (provider, model)
);
"""
#: relative cost of running a model, within and across providers, when no
#: price is known: what a request on it "spends" compared to the fast tier
#: the top tier at 5× the fast tier follows the vendors' own API price ratio
#: (Opus to Sonnet); a per-model cost_weight overrides it
COST_WEIGHT = {"small_open": 0.05, "mid_open": 0.1, "large_open": 0.2, "image": 0.2,
               "mid_agent": 0.15, "large_agent": 0.3,
               "frontier_agent_fast": 1.0, "frontier_api": 3.0, "frontier_agent": 5.0}
#: a flagship that is metered on its own window (Claude Code shows "Current
#: week (Fable)") costs more than the tier's other models. Used only when no
#: public price is known; per-model cost_weight overrides.
COST_NAMES = {"fable": 10.0}
#: public API prices become a cost weight on the same scale: the blended
#: $/M tokens (a quarter in, three quarters out) over this anchor, chosen so
#: that Opus lands on the frontier_agent weight of 5
PRICE_ANCHOR = 4.0

#: below this many measured items a score is still the prior
MIN_ITEMS = 3
#: from this many, eki's own measurement outranks the public boards: the
#: boards ran thousands of items, but on a different harness and a different
#: build; a few dozen on this one say more about what will happen here
SOLID_ITEMS = 10


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
    #: what the public boards say: {"base", "name", "date", "scores": {task/difficulty}}
    public: Dict[str, Any] = field(default_factory=dict)
    #: relative cost; None means "by class"
    cost_weight: Optional[float] = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    def quality(self, task: str, difficulty: str = "") -> float:
        """In order of how much it knows: eki's own measurement at this
        difficulty, what the public boards report, the class's prior.

        A score from easy items says nothing about hard work, so it never
        stands in for one; a public score at a harder level can."""
        return self._quality(task, difficulty)[0]

    def basis(self, task: str, difficulty: str = "") -> str:
        return self._quality(task, difficulty)[1]

    def _quality(self, task: str, difficulty: str = "") -> Tuple[float, str]:
        got = self.measured.get(f"{task}/{difficulty}") if difficulty else None
        if got and got.get("n", 0) >= SOLID_ITEMS:
            return float(got["score"]), "measured"
        if self.public.get("scores") and difficulty:
            found = public_scores.nearest(self.public["scores"], task, difficulty)
            if found is not None:
                return float(found), "public"
        if got and got.get("n", 0) >= MIN_ITEMS:
            return float(got["score"]), "measured"
        return priors.QUALITY.get(self.klass, {}).get(task, 0.0), "prior"

    @property
    def cost(self) -> float:
        if self.cost_weight is not None:
            return self.cost_weight
        price = (self.public or {}).get("price") or {}
        if price.get("out") and self.klass.startswith("frontier"):
            # a local build's price is memory and time, not dollars
            blended = 0.25 * float(price.get("in", 0.0)) + 0.75 * float(price["out"])
            return round(max(0.2, blended / PRICE_ANCHOR), 2)
        name = (self.model or (self.public or {}).get("base") or "").lower()
        for token, weight in COST_NAMES.items():
            if token in name:
                return weight
        return COST_WEIGHT.get(self.klass, 1.0)

    def to_json(self) -> Dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model, "label": self.label,
            "enabled": self.enabled, "context_tokens": self.context_tokens,
            "price_in": self.price_in, "price_out": self.price_out,
            "speed_tok_s": self.speed_tok_s, "class": self.klass, "source": self.source,
            "cost": self.cost,
            "public": {k: self.public.get(k, "") for k in ("base", "name", "date")}
            if self.public else None,
            "scores": {t: {
                "prior": round(priors.QUALITY.get(self.klass, {}).get(t, 0.0), 2),
                "public": {d: round(v, 2) for d in ("easy", "medium", "hard")
                           if (v := public_scores.nearest(self.public.get("scores", {}), t, d))
                           is not None} if self.public.get("scores") else {},
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
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(models)")}
        if "public" not in cols:
            self._conn.execute("ALTER TABLE models ADD COLUMN public TEXT NOT NULL DEFAULT '{}'")
        if "cost_weight" not in cols:
            self._conn.execute("ALTER TABLE models ADD COLUMN cost_weight REAL")
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
                " price_in, price_out, speed_tok_s, class, measured, source, updated_at,"
                " public, cost_weight)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(provider, model) DO UPDATE SET label=excluded.label,"
                " enabled=excluded.enabled, context_tokens=excluded.context_tokens,"
                " price_in=excluded.price_in, price_out=excluded.price_out,"
                " speed_tok_s=excluded.speed_tok_s, class=excluded.class,"
                " measured=excluded.measured, source=excluded.source,"
                " updated_at=excluded.updated_at, public=excluded.public,"
                " cost_weight=excluded.cost_weight",
                (rec.provider, rec.model, rec.label, int(rec.enabled), rec.context_tokens,
                 rec.price_in, rec.price_out, rec.speed_tok_s, rec.klass,
                 json.dumps(rec.measured), rec.source, int(time.time()),
                 json.dumps(rec.public), rec.cost_weight))
            self._conn.commit()
        return rec

    def seen(self, provider: str, model: str, label: str = "", context_tokens: int = 0,
             klass: str = "", source: str = "listed",
             public: Optional[Dict[str, Any]] = None) -> ModelRecord:
        """A model a provider listed: recorded once, and what was measured
        or set by hand about it is kept across listings."""
        have = self.get(provider, model)
        if have is not None:
            changed = False
            if label and have.label != label:
                have.label, changed = label, True
            if context_tokens and have.context_tokens != context_tokens:
                have.context_tokens, changed = context_tokens, True
            if klass and have.klass != klass:           # derived, never user-set
                have.klass, changed = klass, True
            if public is not None and public != have.public:
                have.public, changed = public, True
            return self.upsert(have) if changed else have
        return self.upsert(ModelRecord(provider=provider, model=model, label=label or model,
                                       context_tokens=context_tokens, klass=klass,
                                       source=source, public=public or {}))

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

    def record_speed(self, provider: str, model: str, tok_s: float) -> None:
        rec = self.get(provider, model)
        if rec is not None and tok_s:
            rec.speed_tok_s = round(tok_s, 1)
            self.upsert(rec)

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

    def remove(self, provider: str, model: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM models WHERE provider = ? AND model = ?",
                                     (provider, model))
            self._conn.commit()
        return cur.rowcount > 0

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
            measured=json.loads(row["measured"] or "{}"), source=row["source"],
            public=json.loads(row["public"] or "{}") if "public" in row.keys() else {},
            cost_weight=row["cost_weight"] if "cost_weight" in row.keys() else None)
