# SPDX-License-Identifier: Apache-2.0
"""Skills eki drafts for itself after a run (ROADMAP, Stage 4 *Evolve*).

A skill is the safest thing eki can change about itself: a folder of
instructions in a git repo, readable, revertible with one commit, and no
engine swap. So after a run that taught something, eki asks a model to write
the lesson down as a skill — or to improve one it wrote before — and commits
it to the store every backend reads (eki/skills.py).

Not every run is worth a look. A run is only reviewed when there is a sign
that something was learned:

- *asked*      — the person said so: "remember this as a skill", "from now
                 on…", "next time…"
- *corrected*  — the request opens by correcting the answer before it:
                 "no, use pnpm", "don't add comments"
- *recovered*  — the answer before this one failed or was stopped, and
                 this one went through

The model that did the work reviews it (a fresh one-shot, not the thread's
session), so the lesson is written by whoever knows the domain; setting
`skills_learn_backend` names another. The review can, and usually should,
answer "none".

What eki may change: skills it learned itself and nobody has edited since.
A skill you wrote or edited is yours — eki reads it, never rewrites it — and
`eki` (the built-in) is off limits. Everything learned is a commit whose
message says why and which run taught it; `eki skills learned` lists them and
`eki skills rm NAME` or `git revert` takes one back.

One lesson, one place. Claude Code and Codex each have a memory of their
own, which would keep a second copy that only that program sees. So while
eki is learning:

- both are told (`agent_note`) to leave remembering to eki rather than
  write their memory, CLAUDE.md / AGENTS.md or a skills folder;
- Codex runs with its `memories` feature off;
- a note Claude Code saves to its auto-memory anyway (~/.claude/projects/
  */memory) during a run is itself a signal: the review sees it, and a
  note whose lesson is now a skill is moved out of Claude's memory into
  ~/.eki/learn/absorbed. Notes that are facts about one project stay;
- a skill folder an agent made directly in ~/.claude/skills or
  ~/.agents/skills during a run is taken into the store (linked back, so
  nothing changes for the program) as written for you: eki won't rewrite
  it. Only the folders of the program that ran, and only notes in the
  projects eki's run worked in — your own sessions elsewhere are left alone.

Setting `skills_learn`: "apply" (default — a learned skill is on at once),
"propose" (it arrives turned off, for you to turn on), or "off".
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import skills

LOG = Path("~/.eki/learn.json").expanduser()
#: Claude Code's auto-memory: ~/.claude/projects/<project>/memory/*.md
CLAUDE_PROJECTS = Path("~/.claude/projects").expanduser()
#: where a note whose lesson became a skill is moved to (never deleted)
ABSORBED = Path("~/.eki/learn/absorbed").expanduser()
#: where a CLI reviewing a run is started: never a repo, nothing to touch
WORKDIR = Path("~/.eki/learn").expanduser()
#: reviews eki starts on its own in a day (the ones you ask for don't count)
DAILY = 8
TIMEOUT = 240.0
MODES = ("apply", "propose", "off")

# ---- what the agents are told ------------------------------------------------

AGENT_NOTE = (
    "Remembering is eki's job on this Mac. When the person asks you to remember "
    "something, or corrects how you work, don't save it to your own memory, to "
    "CLAUDE.md or AGENTS.md, or to a skills folder (unless they name that file) — "
    "say it's noted and follow it for the rest of this conversation. After each "
    "turn eki reviews the work and keeps lasting lessons as skills that every "
    "agent here (Claude Code, Codex, local models) is given.")


def learning(settings: Dict[str, Any]) -> bool:
    return str(settings.get("skills_learn", "apply")) in ("apply", "propose")


def agent_note(settings: Dict[str, Any]) -> str:
    """The standing instruction for Claude Code and Codex — none when eki
    isn't learning, so their own memory is theirs again."""
    return AGENT_NOTE if learning(settings) else ""


# ---- what the agents saved anyway ---------------------------------------------

