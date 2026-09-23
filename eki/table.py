# SPDX-License-Identifier: Apache-2.0
"""The routing table (docs/routing.md).

Two things, each checkable on its own:

1. **Which row a request is** — the prompt check. The label eki reads off a
   request (eki/classify.py) maps to a row; a request like one you filed
   under a row yourself ("things like this are writing") goes to that row.
2. **Where each row goes** — first choice, then the fallbacks. The first
   choice that can take the request right now gets it: running, with
   quota, able to do this kind of work. Moving down the row is failover.

The table fills itself. Within a program the vendor's ladder says which
model (eki/watch.py); between subscriptions, the one with the most room
comes first (eki/capacity.py) — so someone with a $200 Codex plan and a $20
Claude one gets Codex first, and someone with the reverse gets Claude.
Cells you set, in plain words, or that eki learned from what you do, are
kept over the defaults and marked as such (~/.eki/routing.json).

A target is "<provider>" or "<provider>@<role>" (a ladder role: default,
top, fast); "…!easy" takes only easy requests (a local model given a code
change: small ones only).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PATH = Path("~/.eki/routing.json").expanduser()

ROWS: List[Tuple[str, str, List[str]]] = [
    ("quick", "Quick question", ["what is 17×23", "hi", "how many days until Friday"]),
    ("writing", "Writing, translation", ["a short poem about autumn", "translate this to Japanese",
                                          "make this email friendlier"]),
    ("explain", "Explain, reason", ["explain Python's GIL", "prove odd + odd is even",
                                     "compare Postgres and SQLite for a small app"]),
    ("code", "Code change", ["fix the failing test", "add week dates to the date parser",
                             "write a function that reverses a string"]),
    ("code_hard", "Big or hard code change", ["refactor auth and update all callers",
                                              "migrate the app to the new API"]),
    ("retry", "Retry after a correction or failure", ["no, standard library only",
                                                      "that didn't work, try again"]),
    ("research", "Research on the web", ["latest news on Opus 5.5", "price of an M5 Mac mini"]),
    ("picture", "Picture", ["a watercolor fox", "make it bluer"]),
]
TITLES = {r: t for r, t, _ in ROWS}
ROLE_WORD = {"default": "", "top": "top", "fast": "fast"}


# ---- the prompt check -------------------------------------------------------------------

#: a question, not a change: "explain how…", "why does…", "what is…"
_ASKS = re.compile(r"^\W*(please\s+)?(explain|what|why|how (does|do|is|are|would)|when (should|do)|compare"
                   r"|what's|whats|is it|should i|can you explain|tell me)\b", re.I)


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def row_for(task: str, difficulty: str, escalate: bool = False, prompt: str = "",
            examples: Optional[Dict[str, List[str]]] = None) -> Tuple[str, str]:
    """(row, why) for a labelled request."""
    if escalate:
        return "retry", "the answer before was corrected, or failed"
    for row, said in (examples or {}).items():
        best = max((similar(prompt, e) for e in said), default=0.0)
        if best >= 0.6:
            return row, "like a request you filed under it"
    if task == "image":
        return "picture", "asks for a picture"
    if task == "research":
        return "research", "needs things looked up"
    if task == "code" and _ASKS.match(prompt or ""):
        return ("explain", "a question about code") if difficulty != "easy" else ("quick", "a quick question about code")
    if task in ("repo", "code", "screen"):
        if difficulty == "hard":
            return "code_hard", f"{task}, hard"
        return "code", task
    if task in ("writing", "translate"):
        return "writing", task
    if difficulty == "easy":
        return "quick", f"{task}, easy"
    return "explain", f"{task}, {difficulty}"


# ---- the default table -------------------------------------------------------------------

def defaults(subs: List[str], local: Optional[str], images: List[str], web: List[str],
             metered: List[str], raw: Optional[str] = None,
             seconds: Optional[Dict[str, float]] = None) -> Dict[str, List[str]]:
    """The table for what this Mac has: `subs` are the subscriptions, the
    one with the most room first; `local` the local model with a harness
    (or the model itself, when there's no harness); `raw` the local model
    answering directly — for requests that only need an answer, faster; a
    request that needs hands passes it over for the harness; `metered` API
    keys, last. With `seconds` (typical time per backend) the two local
    choices go fastest first."""
    if raw and local and raw != local:
        pair = sorted([raw, local], key=lambda k: (seconds or {}).get(k, 0 if k == raw else 1e9))
    else:
        pair = [local] if local else []
    s0 = subs[0] if subs else None
    s1 = subs[1] if len(subs) > 1 else None
    m = [f"{k}@default" for k in metered]

    def row(*xs: Optional[str]) -> List[str]:
        out: List[str] = []
        for x in xs:
            if x and x not in out:
                out.append(x)
        return out
    at = lambda p, r: f"{p}@{r}" if p else None          # noqa: E731
    return {
        "quick": row(*pair, at(s0, "fast"), at(s1, "fast")),
        "writing": row(*pair, at(s0, "default"), at(s1, "default"), *m),
        "explain": row(at(s0, "default"), at(s1, "default"), *pair, *m),
        "code": row(at(s0, "default"), at(s1, "default"), f"{local}!easy" if local else None, *m),
        "code_hard": row(at(s0, "default"), at(s1, "default"), *m),
        "retry": row(at(s0, "top"), at(s0, "default"), at(s1, "default"), *m),
        "research": row(*[f"{k}@default" for k in subs if k in web], *[f"{k}@default" for k in metered if k in web]),
        "picture": row(*images),
    }


def parse_target(t: str) -> Tuple[str, str, str]:
    """"codex-qwen!easy" → ("codex-qwen", "", "easy"); "claude_code@top" → (…, "top", "")."""
    only = ""
    if "!" in t:
        t, only = t.split("!", 1)
    key, _, role = t.partition("@")
    return key, role, only


# ---- your cells and learned ones ----------------------------------------------------------

def load() -> Dict[str, Any]:
    try:
        data = json.loads(PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: Dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(PATH)


def effective(base: Dict[str, List[str]], rules: Dict[str, Any], thread: str = "",
              now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """The table as routing uses it: {row: {"targets", "source", "said"}}.

    Order of what changes a cell: a row you (or eki, learning) set replaces
    the default; "backup" moves a provider to the end of every row; "never"
    takes it out of every row; a rule for this thread goes first."""
    now = now or time.time()
    out: Dict[str, Dict[str, Any]] = {}
    for row, _, _ in ROWS:
        cell = (rules.get("rows") or {}).get(row)
        if cell and cell.get("targets"):
            out[row] = {"targets": list(cell["targets"]), "source": cell.get("source", "you"),
                        "said": cell.get("said", "")}
        else:
            out[row] = {"targets": list(base.get(row) or []), "source": "default", "said": ""}
    backups = [b["target"] for b in rules.get("backup") or []]
    nevers = [n["target"] for n in rules.get("never") or []]
    for row, cell in out.items():
        ts = [t for t in cell["targets"] if parse_target(t)[0] not in nevers]
        front = [t for t in ts if parse_target(t)[0] not in backups]
        back = [t for t in ts if parse_target(t)[0] in backups]
        cell["targets"] = front + back
    t = (rules.get("threads") or {}).get(thread) if thread else None
    if t and (not t.get("until") or t["until"] > now):
        for row, cell in out.items():
            if row == "picture":
                continue
            for target in reversed(t["targets"]):
                cell["targets"] = to_front(cell["targets"], target)
            cell["source"] = "this thread"
            cell["said"] = t.get("said", "")
    return out


def to_front(targets: List[str], target: str) -> List[str]:
    """`target`'s provider first in a row, without saying it twice: the
    row's own entries for that provider move up, roles kept ("use Claude"
    leaves the retry row's Fable before its Opus); a provider the row
    didn't have goes in as given."""
    key = parse_target(target)[0]
    mine = [t for t in targets if parse_target(t)[0] == key]
    rest = [t for t in targets if parse_target(t)[0] != key]
    if parse_target(target)[1] and target not in mine:
        mine = [target] + [t for t in mine if t != target]
    return (mine or [target]) + rest


def set_row(rules: Dict[str, Any], row: str, targets: List[str], source: str, said: str) -> None:
    rules.setdefault("rows", {})[row] = {"targets": targets, "source": source, "said": said,
                                          "at": int(time.time())}


def add_rule(rules: Dict[str, Any], kind: str, target: str, source: str, said: str) -> None:
    items = [x for x in rules.get(kind) or [] if x.get("target") != target]
    items.append({"target": target, "source": source, "said": said, "at": int(time.time())})
    rules[kind] = items


def forget(rules: Dict[str, Any], what: str) -> List[str]:
    """Take back a rule: a row id, a provider (its never/backup rules), a
    thread id, or "all". Returns what was removed."""
    gone: List[str] = []
    if what == "all":
        gone = list((rules.get("rows") or {}).keys()) + [x["target"] for k in ("never", "backup")
                                                          for x in rules.get(k) or []]
        for k in ("rows", "never", "backup", "threads"):
            rules.pop(k, None)
        return gone
    if what in (rules.get("rows") or {}):
        cell = rules["rows"].pop(what)
        if cell.get("source") == "learned":
            rules.setdefault("declined", {})[f"{what}->{cell['targets'][0]}"] = int(time.time())
        gone.append(what)
    for k in ("never", "backup"):
        keep = [x for x in rules.get(k) or [] if x.get("target") != what]
        if len(keep) != len(rules.get(k) or []):
            rules[k] = keep
            gone.append(f"{k} {what}")
    if what in (rules.get("threads") or {}):
        rules["threads"].pop(what)
        gone.append(f"thread {what}")
    return gone


def add_example(rules: Dict[str, Any], row: str, text: str) -> None:
    ex = rules.setdefault("examples", {}).setdefault(row, [])
    if text and text not in ex:
        ex.append(text[:300])
        del ex[:-20]


# ---- learning from what you do ------------------------------------------------------------

#: picks by name that move a row's work to one provider, in distinct threads
LEARN_AFTER = 3
LEARN_DAYS = 14
DECLINED_DAYS = 30


def learnable(overrides: List[Dict[str, Any]], rules: Dict[str, Any], row: str, to: str,
              now: Optional[float] = None) -> bool:
    """Whether picking `to` by name for `row` has become a pattern worth a
    rule: in LEARN_AFTER different threads within two weeks, not a cell you
    set yourself, and not a rule you undid lately."""
    now = now or time.time()
    cell = (rules.get("rows") or {}).get(row) or {}
    if cell.get("source") == "you":
        return False
    if cell.get("targets") and parse_target(cell["targets"][0])[0] == to:
        return False
    declined = max((v for k, v in (rules.get("declined") or {}).items()
                    if k == f"{row}->{to}" or k.startswith(f"{row}->{to}@") or k.startswith(f"{row}->{to}!")),
                   default=0)
    if declined and now - declined < DECLINED_DAYS * 86400:
        return False
    threads = {o.get("conversation") for o in overrides
               if o.get("row") == row and o.get("to") == to and o.get("at", 0) > now - LEARN_DAYS * 86400}
    return len(threads - {None, ""}) >= LEARN_AFTER


# ---- saying it -------------------------------------------------------------------------------

def label_of(target: str, names: Dict[str, str], ladders: Dict[str, Dict[str, str]]) -> str:
    """"claude_code@default" → "Claude Code · opus"."""
    key, role, only = parse_target(target)
    name = names.get(key, key)
    lad = ladders.get(key) or {}
    model = lad.get(role or "default", "") if lad else ""
    if role and not lad:
        model = "" if role == "default" else role
    out = f"{name} · {model}" if model else name
    return out + (" (small changes only)" if only == "easy" else "")


class Labels(dict):
    """{target: how it reads}, and any target not listed — "codex-qwen!easy" —
    read the same way when asked for with .get()."""

    def __init__(self, names: Dict[str, str], ladders: Dict[str, Dict[str, str]]):
        super().__init__()
        self.names, self.ladders = names, ladders

    def get(self, key: str, default: Any = None) -> str:           # type: ignore[override]
        return super().get(key) or label_of(key, self.names, self.ladders)
