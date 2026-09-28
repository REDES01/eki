"""The short digest page: patch notes readable in twenty seconds
(docs/self-build.md, "The digest").

Pure: no database, no files. digest.py hands it the day's changes and writes
what it returns; digestprose.py asks the local model for the notes with
`brief` and keeps them only when `check` finds nothing wrong.

A page is the title `# eki MM-DD`, one headline sentence, a section per area
that changed (the bare area name, then its `- ` lines) and the closing line
eki writes itself: `Waits for you: N.  Score: <verdict>.` (with `K PRs to review.`
between them when pull requests are open)
"""
from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from . import doccheck

AREAS = ("Self-build", "Web UI", "Models", "Routing", "Pictures", "Engine", "Docs")
HEADLINE_WORDS, LINE_WORDS, SECTION_LINES, PAGE_LINES = 20, 14, 5, 15
DOCS_LINES = 1

EXAMPLE = """\
# eki 09-28
Merging and shipping run without a person; the Station folds to one line per section.

Self-build
- Pieces merge and go live on their own; a hand-pushed fix ships within five minutes.
- A piece that fails its checks no longer rides into main under another one.

Web UI
- Station: one summary line per section, details on click.
- Settings page edits routing and providers as a form.

Models
- Easy code changes try Codex on the local model first; Claude takes over on handoff.

Waits for you: nothing.  Score: same as yesterday.
"""

#: path patterns per area; the first that matches wins, and ties between
#: areas go to the one earlier here
RULES = [
    ("Docs", ("docs/*",)),
    ("Routing", ("eki/routing/*",)),
    ("Web UI", ("eki/web/*", "eki/api_station.py", "eki/api_settings.py")),
    ("Pictures", ("eki/providers/comfyui*", "eki/comfy*", "eki/gallery.py")),
    ("Models", ("eki/providers/*", "eki/models.py")),
    ("Self-build", ("eki/self*", "eki/queue.py", "eki/train*.py", "eki/builds.py",
                    "eki/resolve.py", "eki/rebase.py", "eki/integration.py", "eki/review.py",
                    "eki/faults.py", "eki/score.py", "eki/digest*.py", "eki/patchnotes.py",
                    "eki/chores.py")),
]
ORDER = [area for area, _ in RULES] + ["Engine"]