def project_slug(folder: str) -> str:
    """The name Claude Code gives a folder under ~/.claude/projects."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(folder))


def saved_notes(since: float, folders: List[str], limit: int = 6) -> List[Dict[str, Any]]:
    """Notes Claude Code wrote to its auto-memory since `since`, in the
    folders eki's run worked in and nowhere else — a note your own Claude
    Code session wrote in another project at the same time is not eki's to
    look at. The index (MEMORY.md) aside."""
    out: List[Dict[str, Any]] = []
    if not CLAUDE_PROJECTS.is_dir() or not folders:
        return out
    mine = {project_slug(f) for f in folders if f} - {project_slug(str(WORKDIR))}
    for p in sorted(CLAUDE_PROJECTS.glob("*/memory/*.md")):
        if p.name == "MEMORY.md" or p.parent.parent.name not in mine:
            continue
        try:
            if p.stat().st_mtime < since:
                continue
            text = p.read_text(errors="replace")
        except OSError:
            continue
        out.append({"file": f"{p.parent.parent.name}/{p.name}", "path": str(p),
                    "text": text[:3000]})
    return out[-limit:]


def absorb(notes: List[Dict[str, Any]]) -> List[str]:
    """Move notes out of Claude Code's memory (their lesson is a skill now)
    and take their lines out of that project's MEMORY.md index."""
    moved: List[str] = []
    for n in notes:
        src = Path(n["path"])
        if not src.is_file():
            continue
        project = src.parent.parent.name
        dest_dir = ABSORBED / project
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        i = 1
        while dest.exists():
            dest = dest_dir / f"{src.stem}.{i}{src.suffix}"
            i += 1
        src.replace(dest)
        index = src.parent / "MEMORY.md"
        try:
            lines = index.read_text().splitlines(keepends=True)
            kept = [l for l in lines if f"({src.name})" not in l and f"/{src.name})" not in l]
            if kept != lines:
                index.write_text("".join(kept))
        except OSError:
            pass
        moved.append(n["file"])
    return moved


def absorbs(answer: Dict[str, Any], notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The notes the review says its answer (or an existing skill) covers."""
    said = answer.get("absorbs")
    if not isinstance(said, list):
        return []
    names = {str(x).strip() for x in said}
    return [n for n in notes if n["file"] in names or n["file"].split("/", 1)[-1] in names]


def adopt_new_folders(since: float, view: str, by: str = "", run: str = "") -> List[str]:
    """Skill folders the agent made itself in its own CLI's folder (`view`:
    "claude" or "codex") during a run: taken into the store — one copy,
    linked back, every backend — and treated as written for you, so eki
    never rewrites them."""
    if view not in skills.VIEWS:
        return []
    root = skills.VIEWS[view]
    fresh = []
    for u in skills.unmanaged():
        if u["held"] or u["link"] or Path(u["path"]).parent != root:
            continue
        try:
            if (Path(u["path"]) / "SKILL.md").stat().st_mtime < since:
                continue
        except OSError:
            continue
        fresh.append(Path(u["path"]).name)
    if not fresh:
        return []
    return skills.adopt(fresh, by=by, run=run)


# ---- is there anything to learn? ----------------------------------------------

ASKED = re.compile(
    r"(?i)(remember (this|that|it)\b|(make|save|keep|turn|write) (this|that|it)( down)? ((in)?to |as )?a skill"
    r"|as a skill\b|\bfor next time\b|\bnext time\b|\bfrom now on\b|\bgoing forward\b"
    r"|\bin (the )?future,|记住|下次|以后都|今后|覚えて|次から|今後は)")
CORRECTED = re.compile(
    r"(?i)^\W*(no\b|nope\b|wrong\b|not (like )?that\b|that'?s (not|wrong)\b|actually\b|"
    r"don'?t\b|do not\b|never\b|stop\b|instead\b|you (should|shouldn'?t|forgot|missed)\b|"
    r"why did you\b|i said\b|i told you\b|please (don'?t|use|stop)\b|use \S+,? not\b)"
    r"|^\W*(不对|不是|错了|别|不要|应该|違う|そうじゃなく|じゃなくて|しないで)")


def signals(prompt: str, before: List[Dict[str, Any]]) -> List[str]:
    """Why this request might have taught something — empty when nothing did.

    `before` is the thread up to (not including) this request."""
    text = (prompt or "").strip()
    out: List[str] = []
    if ASKED.search(text):
        out.append("asked")
    last = next((t for t in reversed(before) if t["role"] == "assistant"), None)
    if last is not None:
        if CORRECTED.search(text[:160]):
            out.append("corrected")
        meta = _meta(last)
        if meta.get("failed") or meta.get("stopped"):
            out.append("recovered")
    return out


def _meta(turn: Dict[str, Any]) -> Dict[str, Any]:
    m = turn.get("meta")
    if isinstance(m, dict):
        return m
    try:
        return json.loads(m or "{}") or {}
    except (TypeError, ValueError):
        return {}


# ---- the review ------------------------------------------------------------------

WHY = {
    "asked": "The person asked for this to be remembered.",
    "corrected": "The person corrected the answer before this one.",
    "recovered": "The attempt before this one failed or was stopped; this one went through.",
    "saved": "The agent saved a note to its own memory during this work.",
}

PROMPT = """\
You are reviewing a finished piece of work that eki (a model hub on this Mac)
ran. Decide whether it taught something worth keeping as a SKILL: standing
instructions that every agent eki uses (Claude Code, Codex, local models)
will be given whenever a similar task comes up again.

