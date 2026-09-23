# SPDX-License-Identifier: Apache-2.0
"""Which models are out there, read once a day from the people who make and
use them (docs/watch.md).

eki used to rank models itself — class priors, benchmark snapshots, a
threshold and a cost. It concluded Fable beat Opus at everything, while
Anthropic's own page says to start with Opus for most work. So eki no longer
works it out; it reads it:

- **Frontier models — the vendor's own guidance.** Anthropic's and OpenAI's
  model pages say which model is the default, which is the most capable,
  which is the fast one. A reader model turns the page into a *ladder* over
  the models the program on this Mac can actually run (Claude Code's
  aliases, Codex's model list):  default · top · fast. Routing follows the
  ladder: work → default (hard work too), easy work → fast, and the top
  model when the default fell short — a correction, or a failure retried.
- **Local models — what people run, and what their makers measured.**
  Ollama's library sorted by popularity is the shortlist; a model's page
  carries its makers' benchmark chart (an image, read by a model that can
  see). A candidate that fits this Mac is compared with the local model you
  run, on the benchmarks both appear in — the chart usually includes its
  predecessor. Where the chart doesn't say, eki's own test is the fallback,
  after download. A better one is *suggested*; you take it with one command.

What changed since yesterday is news (a notification, the journal). A page
that can't be read is a fault like any other (eki/observe.py).
"""
from __future__ import annotations

import html as html_mod
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOME = Path("~/.eki/watch").expanduser()
STATE = HOME / "state.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; eki model watch)"}
#: once a day
EVERY_HOURS = 20
#: popular Ollama models looked at each day
POPULAR = 15

VENDORS: Dict[str, Dict[str, str]] = {
    "claude_code": {"vendor": "Anthropic", "url": "https://platform.claude.com/docs/en/models/overview"},
    "codex": {"vendor": "OpenAI", "url": "https://developers.openai.com/api/docs/models"},
}
OLLAMA = "https://ollama.com"
#: where each program's newest version is published
NPM = {"claude_code": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
ROLES = ("default", "top", "fast")


# ---- state ----------------------------------------------------------------------

def load() -> Dict[str, Any]:
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state: Dict[str, Any]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE)


_CACHE: Dict[str, Any] = {"mtime": None, "state": {}}


def ladder(provider: str) -> Dict[str, str]:
    """The provider's ladder ({role: model id}, "" = the program's own
    default), or {} before the first reading. Re-read when the file changes."""
    try:
        m = STATE.stat().st_mtime
    except OSError:
        return {}
    if _CACHE["mtime"] != m:
        _CACHE.update(mtime=m, state=load())
    v = (_CACHE["state"].get("vendors") or {}).get(provider) or {}
    got = v.get("ladder") or {}
    out = {r: str(got[r]) for r in ROLES if r in got and got[r] is not None}
    if out:
        out["vendor"] = str(v.get("vendor") or "")
    return out


def due(state: Dict[str, Any], now: Optional[float] = None) -> bool:
    return (now or time.time()) - float(state.get("at") or 0) >= EVERY_HOURS * 3600


# ---- pages -------------------------------------------------------------------------

