# SPDX-License-Identifier: Apache-2.0
"""Pick a backend for a request, and say why.

Filters in order: your policy, whether it is actually running, hard
requirements, live quota, then cost and declared order.
Every decision carries its reasoning — automatic selection you can't inspect is
worse than none, and the first question of any surprising answer is "which
model was that?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import priors
from .adapters.base import Backend
from .policy import Policy


@dataclass
class Need:
    """What a request demands of a backend."""

    repo: bool = False           # must be able to edit files where it runs
    tools: bool = False
    vision: bool = False
    images_out: bool = False
    web: bool = False            # needs to look things up on the web
    context_tokens: int = 0
    backend: Optional[str] = None       # explicit override, wins over everything
    #: what the request is and how demanding it is, from eki.classify
    task: str = ""
    difficulty: str = ""
    #: the answer before this one was corrected, or failed: go up the ladder
    escalate: bool = False
    #: the routing table's row for this request, and its choices in order
    #: (eki/table.py): the first that can take it gets it
    row: str = ""
    row_title: str = ""
    targets: List[str] = field(default_factory=list)
    #: only these may take it (a goal's budget); None: any
    allowed: Optional[List[str]] = None


@dataclass
class Choice:
    backend: Optional[Backend]
    reason: str
    rejected: List[str] = field(default_factory=list)
    #: which of the provider's models, "" for its default
    model: str = ""

    @property
    def label(self) -> str:
        if self.backend is None:
            return ""
        return f"{self.backend.key} ({self.model})" if self.model else self.backend.key


class QuotaSource:
    """What the router needs from quota: which providers have a spent window.

    The engine's QuotaBoard is the real one. A backend with no headroom is
    skipped rather than attempted and failed — the point of tracking quota is
    to stop discovering it the hard way. Unknown quota is never held against
    a backend.
    """

    def __init__(self, ceiling: float = 0.99):
        self.ceiling = ceiling

    def exhausted(self) -> Dict[str, str]:
        """{provider key: why}."""
        return {}

    def pace(self) -> Dict[str, Any]:
        """{provider key: quota.pace.ProviderPace}: how fast each window is
        being spent, as a factor on the provider's cost. Empty: all on pace."""
        return {}