Keep a lesson only if it is
- reusable: it applies to a kind of future task, not only to this one;
- concrete: steps, conventions, commands, preferences or pitfalls — never
  generic advice like "be careful" or "write clean code";
- earned here: what the person asked for, what they corrected, or what made
  a failed attempt work.
Never put secrets, keys, passwords, personal details, or this conversation's
one-off content (its data, its names, its answer) into a skill. If an
existing skill already covers it, or you are unsure, the answer is "none".

Why this work was flagged: {why}

Skills that already exist (name: when to use it) — do not duplicate them:
{existing}
{editable}
The work, oldest first:
{transcript}

{notes}
Reply with ONE JSON object and nothing else. One of:
{{"action": "none", "why": "…"}}
{{"action": "new", "name": "short-kebab-name", "description": "One sentence: which kind of task this is for and what it makes the agent do.", "body": "Markdown instructions, under 300 words.", "why": "what in this work taught it"}}
{{"action": "edit", "name": "<an editable skill>", "description": "…", "body": "the full new body", "why": "…"}}
Prefer "edit" when an editable skill already covers this kind of task.{absorbs}
Only answer; don't save anything to memory or to files."""

NOTES = """
The agent also saved these notes to its own memory during the work. eki keeps
lessons as shared skills instead, so that every agent gets them — one lesson,
one place:
{blocks}
"""

ABSORBS = """
Add "absorbs": ["<note file>", …] to your answer, listing each note above whose
lesson your answer — or a skill that already exists — now fully covers. Leave
out notes that are facts about one project or task (where something lives, a
local patch, a status): those stay in the agent's memory."""


