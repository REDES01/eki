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

from . import imagespec

TASKS = ("chat", "writing", "translate", "code", "repo", "math", "research", "image", "screen")
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
    #: has to be done, not answered: goes to a harness whatever the task
    hands: bool = False

    def to_json(self) -> Dict[str, Any]:
        return {"task": self.task, "difficulty": self.difficulty,
                "source": self.source, "confidence": self.confidence, "ms": self.ms,
                "hands": self.hands}

    @property
    def short(self) -> str:
        return f"{self.task} · {self.difficulty}"


_IMAGE = re.compile(r"\b(draw|paint(ing)?|render|illustration|logo|watercolou?r|sketch|"
                    r"generate an image|image of|picture of|photo of|comic|poster|wallpaper|"
                    r"isometric|icon of|product shot|cinematic)\b", re.I)
#: Saying it outright — "generate an image…", "make me a picture of…", "画一只猫".
#: A verb of making, then a word for a picture a few words later. This one is a
#: fact about the request rather than a guess, so the model's label never
#: overrides it.
_MAKE_IMAGE = re.compile(
    r"\b(generat(e|es|ing|ion)|creat(e|ing)|make|making|produce|draw|paint|render|"
    r"give me|show me|i (want|need|would like|'d like))\b"
    r"(?:\W+(?!(?:the|this|that|these|those|my|your|our|its)\b)\w+){0,4}?\W+"
    r"(?<!\bthe )(?<!\bthis )(?<!\bthat )(?<!\bmy )"
    r"(images?|pictures?|pics?|photos?|illustrations?|artworks?|drawings?|paintings?|"
    r"portraits?|wallpapers?|logos?|icons?|posters?)\b"
    r"|^\s*/?(image|img|picture)\s*[:：]"
    r"|(生成|画|做|来|給我|给我|创建).{0,8}(图|圖|画像|照片|插画|壁纸)"
    r"|(画像|イラスト|絵|写真).{0,6}(生成|作って|描いて|作成|ください)"
    r"|^\s*画(一|个|張|张|只|幅)", re.I)
#: …unless the picture is the *input*: a thing to read, resize or talk about
_ABOUT_IMAGE = re.compile(
    r"\b(describe|caption|explain|analy[sz]e|read|ocr|resize|compress|crop|convert|"
    r"upload|load|parse|what('s| is) in)\b(?:\W+\w+){0,3}?\W+(this|that|the|my|an?)\W+"
    r"(image|picture|photo|pic)\b", re.I)


def asks_for_image(prompt: str) -> bool:
    """True when the request says, in words, that the answer is a picture."""
    text = prompt.strip()
    if _CODE.search(text) or _ABOUT_IMAGE.search(text):
        return False
    return bool(_MAKE_IMAGE.search(text))


#: A picture was the last thing in the thread, and this reads as a change to
#: it: "make it bluer", "remove the hat", "now at sunset", "换成蓝色". Only ever
#: consulted when the previous answer *was* a picture — on their own these
#: words mean nothing of the sort.
_EDIT_IMAGE = re.compile(
    r"\b(make (it|that|this|them|her|him|the \w+)|change|replace|swap|remove|erase|"
    r"delete|get rid of|add|put|give (it|him|her|them)|turn (it|the|this|that)|"
    r"recolou?r|brighten|darken|lighten|sharpen|blur|zoom|crop|rotate|flip|mirror|"
    r"same (but|image|picture|thing|one|scene|style)|but (with|in|at|on|make)|"
    r"with(out)? (a|an|the|more|less|no|some)|instead|(more|less|fewer|no) \w+|"
    r"background|foreground|lighting|colou?rs?|style|"
    r"bluer|redder|greener|darker|lighter|brighter|warmer|cooler|softer|sharper|"
    r"bigger|smaller|larger|closer|wider|taller|shorter|older|younger|happier|cuter|"
    r"realistic|cartoon|anime|night|daytime|sunset|sunrise)\b"
    r"|^\s*(ok(ay)?[, ]+)?(now|and|also|but|then)\b"
    r"|改|换|換|变成|變成|再来|再來|再画|更|加上|加个|去掉|删掉|刪掉|背景|颜色|顏色"
    r"|もっと|にして|変えて|消して|追加|足して|もう一|背景|色を", re.I)