class Router:
    def __init__(self, backends: List[Backend], quota: Optional[QuotaSource] = None,
                 policy: Optional["Policy"] = None,
                 is_up: Optional[Callable[[str], Optional[bool]]] = None,
                 reserved: Optional[set] = None,
                 models_for: Optional[Callable[[str], List[Any]]] = None,
                 ladder_for: Optional[Callable[[str], Dict[str, str]]] = None,
                 with_tools: Optional[Callable[[], Dict[str, str]]] = None):
        self.backends = backends
        self.quota = quota
        #: current preferences; replaced wholesale when the user edits them
        self.policy = policy or Policy()
        #: True / False / None for "don't know", which is never held against it
        self.is_up = is_up or (lambda key: None)
        #: backends kept out of automatic routing because they have another
        #: job — the router model itself. Still available by name.
        self.reserved = reserved or set()
        #: the registry's records for a provider's models: each has .model
        #: and .quality(task). None or empty means "just the default".
        self.models_for = models_for or (lambda key: [])
        #: the vendor's own guidance for a program, read daily (eki/watch.py):
        #: {"default", "top", "fast": model id, "vendor"}; {} = not read yet
        self.ladder_for = ladder_for or (lambda key: {})
        #: {a local model: the same model with Codex's hands} — tools decide
        #: which of the two a row's local choice means for this request
        self.with_tools = with_tools or (lambda: {})

    def _quality(self, backend: Backend, task: str) -> float:
        return priors.quality(backend.info.kind, getattr(backend, "options", {}) or {},
                              task, backend.info.capabilities)

    def choose(self, need: Need) -> Choice:
        rejected: List[str] = []

        if need.backend:
            for b in self.backends:
                if b.key == need.backend:
                    return Choice(b, f"{b.key}: asked for by name")
            return Choice(None, f"no backend named {need.backend!r}")

        spent = self.quota.exhausted() if self.quota else {}
        paces = self.quota.pace() if self.quota else {}
        candidates: List[Backend] = []

        for b in self.backends:
            if b.key in self.reserved:
                rejected.append(f"{b.key}: reserved as the router model")
                continue
            if need.allowed is not None and b.key not in need.allowed:
                rejected.append(f"{b.key}: outside this goal's budget")
                continue
            if self.policy.is_disabled(b.key):
                rejected.append(f"{b.key}: turned off in policy")
                continue
            if self.is_up(b.key) is False:
                # a server that isn't listening is not a candidate; routing to
                # it produces a failed request instead of a different answer
                rejected.append(f"{b.key}: not running")
                continue
            caps = b.info.capabilities
            if not need.images_out and not caps.text:
                # an image model asked a question draws a picture of it; that
                # is a wrong answer, not a cheap one
                rejected.append(f"{b.key}: draws images, doesn't answer in words")
                continue
            missing = [
                name for name, wanted, have in (
                    ("repo", need.repo, caps.repo),
                    ("tools", need.tools, caps.tools),
                    ("vision", need.vision, caps.vision),
                    ("web", need.web, caps.web),
                    ("images", need.images_out, caps.images_out),
                ) if wanted and not have
            ]
            if missing:
                rejected.append(f"{b.key}: lacks {', '.join(missing)}")
                continue
            if need.context_tokens and caps.context_tokens < need.context_tokens:
                rejected.append(
                    f"{b.key}: context {caps.context_tokens} < {need.context_tokens}")
                continue
            source = b.info.quota_source
            if source and source in spent:
                rejected.append(f"{b.key}: {spent[source]}")
                continue
            candidates.append(b)

        if not candidates:
            return Choice(None, "no backend can serve this request", rejected)

        if need.targets:
            picked = self._from_row(need, candidates, rejected, paces)
            if picked is not None:
                return picked
            note_row = f"none of {need.row_title or need.row}'s choices could take it; "
        else:
            note_row = ""

        # Cheap is not the same as good enough. A labelled request keeps only
        # the backends with a model believed to handle that kind of work at
        # that difficulty; if none clears the bar, the best available wins
        # instead of the cheapest. Within a provider, the model chosen is the
        # cheapest adequate one: the default if it clears the bar, else the
        # least of those that do, else its best.
        note = ""
        chosen: Dict[str, Tuple[str, float]] = {}   # backend key → (model, quality)

        def pace_of(b: Backend):
            return paces.get(b.info.quota_source or "") if b.info.quota_source else None

        # A program with a ladder (the vendor's own guidance) takes the
        # model its role calls for and is always good enough — that's what
        # the vendor says the model is for. The rest (local models) are
        # judged as before: good enough for this difficulty, or not.
        role = self.role_for(need)
        laddered: Dict[str, str] = {}
        for b in candidates:
            lad = self.ladder_for(b.key)
            if lad:
                chosen[b.key] = (self._ladder_model(lad, role, pace_of(b)), 1.0)
                laddered[b.key] = lad.get("vendor", "")
        if need.escalate and laddered:
            for b in candidates:
                if b.key not in laddered:
                    rejected.append(f"{b.key}: the last answer needed a stronger model")
            candidates = [b for b in candidates if b.key in laddered]

        if need.task and need.difficulty:
            bar = priors.NEED.get(need.difficulty, 0.72)
            for b in candidates:
                if b.key not in laddered:
                    chosen[b.key] = self._pick_model(b, need.task, need.difficulty, bar,
                                                     pace=pace_of(b))
            good = [b for b in candidates if chosen[b.key][1] >= bar]
            if good:
                note = f" for {need.task}/{need.difficulty}"
                for b in candidates:
                    if chosen[b.key][1] < bar:
                        rejected.append(f"{b.key}: likely not good enough for "
                                        f"{need.difficulty} {need.task}")
                candidates = good
            else:
                best = max(chosen[b.key][1] for b in candidates)
                candidates = [b for b in candidates if chosen[b.key][1] == best]
                note = f" — the best eki has for {need.difficulty} {need.task}"

        # cheapest first — the tier, made dearer by a window being spent
        # ahead of pace and cheaper by one that's behind — then the policy's
        # order, then config order, which is the user's standing preference
        def tier(b: Backend) -> int:
            return self.policy.tier_for(b.key, b.info.cost.tier)

        def factor(b: Backend) -> float:
            p = pace_of(b)
            if p is None:
                return 1.0
            m = chosen.get(b.key, ("", 0.0))[0]
            if not m:                               # the default: known by its label
                rec = next((r for r in self.models_for(b.key) or [] if r.model == ""), None)
                m = getattr(rec, "label", "") if rec else ""
            return p.factor * p.model_factor(m)

        def paced(b: Backend) -> float:
            return round(tier(b) * factor(b), 2)

        unpaced = min(candidates, key=lambda b: (tier(b), self.policy.rank(b.key)))
        candidates.sort(key=lambda b: (paced(b), self.policy.rank(b.key)))
        pick = candidates[0]
        model = chosen.get(pick.key, ("", 0.0))[0]
        if note_row:
            rejected.insert(0, note_row.rstrip("; "))
        name = f"{pick.key} ({model})" if model else pick.key
        if pick.key in laddered:
            vendor = laddered[pick.key] or "the vendor"
            said = {"top": f"{vendor}'s most capable", "fast": f"{vendor}'s fast model",
                    "default": f"{vendor}'s default for most work"}[role]
            if model != self.ladder_for(pick.key).get(role, model):
                said = f"{vendor}'s default (its top model's window is being spent fast)"
            why = f"{name}: {said}"
            if need.escalate:
                why += ", after the last answer was corrected or failed"
        else:
            why = f"{name}: cheapest fit (tier {tier(pick)}){note}"
        if tier(pick) != pick.info.cost.tier:
            why += " by policy"
        pace = pace_of(pick)
        if pace is not None and pace.pace.why:
            why += f", {pace.pace.why}"
        if unpaced is not pick:
            other = pace_of(unpaced)
            if other is not None and other.pace.why:
                why += f"; {unpaced.key} {other.pace.why}"
        if pick.info.cost.note:
            why += f", {pick.info.cost.note}"
        return Choice(pick, why, rejected, model=model)

    ORDINAL = ("1st", "2nd", "3rd", "4th", "5th", "6th")

    def _from_row(self, need: Need, candidates: List[Backend], rejected: List[str],
                  paces: Dict[str, Any]) -> Optional[Choice]:
        """The first of the row's choices that can take the request now."""
        from .table import parse_target
        by_key = {b.key: b for b in candidates}
        passed: List[str] = []
        hands = self.with_tools() if need.tools else {}
        for i, target in enumerate(need.targets):
            key, role, only = parse_target(target)
            if key in hands:
                key = hands[key]            # this one needs tools: the same model, with Codex's hands
            b = by_key.get(key)
            if b is None:
                why = next((r.split(": ", 1)[1] for r in rejected if r.startswith(f"{key}: ")), "not set up")
                passed.append(f"{key}: {why}")
                continue
            if only == "easy" and need.difficulty != "easy":
                passed.append(f"{key}: small changes only")
                continue
            lad = self.ladder_for(key) if role else {}
            pace = paces.get(b.info.quota_source or "") if b.info.quota_source else None
            model = self._ladder_model(lad, role, pace) if lad else ""
            rested = bool(lad) and role == "top" and model != lad.get("top", model)
            name = f"{key} ({model})" if model else key
            why = f"{name}: {need.row_title or need.row} → {self.ORDINAL[min(i, 5)]} choice"
            if rested:
                why += f" ({lad.get('top')}'s own window is being spent fast)"
            if passed:
                why += " — passed over " + "; ".join(passed)
            return Choice(b, why, rejected, model=model)
        return None

    #: a top model with its own window (Fable's week) spent this much faster
    #: than it lasts is given a rest: the default takes its work
    TOP_PACE_LIMIT = 1.5

    @staticmethod
    def role_for(need: Need) -> str:
        """The vendor's default for the work, hard work included; its top
        model when the default fell short — the answer before was corrected,
        or failed ("…or when your evals on Opus still fall short", as
        Anthropic puts it); its fast one for easy work."""
        if need.escalate:
            return "top"
        if need.difficulty == "easy":
            return "fast"
        return "default"

    def _ladder_model(self, lad: Dict[str, str], role: str, pace: Any = None) -> str:
        model = lad.get(role, lad.get("default", ""))
        if role == "top" and pace is not None and model:
            try:
                if pace.model_factor(model) >= self.TOP_PACE_LIMIT:
                    return lad.get("default", "")
            except Exception:                       # noqa: BLE001
                pass
        return model

    def _pick_model(self, backend: Backend, task: str, difficulty: str,
                    bar: float, pace: Any = None) -> Tuple[str, float]:
        """(model, quality): the cheapest of the provider's models that clears
        the bar, taking the default when it does; its best when none does.
        A model with a window of its own (Fable's week) is costed at its pace."""
        records = list(self.models_for(backend.key) or [])
        if not records:
            return "", self._quality(backend, task)
        scored = [(r.model, float(r.quality(task, difficulty)),
                   float(getattr(r, "cost", 1.0))
                   * (pace.model_factor(r.model or getattr(r, "label", "")) if pace else 1.0))
                  for r in records]
        default = next(((q, c) for m, q, c in scored if m == ""), None)
        adequate = [(c, -q, m, q) for m, q, c in scored if q >= bar]
        if adequate:
            # the cheapest that clears the bar; the default when it costs no
            # more than that (it's what the user set up, and it needs no flag)
            c, _, m, q = min(adequate)
            if default is not None and default[0] >= bar and default[1] <= c:
                return "", default[0]
            return m, q
        q, m = max((q, m) for m, q, _ in scored)
        return m, q