def text_of(page: str, limit: int = 30_000) -> str:
    """A page's words, without markup, scripts or styles."""
    t = re.sub(r"<(script|style|svg|noscript)\b.*?</\1>", " ", page, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_mod.unescape(t)
    return re.sub(r"\s+", " ", t).strip()[:limit]


_LI = re.compile(r'<li\b[^>]*>\s*<a href="/library/([^"/:]+)"(.*?)</li>', re.S)
_SPAN = re.compile(r"<span[^>]*>([^<]{1,24})</span>")
_SIZE = re.compile(r"^(?:e)?\d+(?:\.\d+)?[bm]$")        # "27b", "350m"; pulls are "1.2M"
_PULLS = re.compile(r"<span\s*>([\d.,]+[KMB]?)</span>\s*<span[^>]*>&nbsp;Pulls", re.I)
_UPDATED = re.compile(r'title="([A-Z][a-z]{2} \d{1,2}, \d{4}[^"]*)"')


def _count(s: str) -> int:
    s = s.replace(",", "").strip().upper()
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(s[-1:], 1)
    try:
        return int(float(s.rstrip("KMB")) * mult)
    except ValueError:
        return 0


def parse_search(page: str) -> List[Dict[str, Any]]:
    """Ollama's model list, in the order shown (popular first)."""
    out = []
    for name, body in _LI.findall(page):
        spans = [s.strip() for s in _SPAN.findall(body)]
        sizes = [s for s in spans if _SIZE.match(s)]
        caps = [s for s in spans if s in ("vision", "tools", "thinking", "embedding", "audio", "cloud")]
        pulls = _PULLS.search(body)
        updated = _UPDATED.search(body)
        desc = re.search(r"<p[^>]*>([^<]{3,400})</p>", body)
        out.append({"name": name, "sizes": sizes, "caps": caps,
                    "cloud_only": "cloud" in caps and not sizes,
                    "pulls": _count(pulls.group(1)) if pulls else 0,
                    "updated": updated.group(1) if updated else "",
                    "about": html_mod.unescape(desc.group(1).strip()) if desc else ""})
    return out


_TAG = re.compile(r"\b([a-z0-9][\w.-]*):([\w.-]+)(?: latest)?( MLX)? (\d+(?:\.\d+)?)GB · (\d+)K context")


def parse_library(page: str, name: str) -> Dict[str, Any]:
    """A model's page: its builds (size on disk, context) and the pictures
    in its readme that may be benchmark charts."""
    text = text_of(page, 60_000)
    tags: Dict[str, Dict[str, Any]] = {}
    for model, tag, mlx, gb, ctx in _TAG.findall(text):
        if model != name:
            continue
        tags.setdefault(tag, {"tag": tag, "gb": float(gb), "context_k": int(ctx), "mlx": bool(mlx)})
    images = []
    for src, alt in re.findall(r'<img[^>]*src="(/assets/library/[^"]+)"[^>]*?(?:alt="([^"]*)")?\s*/?>', page):
        images.append({"url": OLLAMA + src, "alt": alt or ""})
    readme = text.split(" Readme ", 1)[-1][:6000] if " Readme " in text else ""
    return {"name": name, "tags": list(tags.values()), "images": images, "readme": readme}


def charts(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Which pictures to show the reader: named like a chart first; the
    first picture of a readme is usually the logo, so it goes last."""
    named = [i for i in images if re.search(r"bench|score|eval|perf|compar|result", i["alt"], re.I)]
    rest = [i for i in images[1:] if i not in named]
    return (named + rest)[:3]


# ---- names ---------------------------------------------------------------------------

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def family(name: str) -> Tuple[str, str]:
    """("qwen", "3.8") from "qwen3.8" / "Qwen3.8-27B-4bit"; version "" if none."""
    base = name.split("/")[-1].lower()
    m = re.match(r"([a-z][a-z-]*?)[-_]?(\d+(?:\.\d+)*)", base)
    return (m.group(1).rstrip("-"), m.group(2)) if m else (re.sub(r"[^a-z]", "", base), "")


def newer(a: str, b: str) -> bool:
    """Is version a after version b ("4.0" > "3.8")?"""
    def parts(v: str) -> List[int]:
        return [int(x) for x in v.split(".") if x.isdigit()]
    return bool(a) and bool(b) and parts(a) > parts(b)


def params_of(name: str) -> Optional[float]:
    m = re.findall(r"(\d+(?:\.\d+)?)b\b", name.lower().replace("_", "-"))
    return max(float(x) for x in m) if m else None


# ---- what the reader is asked ------------------------------------------------------

def vendor_prompt(vendor: str, page_text: str, runnable: List[Dict[str, str]], program: str) -> str:
    rows = "\n".join(f"- {r['id']}: {r.get('label') or r['id']}"
                     + (f" — runs {r['resolved']}" if r.get("resolved") else "")
                     for r in runnable) or "(none listed)"
    note = ("Each Claude Code id is an alias that always means the newest model of that family "
            "(\"opus\" = the newest Opus). " if program == "claude_code" else "")
    return (
        f"Below is the text of {vendor}'s model overview page, fetched today, and the models "
        f"{program} on this Mac can run. {note}\n\n"
        "1. Say what the vendor itself recommends: its default or starting point for most work, "
        "its most capable model (for the hardest work), and its fast / cheap one. Quote the "
        "sentence where it says so.\n"
        f"2. Fill each role with a runnable model ONLY — an exact id from the list. When the "
        "vendor's own pick for a role isn't runnable, use the runnable model the vendor positions "
        "closest to that role (e.g. its next-fastest for \"fast\"); null only when no runnable "
        "model fits the role at all. Don't guess from names when the page says otherwise.\n\n"
        "Reply with ONE JSON object and nothing else:\n"
        '{"vendor_models": [{"id": "…", "name": "…", "role": "default|top|fast|other", '
        '"positioning": "…"}], "guidance": "the quoted sentence", '
        '"ladder": {"default": "<runnable id or null>", "top": "…", "fast": "…"}, '
        '"not_offered": ["vendor ids the program can\'t run yet"]}\n\n'
        f"Runnable in {program}:\n{rows}\n\n"
        f"The page:\n{page_text}")


def chart_prompt(model: str, paths: List[str], readme: str, size: str = "") -> str:
    files = "\n".join(f"- {p}" for p in paths)
    which = f" (the {size} build, or the size closest to it)" if size else ""
    return (
        f"These pictures come from the Ollama page of the model {model!r}. Read each image file "
        f"and look at it carefully:\n{files}\n\n"
        "If one is a benchmark chart or table, write down every number it shows: for each "
        "benchmark, the score of each model in it, exactly as printed. If none is, say so.\n"
        "Reply with ONE JSON object and nothing else:\n"
        '{"is_chart": true|false, "benchmarks": [{"name": "…", "scores": {"<model as labelled>": <number>}}], '
        f'"subject": "<exactly one label, copied from the chart, that is this model{which}; empty if none>"}}\n\n'
        f"The page's own words, for context:\n{readme[:3000]}")


def parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def clean_ladder(answer: Dict[str, Any], runnable: List[Dict[str, str]]) -> Dict[str, str]:
    """The ladder over what the program here can run.

    Where the program says which model each of its names runs (Claude Code
    does), the vendor's pick for a role maps onto it by that, not by the
    reader's judgement: the exact model, else the same family in an older
    version ("opus" running Opus 5 while the vendor's default is Opus 5.5 —
    still the right *kind* of model; the gap is reported as stale). The
    reader's ladder fills what that leaves, and only with runnable ids. A
    role nothing fills falls back to the program's own default ("")."""
    ids = {r["id"] for r in runnable}
    resolved = {r["id"]: r.get("resolved") or "" for r in runnable if r.get("resolved")}
    out: Dict[str, str] = {}
    got = answer.get("ladder") or {}
    for role in ROLES:
        v = got.get(role)
        if isinstance(v, str) and v in ids:
            out[role] = v
    for vm in answer.get("vendor_models") or []:
        role, vid = str(vm.get("role") or ""), str(vm.get("id") or "")
        if role not in ROLES or not vid or not resolved:
            continue
        exact = [rid for rid, rv in resolved.items() if rv == vid]
        fam = split_id(vid)[0]
        same = [rid for rid, rv in resolved.items() if fam and split_id(rv)[0] == fam]
        if exact or same:
            out[role] = (exact or same)[0]
    if "default" not in out:
        out["default"] = ""
    return out


# ---- is the program on this Mac up to date? ------------------------------------------

def version_of(text: str) -> str:
    m = re.search(r"\d+\.\d+\.\d+", text or "")
    return m.group(0) if m else ""


def behind(installed: str, latest: str) -> bool:
    def t(v: str) -> Tuple[int, ...]:
        return tuple(int(x) for x in v.split(".") if x.isdigit())
    return bool(installed and latest) and t(latest) > t(installed)


_MODEL_ID = re.compile(r"^(?P<fam>[a-z]+(?:-[a-z]+)*)-(?P<ver>\d+(?:[-.]\d+)*?)(?:-\d{8})?$")


def split_id(model_id: str) -> Tuple[str, str]:
    """("claude-opus", "5.5") from "claude-opus-5-5"; ("", "") if it isn't shaped so."""
    m = _MODEL_ID.match((model_id or "").lower())
    return (m.group("fam"), m.group("ver").replace("-", ".")) if m else ("", "")


def stale(vendor_models: List[Dict[str, Any]], runs: Dict[str, str]) -> List[Dict[str, str]]:
    """Models the vendor puts on its ladder that the program here only runs
    an older version of — "opus" means claude-opus-5 here while Anthropic's
    default is claude-opus-5-5. `runs` is {alias: the model it resolves to}."""
    out = []
    resolved = set(runs.values())
    for vm in vendor_models or []:
        vid, role = str(vm.get("id") or ""), str(vm.get("role") or "")
        if role not in ROLES or not vid or vid in resolved:
            continue
        fam, ver = split_id(vid)
        for alias, rid in runs.items():
            f2, v2 = split_id(rid)
            if fam and f2 == fam and newer(ver, v2):
                out.append({"role": role, "vendor": vid, "alias": alias, "runs": rid})
                break
    return out


def update_command(kind: str, binary: str) -> List[str]:
    """How this program updates itself, as installed on this Mac."""
    if kind == "claude_code":
        return [binary or "claude", "update"]
    real = str(Path(binary).resolve()) if binary else ""
    # the copy eki runs, updated the way it was installed: npm's lives in
    # node_modules (Homebrew's prefix too), a cask in the Cellar, and the
    # standalone one updates itself
    if "node_modules" in real:
        return ["npm", "install", "-g", f"{NPM['codex']}@latest"]
    if "Cellar" in real or "Caskroom" in real:
        return ["brew", "upgrade", "codex"]
    if real and Path(real).is_file():
        # standalone, from Codex's GitHub release: `codex update` can't tell
        # how it got there, so eki replaces it from the same release
        return [sys.executable, "-m", "eki.codex_host", "update", real]
    return ["npm", "install", "-g", f"{NPM['codex']}@latest"]


# ---- comparing a local candidate -------------------------------------------------------

def _label_is(label: str, fam: str, version: str, size: Optional[float]) -> bool:
    n = norm(label)
    if norm(fam + version) not in n:
        return False
    return size is None or norm(f"{size:g}b") in n


def compare(chart: Dict[str, Any], subject: str, current: str) -> Dict[str, Any]:
    """The candidate against the model you run, on the benchmarks both appear in."""
    fam, ver = family(current)
    size = params_of(current)
    shared, wins, rows = 0, 0, []
    for b in chart.get("benchmarks") or []:
        scores = b.get("scores") or {}
        mine = next((v for k, v in scores.items() if _label_is(k, fam, ver, size)), None)
        if mine is None and size is not None:
            mine = next((v for k, v in scores.items() if _label_is(k, fam, ver, None)), None)
        theirs = scores.get(subject) if subject else None
        if not isinstance(mine, (int, float)) or not isinstance(theirs, (int, float)):
            continue
        shared += 1
        wins += theirs > mine
        rows.append({"benchmark": b.get("name"), "candidate": theirs, "current": mine})
    return {"shared": shared, "wins": wins, "rows": rows}


def verdict(cand: Dict[str, Any], current: str, comparison: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """("suggest" | "test" | "skip", why)."""
    if comparison and comparison["shared"] >= 3:
        s, w = comparison["shared"], comparison["wins"]
        if w * 2 > s:
            return "suggest", f"beats {current} on {w} of {s} shared benchmarks"
        return "skip", f"beats {current} on only {w} of {s} shared benchmarks"
    fam_c, ver_c = family(cand["name"])
    fam_m, ver_m = family(current)
    if fam_c == fam_m and newer(ver_m, ver_c):
        return "skip", f"older than the {fam_m} {ver_m} you run"
    if fam_c == fam_m and newer(ver_c, ver_m):
        return "test", f"a newer {fam_c} than {current}, but its chart doesn't compare them — eki would test it after download"
    return "skip", "no benchmarks shared with what you run"


def pick_tag(page: Dict[str, Any], ceiling_gb: float, current_params: Optional[float]) -> Optional[Dict[str, Any]]:
    """The build to consider: the largest that fits with room to spare,
    preferring MLX (it runs natively here) and a size near what you run."""
    fits = [t for t in page["tags"] if t["gb"] + 4 <= ceiling_gb and "cloud" not in t["tag"]]
    if not fits:
        return None
    def key(t: Dict[str, Any]) -> Tuple:
        p = params_of(t["tag"]) or 0
        near = -abs(p - current_params) if current_params else 0
        return (t["mlx"], near, t["gb"])
    return max(fits, key=key)


def match_build(name: str, tag: str, rows: List[Dict[str, Any]]) -> Optional[str]:
    """The mlx-community build of this model and size, 4-bit first."""
    want = norm(name) + norm(re.sub(r"-mlx$", "", tag))
    found = [r["id"] for r in rows if want in norm(r["id"].split("/")[-1])]
    four = [r for r in found if re.search(r"4bit$", r, re.I)]
    return (four or found or [None])[0]


def news(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """What changed since the last reading, in words."""
    out = []
    for prov, v in (after.get("vendors") or {}).items():
        old = ((before.get("vendors") or {}).get(prov) or {}).get("ladder") or {}
        new = v.get("ladder") or {}
        for role in ROLES:
            if role in new and old.get(role) != new.get(role) and before.get("vendors"):
                out.append(f"{v.get('vendor', prov)}: {role} is now {new[role] or 'the default'}"
                           + (f" (was {old[role]})" if old.get(role) else ""))
        for mid in v.get("not_offered") or []:
            if mid not in (((before.get("vendors") or {}).get(prov) or {}).get("not_offered") or []):
                out.append(f"{v.get('vendor', prov)} lists {mid}, which {prov} can't run yet")
    for prov, v in (after.get("vendors") or {}).items():
        prog = v.get("program") or {}
        was = (((before.get("vendors") or {}).get(prov) or {}).get("program") or {})
        for st in prog.get("stale") or []:
            if st not in (was.get("stale") or []):
                out.append(f"{v.get('vendor', prov)}'s {st['role']} is {st['vendor']}, but "
                           f"{prov} here runs {st['runs']} as {st['alias']!r}"
                           + (f" — {prog['latest']} is out ({' '.join(prog['update'])})"
                              if prog.get("behind") else ""))
        if prog.get("behind") and prog.get("latest") != was.get("latest"):
            out.append(f"{prov} {prog.get('installed')} → {prog['latest']} is out ({' '.join(prog['update'])})")
    seen = {s["name"] for s in before.get("suggestions") or []}
    for s in after.get("suggestions") or []:
        if s["name"] not in seen:
            out.append(f"local: {s['name']} — {s['why']}")
    return out