HEX_ID = re.compile(r"\b[0-9a-f]{10}\b")
FILE_NAME = re.compile(r"\b[\w/.-]+\.(py|js|css|md|json|sh)\b")
INNER_WORD = re.compile(r"\b(item|goal|gate|worktree)s?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Change:
    id: str
    title: str
    area: str
    kind: str  # change | tests | docs


def _clean(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def _is_test(path: str) -> bool:
    return _clean(path).startswith("tests/")


def _area_of_path(path: str) -> str:
    if "/" not in path and path.endswith(".md"):
        return "Docs"
    for area, patterns in RULES:
        if any(fnmatch.fnmatchcase(path, p) for p in patterns):
            return area
    return "Engine"


def area_of(paths: Iterable[str]) -> str:
    """The area most of `paths` map to, tests/ left out; Engine when none is left."""
    counts: Dict[str, int] = {}
    for p in paths:
        if not _is_test(p):
            area = _area_of_path(_clean(p))
            counts[area] = counts.get(area, 0) + 1
    if not counts:
        return "Engine"
    return max(counts, key=lambda a: (counts[a], -ORDER.index(a)))


def kind_of(paths: Iterable[str]) -> str:
    paths = list(paths)
    if paths and all(_is_test(p) for p in paths):
        return "tests"
    if doccheck.docs_only(paths):
        return "docs"
    return "change"


def title(until: float) -> str:
    return time.strftime("# eki %m-%d", time.localtime(until))


def closing(waits: int, verdict: str, prs: int = 0) -> str:
    """The line eki writes itself; open PRs get their own sentence, only when there are some."""
    review = f"  {prs} PR{'' if prs == 1 else 's'} to review." if prs > 0 else ""
    return f"Waits for you: {waits or 'nothing'}.{review}  Score: {verdict}."


def _words(text: str) -> List[str]:
    return text.split()


def words(text: str) -> int:
    """The words the limits count: one- and two-letter words ('a', 'on', 'to')
    are free, so the example's lines fit in fourteen."""
    return sum(1 for w in _words(text) if len(w.strip(".,;:!?()'\"")) > 2)


def _cut(text: str, words: int) -> str:
    """`text` on one line, at most `words` words; a cut one ends in '…'."""
    w = _words(text)
    return " ".join(w) if len(w) <= words else " ".join(w[:words]) + "…"


def rules_page(changes: List[Change], until: float, waits: int, verdict: str, prs: int = 0) -> str:
    """The page eki writes without a model: one line per changed area."""
    kept = [c for c in changes if c.kind != "tests"]
    by_area: Dict[str, List[str]] = {}
    for c in kept:
        by_area.setdefault(c.area if c.area in AREAS else "Engine", []).append(" ".join(_words(c.title)))
    areas = [a for a in AREAS if a in by_area]
    if not kept:
        head = "Nothing new landed."
    else:
        noun = "change" if len(kept) == 1 else "changes"
        head = _cut(f"{len(kept)} {noun} landed: {', '.join(areas)}.", HEADLINE_WORDS)
    out = [title(until), head, ""]
    for a in areas:
        line = "; ".join(t for t in by_area[a] if t) or "changed"
        out += [a, "- " + _cut(line, LINE_WORDS), ""]
    out.append(closing(waits, verdict, prs))
    return "\n".join(out) + "\n"


RULES_TEXT = f"""\
You write the daily patch notes of eki, a program that builds itself. Someone
reads them in twenty seconds, like a game's patch notes.

1. The page. A title line (eki writes it), then one headline: one sentence,
   at most {HEADLINE_WORDS} words, not starting with '-'. Then only the areas
   that changed, in this order: {", ".join(AREAS)}. A section header is the
   bare area name, with no '##'. Its lines start with '- ' and have
   at most {LINE_WORDS} words; at most {SECTION_LINES} lines per section and
   {PAGE_LINES} on the page; Docs has at most {DOCS_LINES} line. A day with one change is a page with one line. eki
   writes the closing line itself, never you.
2. What counts as a change: work on eki itself that landed, went live or was
   applied in the last day. Only those are listed below.
3. Area and kind. Each change's area comes from the files it touched (tests
   are ignored); its kind is 'change', 'docs' (only documentation) or 'tests'
   (only tests). Tests-only changes are not worth a line; docs changes get at
   most one Docs line.
4. The score: eki compares today with yesterday (faults, corrections, work
   done locally) and writes the verdict on the closing line.
5. Waits for you: eki counts what waits for a person now and writes it on the
   closing line.

Say what changed for the person who uses eki, in plain words. Merge small
changes into one line. Never write an id, a file name, or the words item,
goal, gate or worktree.

An example page:

{EXAMPLE}"""


def brief(changes: List[Change]) -> str:
    """The prompt for the model's notes: the rules, the example, the changes, the ask."""
    lines = [f"[{c.area}] ({c.kind}) {' '.join(_words(c.title))}" for c in changes]
    areas = [a for a in AREAS if any(c.area == a and c.kind != "tests" for c in changes)]
    return (RULES_TEXT + "\nToday's changes, one per line as [Area] (kind) title:\n"
            + ("\n".join(lines) or "(none)") + "\n\n"
            + "Answer with exactly two blocks and nothing else.\n\n"
            "NOTES:\n"
            "the headline line, then for each area the header line and its '- ' lines. "
            f"Use only these areas: {', '.join(areas) or '(none: the headline alone)'}. "
            "No title, no closing line, no ids, no file names, and not the words "
            "item, goal, gate or worktree.\n\n"
            "TRIAGE:\n"
            "one line per thing that went wrong or looks odd, as\n"
            "- <what> — fix | watch | ignore — <why>\n"
            'A fix line names the wish as eki self "<the wish>". '
            "If there is none: - nothing — ignore — a quiet day\n")


def check(notes: str, areas: Iterable[str]) -> Optional[str]:
    """None when `notes` keep the page's rules, else what is wrong with them."""
    areas = set(areas)
    lines = [ln.strip() for ln in notes.splitlines() if ln.strip()]
    if not lines or lines[0].startswith("-"):
        return "no headline"
    if words(lines[0]) > HEADLINE_WORDS:
        return f"headline over {HEADLINE_WORDS} words"
    section: Optional[str] = None
    counts: Dict[str, int] = {}
    total = 0
    for ln in lines[1:]:
        if not ln.startswith("- "):
            if ln not in areas:
                return f"section {ln!r} is not one of {', '.join(sorted(areas)) or 'none'}"
            section = ln
            continue
        if section is None:
            return "a line outside any section"
        if words(ln[2:]) > LINE_WORDS:
            return f"a line over {LINE_WORDS} words"
        counts[section] = counts.get(section, 0) + 1
        total += 1
        if counts[section] > SECTION_LINES:
            return f"more than {SECTION_LINES} lines in {section}"
        if section == "Docs" and counts[section] > DOCS_LINES:
            return f"more than {DOCS_LINES} line in Docs"
    if total > PAGE_LINES:
        return f"more than {PAGE_LINES} lines on the page"
    for name, pattern in (("an id", HEX_ID), ("a file name", FILE_NAME), ("an inner word", INNER_WORD)):
        m = pattern.search(notes)
        if m:
            return f"{name}: {m.group(0)!r}"
    return None


def areas_in(page: str) -> List[str]:
    return [ln.strip() for ln in page.splitlines() if ln.strip() in AREAS]


def with_notes(page: str, notes: str) -> str:
    """`page` with its headline and sections replaced by `notes`."""
    lines = page.splitlines()
    head = next((ln for ln in lines if ln.startswith("# ")), "")
    end = next((ln for ln in reversed(lines) if ln.startswith("Waits for you:")), "")
    return f"{head}\n{notes.strip()}\n\n{end}\n"


def headline(page: str) -> str:
    lines = page.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            return lines[i + 1].strip() if i + 1 < len(lines) else ""
    return ""