def transcript(turns: List[Dict[str, Any]], limit: int = 9000, per: int = 1800) -> str:
    """The last turns of the thread, each cut to a size a review can afford."""
    parts: List[str] = []
    for t in turns[-10:]:
        if t["role"] not in ("user", "assistant"):
            continue
        who = "PERSON" if t["role"] == "user" else f"AGENT ({t.get('backend') or '?'})"
        body = (t.get("content") or "").strip()
        meta = _meta(t)
        flag = " [this attempt failed]" if meta.get("failed") else (
            " [stopped]" if meta.get("stopped") else "")
        if len(body) > per:
            body = body[: per // 2] + "\n[…]\n" + body[-per // 2:]
        parts.append(f"--- {who}{flag}\n{body}")
    text = "\n".join(parts)
    if len(text) > limit:
        text = "[…earlier turns cut]\n" + text[-limit:]
    return text


def editable() -> List[Dict[str, Any]]:
    """Skills eki learned itself that nobody has edited since."""
    return [s for s in skills.list_skills() if skills.learnable(s["folder"])]


def build_prompt(turns: List[Dict[str, Any]], why: List[str],
                 notes: Optional[List[Dict[str, Any]]] = None) -> str:
    mine = editable()
    mine_names = {s["folder"] for s in mine}
    others = [s for s in skills.list_skills() if s["folder"] not in mine_names]
    existing = "\n".join(f"- {s['name']}: {s['description']}" for s in others) or "(none)"
    ed = ""
    if mine:
        blocks = []
        for s in mine:
            _, body = skills.split(skills.source(s["folder"]))
            blocks.append(f"### {s['name']}: {s['description']}\n{body.strip()[:2500]}")
        ed = ("\nSkills you wrote before and may edit (full text):\n"
              + "\n\n".join(blocks) + "\n")
    blocks = "\n".join(f"### note: {n['file']}\n{n['text'].strip()}" for n in notes or [])
    return PROMPT.format(why=" ".join(WHY.get(w, w) for w in why),
                         existing=existing, editable=ed, transcript=transcript(turns),
                         notes=NOTES.format(blocks=blocks) if notes else "",
                         absorbs=ABSORBS if notes else "")


def parse(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in a model's answer (thinking and fences allowed)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and "action" in obj:
            return obj
    return None


SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[abpr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"]{8,})")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")


def check(d: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """A review's answer made safe to apply, or None and why not."""
    action = str(d.get("action") or "").lower()
    why = " ".join(str(d.get("why") or "").split())[:300]
    if action == "none":
        return None, why or "nothing to keep"
    if action not in ("new", "edit"):
        return None, f"unknown action {action!r}"
    name = str(d.get("name") or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not SLUG.match(name) or name == skills.EKI_SKILL:
        return None, f"bad name {name!r}"
    desc = " ".join(str(d.get("description") or "").split())
    body = str(d.get("body") or "").strip()
    if not 20 <= len(desc) <= 400:
        return None, "description missing or too long"
    if not 40 <= len(body) <= 5000:
        return None, "body missing or too long"
    if SECRET.search(desc + "\n" + body):
        return None, "it looked like it contained a secret"
    held = skills.get(name)
    if held is not None and not skills.learnable(held["folder"]):
        return None, f"{name} is yours — eki doesn't rewrite it"
    if action == "edit" and held is None:
        action = "new"
    if action == "new" and held is not None:
        action = "edit"                     # its own earlier lesson: improve it
    backends = d.get("backends")
    if isinstance(backends, list):
        backends = [b for b in skills.BACKENDS if b in backends] or None
    else:
        backends = None
    return {"action": action, "name": name, "description": desc, "body": body,
            "why": why or "learned from a run", "backends": backends}, ""


def apply(change: Dict[str, Any], mode: str, run: str = "", conversation: str = "") -> Dict[str, Any]:
    """Commit a checked change to the store. In "propose" a new skill arrives
    off, and a skill already on is not changed under you."""
    held = skills.get(change["name"])
    if mode == "propose" and held is not None and held["enabled"]:
        return {"skill": change["name"], "applied": False,
                "note": "proposed an edit to a skill that is on; not applied (propose mode)"}
    s = skills.learn(change["name"], change["description"], change["body"], why=change["why"],
                     run=run, conversation=conversation, backends=change["backends"],
                     enabled=(mode == "apply"))
    return {"skill": s.get("name", change["name"]), "applied": True,
            "action": change["action"], "enabled": bool(s.get("enabled"))}


# ---- the log -------------------------------------------------------------------------

def _load() -> Dict[str, Any]:
    try:
        data = json.loads(LOG.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(entry: Dict[str, Any]) -> None:
    data = _load()
    rows = data.get("reviews") or []
    rows.append({"at": int(time.time()), **entry})
    data["reviews"] = rows[-300:]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(LOG)


def reviews(limit: int = 30) -> List[Dict[str, Any]]:
    return list(reversed((_load().get("reviews") or [])[-limit:]))


def budget_left(daily: int = DAILY) -> int:
    """Reviews eki may still start on its own today."""
    since = time.time() - 86400
    used = sum(1 for r in _load().get("reviews") or []
               if r.get("at", 0) >= since and "asked" not in (r.get("signals") or [])
               and not r.get("manual"))
    return max(0, daily - used)