#: nothing but "again": the same request, another roll of the dice
_REDO_IMAGE = re.compile(
    r"^\W*(please )?(try again|again|once more|one more( time)?|redo( it)?|regenerate( it)?|"
    r"(give me |make |show me |do |try )?(another|a different|a new)"
    r"( one| version| take| variation| try)?|再来一(张|張|次|个)|再画一(张|張)|重新(生成|画)|"
    r"もう一(枚|度|回)|やり直して?)(,? please)?\W*$", re.I)
#: all that is left of "same but 16:9" or "now in portrait" once the paper is set aside
_SAME_AGAIN = re.compile(r"^\W*((the )?same( (one|thing|image|picture|prompt))?|again|now|but|and|also|"
                         r"this time|please|同样|同樣|一样|一樣|同じ|\s)*\W*$", re.I)
_BACKREF = re.compile(r"\b(same|it|this one|that one|the (last|previous) (one|image|picture))\b"
                      r"|这张|這張|那张|那張|刚才|剛才|さっき|この|その", re.I)
_QUESTION = re.compile(r"^\s*(what|why|how|which|who|where|when|is|are|was|does|did|do)\b", re.I)


def image_followup(prompt: str) -> str:
    """What this says about the picture just shown: "edit", "redo", or "".

    The caller has established that a picture *was* just shown; this only
    reads the words. A request that names a new picture outright ("generate an
    image of a dog") is a fresh one unless it points back at the last ("the
    same cat, but…")."""
    text = prompt.strip()
    if not text or len(text) > 400:
        return ""
    # "4 more", "try again at 1280x720", "same but 16:9": the size and the
    # count are about the paper; what is left says which of the two this is
    said = imagespec.read(text)
    if said.as_dict():
        if _SAME_AGAIN.match(said.prompt):
            return "redo"
        text = said.prompt
    if _REDO_IMAGE.match(text):
        return "redo"
    if _CODE.search(text) or _ABOUT_IMAGE.search(text) or _QUESTION.match(text):
        return ""
    if not _EDIT_IMAGE.search(text):
        return ""
    if asks_for_image(text) and not _BACKREF.search(text):
        return ""
    return "edit"


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
_CODE = re.compile(r"\b(refactor\w*|modules?|callers?|codebase|call sites?|unit tests?|linter|lint|"
                   r"dependency injection|stack overflow|null pointer|segfault|"
                   r"python|javascript|typescript|swiftui|swift|rust|golang|goroutines?|java|"
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
#: making a thing: "create an interactive RPG game", "build me a website"
_BUILD = re.compile(r"\b(create|build|make|develop|code|program|implement|set up|scaffold|prototype|write)\b"
                    r"(?:\W+(?!(?:sure|it|this|that|them)\b)\w+){0,4}?"
                    r"\W+(apps?|applications?|games?|websites?|sites?|web ?apps?|platform|engine|backend|frontend"
                    r"|server|dashboard|simulator|saas|clone|mvp|tools?|bots?|cli|extensions?|plugins?|scripts?"
                    r"|programs?|services?|library|api)\b", re.I)
_BIG_BUILD = re.compile(r"\b(apps?|applications?|games?|websites?|sites?|web ?apps?|platform|engine|backend"
                        r"|frontend|server|dashboard|simulator|saas|clone|mvp|services?)\b", re.I)
_SIMPLE = re.compile(r"\b(explain|compare|why|walk me through|plan|design|help me|"
                     r"what are the|how does|critique)\b", re.I)
_EASY = re.compile(r"^(hi|hey|hello|thanks|thank you|yes|no|ok(ay)?)\b", re.I)
#: The screen of this Mac: looking at it or driving it. Needs a program with
#: tools (Claude Code, Codex with eki's screen tools), never a bare model —
#: which would answer that it has no hands, and did.
_SCREEN = re.compile(
    r"\b(take|grab|capture|get|make) (a |me a |the )?(screen ?shot|screen capture|screengrab)\b"
    r"|\bscreen ?shot\b.*\b(this|my|the) (screen|display|window|mac)\b"
    r"|^\s*(take )?screen ?shot\s*$"
    r"|\b(what('s| is) (on|in) (my|the) (screen|display|window)|see my screen|look at (my|the) screen)\b"
    r"|\b(click|double.?click|right.?click|press|type|scroll)\b.*\b(button|window|app|screen|menu|"
    r"field|tab|dialog|finder|safari|chrome|xcode|simulator)\b"
    r"|\b(open|launch|switch to|bring up|quit) (the app |the )?(finder|safari|chrome|xcode|"
    r"simulator|terminal|notes|mail|calendar|music|photos|system settings)\b"
    r"|\b(control|use|drive|operate) (my|the) (mac|computer|desktop|screen)\b"
    r"|截图|截屏|スクリーンショット|画面を(撮|見)", re.I)


def wants_screen(text: str) -> bool:
    return bool(_SCREEN.search(text))


#: Anything that has to *act* rather than answer: run or install something,
#: read or change files here, fetch a page, send something, drive an app.
#: A bare model would describe the steps; a harness does them.
_ACT = re.compile(
    r"\b(run|execute|install|uninstall|build|compile|deploy|restart|start|stop|kill|launch)\b"
    r"\s+(the |my |a |this |that |it\b|`|[\w./-]+)"
    r"|\b(ls|cat|grep|find|curl|wget|git|npm|pip|brew|docker|pytest|node|python3?)\s+[\w./-]"
    # the command, not the verb: "make test", never "make it shorter"
    r"|(?:^|`|\$\s?)make\s+(test|tests|build|install|clean|all|run|lint|check|dev|release)\b"
    r"|\b(list|show|read|open|check|look at|inspect|delete|remove|rename|move|copy|create|"
    r"write|save|edit|change|update)\s+(the |my |this |that |a |all )?(old |new |stale |big |large )?"
    r"(files?|folders?|directory|directories|logs?|config|downloads|desktop|documents|"
    r"\w+\.(py|ts|js|json|ya?ml|md|txt|swift|toml|log|csv|sh))\b"
    r"|\b(in|from|on) (my|the|this) (mac|computer|machine|laptop|disk|home (folder|directory)|"
    r"desktop|downloads folder)\b"
    r"|\b(fetch|download|scrape|crawl|browse to|go to|visit|load) (the |this |that )?"
    r"(url|page|site|website|link|https?://)"
    r"|https?://\S+"
    r"|\b(send|post|email|message|tweet|upload|publish) (it|this|that|the|an?|my)\b"
    r"|\b(what('s| is) (running|listening|using|taking)|which process|disk (space|usage)|"
    r"free space|how much (memory|ram|disk))\b"
    r"|\b(my|the) (clipboard|calendar|mail|inbox|notes|reminders)\b"
    r"|\bcommit\b.*\b(changes?|this|it)\b|\bpush (it|this|the branch|to (origin|main|github))\b"
    r"|运行|执行|安装|打开.{0,6}(文件|文件夹|应用)|读取|删除|下载|实行|実行|インストール|ファイルを", re.I)
#: Asking *about* doing, not to do: "how do I", "what does … do", "explain",
#: "should I", "write a script that…" (the script is the answer).
_ABOUT = re.compile(r"^\s*(how (do|would|can|should) (i|you|we)|what (does|is|are|would)|why|"
                    r"explain|describe|should (i|we)|is it (safe|ok|possible)|can you explain|"
                    r"tell me (about|how)|when (should|do))\b"
                    r"|\b(write|give me|show me) (a|an|the|some) (script|command|snippet|example|"
                    r"one-?liner|function|regex)\b", re.I)


def needs_hands(text: str) -> bool:
    """True when the request has to be *done* on this Mac or the network,
    not answered — it then goes to a harness (Claude Code, Codex, a local
    model with Codex's hands), whatever else it is labelled."""
    t = text.strip()
    if wants_screen(t):
        return True
    if _MINE.search(t):
        return True                 # a question about *this* Mac has to be looked at, not answered
    if _ABOUT.search(t):
        return False
    return bool(_ACT.search(t))


#: things on this Mac: "what is taking up space in my downloads folder"
_MINE = re.compile(r"\bmy (downloads|desktop|documents|files?|folders?|disk|drive|ssd|storage|mac|macbook"
                   r"|computer|laptop|photos|apps|applications|home folder|repo|project|screen|battery|wi-?fi)\b"
                   r"|\b(this|the) (mac|computer|machine|folder|repo)\b|\bon (my|this) (mac|computer|machine)\b"
                   r"|\b(disk space|free space|taking up space|running processes|what'?s running)\b", re.I)


def rules(prompt: str, has_folder: bool = False, after_image: bool = False) -> Label:
    """The obvious cases, for free. Order is the whole design: a request that
    names a folder is a repo change whatever else it says.

    ``after_image`` says the last answer in the thread was a picture, which
    is what makes "make it bluer" an image request rather than small talk."""
    text = prompt.strip()
    if has_folder:
        task = "repo"
    elif wants_screen(text):
        task = "screen"
    elif _REPO.search(text) or (_FILE.search(text) and _REPO_VERB.search(text)):
        task = "repo"
    elif asks_for_image(text) or (_IMAGE.search(text) and not _CODE.search(text) and not _BUILD.search(text)):
        task = "image"
    elif _BUILD.search(text) and not _ABOUT.search(text):
        # making an app, a game, a site, a tool: code, and big unless it's a script
        return Label(task="code", difficulty="hard" if _BIG_BUILD.search(_BUILD.search(text).group(0)) else "medium",
                     source="rules", hands=True)
    elif after_image and image_followup(text):
        task = "image"
    elif re.search(r"^\W*(please\s+)?translate\b|\btranslate (this|that|it|the following)\b|翻译|how do you say",
                   text, re.I):
        # saying "translate" outright beats a word like "today" in the text
        task = "translate"
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
    return Label(task=task, difficulty=_difficulty(text), source="rules",
                 hands=task in ("repo", "screen") or (task != "image" and needs_hands(text)))


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
    "screen - look at or drive this computer's screen\n"
    "difficulty is easy (a small model can do it well), medium, or hard (needs the "
    "strongest model available)."
)
SHOTS = [
    ("say hi in three words", '{"task": "chat", "difficulty": "easy", "hands": false}'),
    ("what's taking up space in my downloads folder",
     '{"task": "chat", "difficulty": "easy", "hands": true}'),
    ("how do I list the biggest files in a folder with du",
     '{"task": "code", "difficulty": "easy", "hands": false}'),
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
                 confidence=max(0.0, min(1.0, confidence)),
                 hands=bool(data.get("hands")) or task in ("repo", "screen"))


class Classifier:
    """Rules always; the model when one is configured and keeping up."""

    def __init__(self, model: Optional[ModelClassifier] = None, use_model: bool = True):
        self.model = model
        self.use_model = use_model

    async def label(self, prompt: str, has_folder: bool = False,
                    after_image: bool = False) -> Label:
        fallback = rules(prompt, has_folder, after_image)
        if not (self.use_model and self.model) or self.model.misses >= 3:
            return fallback
        got = await self.model.label(prompt)
        if got is None:
            return fallback
        if has_folder:
            got.task = "repo"           # the folder is a fact, not an opinion
        elif wants_screen(prompt):
            got.task = "screen"         # and so is "take a screenshot"
        elif asks_for_image(prompt):
            got.task = "image"          # so is "generate an image of…"
        elif after_image and image_followup(prompt):
            got.task = "image"          # and "make it bluer", said to a picture
        if got.task in ("repo", "screen") or (got.task != "image" and needs_hands(prompt)):
            got.hands = True            # the words ask for doing; the model may add cases
        return got
