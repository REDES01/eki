# SPDX-License-Identifier: Apache-2.0
"""What kind of request is this, and how hard is it?

The router needs two things a cost table can't tell it: what the request is
(a translation, a repo change, a picture) and roughly how demanding it is.
Both come from here, twice over — a rule reader that costs nothing and is
never wrong about the obvious cases, and, when a small local model is set up
for the job, a classifier that catches what the rules miss.

The model is given a deadline, not a promise. If it isn't loaded, isn't fast
enough or answers with something that isn't one of the labels, the rules'
answer is used and the request moves on. Labelling must never be the reason
an answer is slow.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

TASKS = ("chat", "writing", "translate", "code", "repo", "math", "research", "image")
DIFFICULTIES = ("easy", "medium", "hard")
#: past this, the rules' answer is used instead — a label is not worth a wait
DEADLINE = 0.9


@dataclass
class Label:
    task: str = "chat"
    difficulty: str = "medium"
    source: str = "rules"           # rules | model
    confidence: float = 0.0
    ms: int = 0

    def to_json(self) -> Dict[str, Any]:
        return {"task": self.task, "difficulty": self.difficulty,
                "source": self.source, "confidence": self.confidence, "ms": self.ms}

    @property
    def short(self) -> str:
        return f"{self.task} · {self.difficulty}"


_IMAGE = re.compile(r"\b(draw|paint(ing)?|render|illustration|logo|watercolou?r|sketch|"
                    r"generate an image|image of|picture of|photo of|comic|poster|wallpaper|"
                    r"isometric|icon of|product shot|cinematic)\b", re.I)
_FILE = re.compile(r"\b[\w\-./]+\.(py|ts|tsx|js|jsx|json|ya?ml|md|txt|swift|go|rs|toml|cfg|lock)\b")
_REPO_VERB = re.compile(r"\b(add|create|fix|bump|update|rename|remove|delete|move|migrate|"
                        r"refactor|run|split|upgrade|implement|convert|set up|format)\b", re.I)
_REPO = re.compile(r"\b(this|the|my) (repo|repository|project|codebase|folder|app|codebase)\b"
                   r"|\bin src/|\bon CI\b|\bthe build is failing\b|\brun the tests\b"
                   r"|\b(in|to|for|of) (this|the) (project|repo|repository|codebase|folder|app)\b",
                   re.I)
_TRANSLATE = re.compile(r"\btranslate\b|\bback.translate\b|\blocali[sz]e\b|how do you say"
                        r"|what does ['\"\u2018\u201c]?[\w\s]+['\"\u2019\u201d]? mean"
                        r"|翻译|\binto (japanese|spanish|french|german|chinese|korean|italian|"
                        r"portuguese|english)\b|\bin (japanese|spanish|french|german|chinese|"
                        r"korean|italian|portuguese)\b", re.I)
_MATH = re.compile(r"\b(prove|proof|show that|derive|derivative|integral|integrate|probability|"
                   r"expected value|solve|equation|factorial|permutations?|eigenvalue|"
                   r"calculate|compute|average of|sum of|area of|how many (ways|seconds|"
                   r"minutes|hours|days)\b|is \d+ prime|count from|percent|tip on)\b"
                   r"|\b\d+\s*[\+\-\*/x×^]\s*\d+|\bwhat('s| is) \d|\d+ ?% |\bconvert \d",
                   re.I)
_RESEARCH = re.compile(r"\b(latest|current(ly)?|right now|today|this week|recent(ly)?|news|"
                       r"reviews?|price of|who is the current|look up|search|cite|sources|"
                       r"literature review|competitive landscape|due.diligence|research\b|"
                       r"weather|store hours|come out|what are people saying)\b", re.I)
_CODE = re.compile(r"\b(python|javascript|typescript|swiftui|swift|rust|golang|goroutines?|java|"
                   r"sql|regex|bash|shell|docker(file)?|kubernetes|git|css|html|div|react|"
                   r"api|endpoint|middleware|function|class|script|compile|stack trace|"
                   r"traceback|typeerror|npm|pip|pandas|node|express|postgres|schema|kernel|"
                   r"async|await|for loop|list comprehension|cache|interpreter|callback|"
                   r"sharding|race condition|unit tests?|integration tests?)\b|```|\bCLI\b", re.I)
_WRITE = re.compile(r"\b(write|draft|rewrite|edit|proofread|summari[sz]e|email|essay|blog|"
                    r"story|poem|haiku|sonnet|limerick|toast|speech|post|caption|headline|"
                    r"subject line|cover letter|outline|op-ed|screenplay|names? for|"
                    r"fix the grammar|shorter|status update)\b", re.I)
_HARD = re.compile(r"\b(design|architecture|migrat\w*|refactor|upgrade|split|end.to.end|"
                   r"prove|proof|rigorous|comprehensive|literature review|due.diligence|"
                   r"strategy|tradeoffs?|analy[sz]e|critique|steelman|implement|derive|"
                   r"exactly-once|lock.free|interpreter|kernel|memory leak|investigate|"
                   r"legal|contract|patent|survey|consistent (character|across)|"
                   r"preserving|non.linear|in the style of|grant proposal|executive summary)\b"
                   r"|\b\d{3,4}[- ]word|\bmap the current state\b", re.I)
_SIMPLE = re.compile(r"\b(explain|compare|why|walk me through|plan|design|help me|"
                     r"what are the|how does|critique)\b", re.I)
_EASY = re.compile(r"^(hi|hey|hello|thanks|thank you|yes|no|ok(ay)?)\b", re.I)


def rules(prompt: str, has_folder: bool = False) -> Label:
    """The obvious cases, for free. Order is the whole design: a request that
    names a folder is a repo change whatever else it says."""
    text = prompt.strip()
    if has_folder:
        task = "repo"
    elif _REPO.search(text) or (_FILE.search(text) and _REPO_VERB.search(text)):
        task = "repo"
    elif _IMAGE.search(text) and not _CODE.search(text):
        task = "image"
    elif _RESEARCH.search(text):
        task = "research"
    elif _CODE.search(text):
        task = "code"
    elif _TRANSLATE.search(text):
        task = "translate"
    elif _MATH.search(text):
        task = "math"
    elif _WRITE.search(text):
        task = "writing"
    else:
        task = "chat"
    return Label(task=task, difficulty=_difficulty(text), source="rules")


def _difficulty(text: str) -> str:
    """Length is a decent proxy: people write more when they want more.

    Wording beats length in both directions — "prove" is hard however short,
    and a long paste that only asks for a rewrite is not."""
    hard_words = bool(_HARD.search(text))
    if _EASY.match(text):
        return "easy"
    if hard_words or len(text) > 140 or text.count("?") >= 3:
        return "hard"
    if len(text) <= 80 and not _SIMPLE.search(text):
        return "easy"
    return "medium"


SYSTEM = (
    "You label the user's request for a router. Answer with JSON only, no prose:\n"
    '{"task": "...", "difficulty": "..."}\n'
    "task is one of:\n"
    "chat - questions, explanations, advice, opinions\n"
    "writing - compose or edit prose: emails, posts, stories, summaries\n"
    "translate - between human languages\n"
    "code - write or explain code with no project of the user's involved\n"
    "repo - touch the user's own project: their repo, folder, files, tests, build. "
    "If the request mentions this repo/project/app/folder or a filename in it, "
    "it is repo, not code.\n"
    "math - calculate, solve or prove\n"
    "research - needs current facts, prices, news or sources looked up\n"
    "image - make a picture\n"
    "difficulty is easy (a small model can do it well), medium, or hard (needs the "
    "strongest model available)."
)
SHOTS = [
    ("say hi in three words", '{"task": "chat", "difficulty": "easy"}'),
    ("add a --dry-run flag to the CLI in this project",
     '{"task": "repo", "difficulty": "medium"}'),
    ("write a python function that reverses a string",
     '{"task": "code", "difficulty": "easy"}'),
    ("the build is failing on CI with a type error in utils.ts, find it and fix it",
     '{"task": "repo", "difficulty": "medium"}'),
    ("explain how a heat pump works, for someone deciding whether to buy one",
     '{"task": "chat", "difficulty": "medium"}'),
    ("prove that there are infinitely many primes of the form 4k+3",
     '{"task": "math", "difficulty": "hard"}'),
    ("translate to French: I'll be ten minutes late",
     '{"task": "translate", "difficulty": "easy"}'),
]


class ModelClassifier:
    """A small local model, asked for two words and given a deadline."""

    def __init__(self, base_url: str, model: str = "", deadline: float = DEADLINE):
        self.base = base_url.rstrip("/")
        self.model = model
        self.deadline = deadline
        self.misses = 0                 # consecutive failures; 3 stands it down

    async def label(self, prompt: str) -> Optional[Label]:
        messages = [{"role": "system", "content": SYSTEM}]
        for user, answer in SHOTS:
            messages += [{"role": "user", "content": user},
                         {"role": "assistant", "content": answer}]
        messages.append({"role": "user", "content": prompt[:1500]})
        body: Dict[str, Any] = {"messages": messages, "max_tokens": 32,
                                "temperature": 0.0, "stream": False}
        if self.model:
            body["model"] = self.model
        started = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.deadline) as c:
                r = await c.post(f"{self.base}/v1/chat/completions", json=body)
            if r.status_code != 200:
                raise httpx.HTTPError(f"HTTP {r.status_code}")
            content = r.json()["choices"][0]["message"].get("content") or ""
        except (httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError, IndexError):
            self.misses += 1
            return None
        label = parse(content)
        if label is None:
            self.misses += 1
            return None
        self.misses = 0
        label.ms = int((time.time() - started) * 1000)
        return label


def parse(content: str) -> Optional[Label]:
    """Take the first JSON object with labels we recognise, and nothing else."""
    match = re.search(r"\{[^{}]*\}", content, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    task = str(data.get("task", "")).strip().lower()
    difficulty = str(data.get("difficulty", "")).strip().lower()
    if task not in TASKS or difficulty not in DIFFICULTIES:
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return Label(task=task, difficulty=difficulty, source="model",
                 confidence=max(0.0, min(1.0, confidence)))


class Classifier:
    """Rules always; the model when one is configured and keeping up."""

    def __init__(self, model: Optional[ModelClassifier] = None, use_model: bool = True):
        self.model = model
        self.use_model = use_model

    async def label(self, prompt: str, has_folder: bool = False) -> Label:
        fallback = rules(prompt, has_folder)
        if not (self.use_model and self.model) or self.model.misses >= 3:
            return fallback
        got = await self.model.label(prompt)
        if got is None:
            return fallback
        if has_folder:
            got.task = "repo"           # the folder is a fact, not an opinion
        return got
