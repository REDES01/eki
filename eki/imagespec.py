# SPDX-License-Identifier: Apache-2.0
"""How big, and how many: read off the words of a request for a picture.

"four images of a red fox at 1536x1024" asks for a fox; the rest is about the
paper. Those words are taken out before the image model sees them — a model
handed "four images of a fox" draws four foxes — and come back as numbers the
adapter can put into the graph: a size in pixels, or a shape (16:9, "in
portrait") for it to cut from its own pixel budget, and a count.

Only plain statements count. "4k" is a quality tag people paste into prompts,
"a portrait of a woman" is a subject and "a town square" is a place, so none
of those say anything here; "at 9:16" does, "at 9:16 pm" doesn't.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "a couple of": 2, "a few": 3,
          "一": 1, "两": 2, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}
_N = r"(?P<n>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|a couple of|a few)"
#: what a picture is called, and what another go at one is called
_PICTURE = (r"images?|pictures?|pics?|photos?|photographs?|illustrations?|artworks?|drawings?|"
            r"paintings?|portraits?|wallpapers?|logos?|icons?|posters?|renders?|sketch(?:es)?")
_ANOTHER = r"versions?|variations?|variants?|options?|takes?|alternatives?|candidates?|samples?"
_NOUN = rf"(?P<noun>{_PICTURE}|{_ANOTHER})"

_SIZE = re.compile(
    r"(?:\b(?:at|in|with)\s+)?(?:\b(?:a\s+)?(?:size|resolution|res)\s*(?:of\s+)?[:：]?\s*|尺寸|分辨率|解像度)?"
    r"(?<![\w.])(?P<w>\d{3,4})\s*(?:x|×|\*|by)\s*(?P<h>\d{3,4})(?![\w.])"
    r"(?:\s*(?:px|pixels?|像素|ピクセル))?(?:\s+(?:size|resolution))?", re.I)

_RATIO = re.compile(
    r"(?:\b(?:at|in|with)\s+(?:an?\s+)?)?(?:\b(?:aspect(?:\s+ratio)?|ratio)\s*(?:of\s+)?[:：]?\s*)?"
    r"(?<![\w:.])(?P<a>21|16|9|4|3|2|5|1)\s*[:：]\s*(?P<b>21|16|10|9|4|3|2|5|1)(?![\w:.])"
    r"(?!\s*[ap]\.?m\b)(?:\s+(?:aspect(?:\s+ratio)?|ratio|format))?", re.I)
_SHAPES = [
    (re.compile(r"(?:\bin\s+)?\b(?:portrait|vertical)\s+(?:orientation|format|mode|aspect|layout)\b"
                r"|\bin\s+portrait\b(?!\s+of)|竖版|竖屏|豎版|[、,]?\s*(?:縦長|縦向き)(?:で|に)?", re.I), 3 / 4),
    (re.compile(r"(?:\bin\s+)?\b(?:landscape|horizontal)\s+(?:orientation|format|mode|aspect|layout)\b"
                r"|\bin\s+landscape\b(?!\s+of)|横版|横屏|橫版|[、,]?\s*(?:横長|横向き)(?:で|に)?", re.I), 4 / 3),
    (re.compile(r"(?:\bin\s+)?\bwide-?screen(?:\s+format)?\b", re.I), 16 / 9),
    (re.compile(r"(?:\bin\s+(?:a\s+)?)?\bsquare\s+(?:format|aspect|crop|ratio)\b|正方形", re.I), 1.0),
]

#: "make 4 images of…", "4 pictures of…" at the start, "…, 3 variations" at the end
_MAKE = (r"generate|create|make|draw|paint|render|produce|design|sketch|give\s+me|show\s+me|"
         r"(?:i\s+)?(?:want|need)|(?:i\s+)?(?:would|'d)\s+like")
_EXTRA = r"(?:(?:different|more|new|separate|distinct|unique|other)\s+)*"
_COUNT_LEAD = re.compile(
    rf"(?P<lead>^\W*|\b(?:{_MAKE})\s+(?:me\s+|us\s+)?(?:a\s+batch\s+of\s+|a\s+set\s+of\s+)?)"
    rf"{_N}\s+{_EXTRA}{_NOUN}\b", re.I)
_COUNT_BATCH = re.compile(
    rf"\b(?:in\s+)?(?:a\s+)?batch(?:\s+size)?\s+(?:of\s+)?{_N}\b(?:\s+{_EXTRA}{_NOUN}\b)?", re.I)
_COUNT_TAIL = re.compile(
    rf"[\s,;.，。、]*(?:(?:[x×]\s?(?P<x>\d{{1,2}}))|{_N}\s+{_EXTRA}{_NOUN})\s*(?:,?\s*please)?[\s.!。]*$", re.I)
#: nothing but "four more": the same again, this many times
_COUNT_MORE = re.compile(
    rf"^\W*(?:(?:{_MAKE}|do|try)\s+(?:me\s+)?)?(?:{_N}\s+more|another\s+{_N.replace('<n>', '<n2>')})"
    rf"(?:\s+(?:of\s+(?:them|these|those)|like\s+(?:this|that|these|it)|{_PICTURE}|{_ANOTHER}))?"
    rf"(?:,?\s*please)?\W*$", re.I)
_COUNT_CJK = re.compile(r"(?P<n>\d{1,2}|[一两兩二三四五六七八九十])\s*(?P<unit>张|張|幅|枚)")


@dataclass
class Spec:
    prompt: str = ""                    # the request, with those words taken out
    width: int = 0                      # pixels, when they were given outright
    height: int = 0
    aspect: float = 0.0                 # width / height, when only a shape was given
    batch: int = 0                      # how many; 0 is "not said"

    def as_dict(self) -> Dict[str, Any]:
        """What was actually said, for the adapter and for a later "try again"."""
        out: Dict[str, Any] = {}
        if self.width and self.height:
            out["width"], out["height"] = self.width, self.height
        elif self.aspect:
            out["aspect"] = round(self.aspect, 4)
        if self.batch:
            out["batch"] = self.batch
        return out


def _number(text: Optional[str]) -> int:
    if not text:
        return 0
    text = " ".join(text.lower().split())
    return int(text) if text.isdigit() else _WORDS.get(text, 0)


def _one(noun: str) -> str:
    """"images" → "an image"; "variations" → "an image" too: the model is
    asked for the one picture, and the count is eki's to honour."""
    word = noun.lower()
    if re.fullmatch(_ANOTHER, word, re.I):
        word = "image"
    elif word.endswith("ches"):
        word = word[:-2]
    elif word.endswith("s"):
        word = word[:-1]
    return ("an " if word[0] in "aeiou" else "a ") + word


def _tidy(text: str) -> str:
    text = re.sub(r"\s+([,.;!?])", r"\1", text)
    text = re.sub(r"([,;])\s*(?=[,.;])", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,;，、")


def read(prompt: str) -> Spec:
    """The size, shape and count a request states, and the request without them."""
    spec = Spec()
    text = prompt

    m = _SIZE.search(text)
    if m:
        spec.width, spec.height = int(m["w"]), int(m["h"])
        text = text[:m.start()] + " " + text[m.end():]
    if not spec.width:
        m = _RATIO.search(text)
        if m and int(m["b"]) and 0.4 <= int(m["a"]) / int(m["b"]) <= 2.5:
            spec.aspect = int(m["a"]) / int(m["b"])
            text = text[:m.start()] + " " + text[m.end():]
    for pattern, shape in _SHAPES:
        m = pattern.search(text)
        if m:
            if not spec.width and not spec.aspect:
                spec.aspect = shape
            text = text[:m.start()] + " " + text[m.end():]
            break

    m = _COUNT_MORE.match(text)
    if m:
        spec.batch = _number(m["n"] or m["n2"])
        text = ""
    if not spec.batch:
        m = _COUNT_BATCH.search(text)
        if m and _number(m["n"]):
            spec.batch = _number(m["n"])
            text = text[:m.start()] + (_one(m["noun"]) if m["noun"] else " ") + text[m.end():]
    if not spec.batch:
        m = _COUNT_LEAD.search(text)
        if m and _number(m["n"]):
            spec.batch = _number(m["n"])
            text = text[:m.start()] + m["lead"] + _one(m["noun"]) + text[m.end():]
    if not spec.batch:
        m = _COUNT_TAIL.search(text)
        if m and (m["x"] or _number(m["n"])):
            spec.batch = int(m["x"]) if m["x"] else _number(m["n"])
            text = text[:m.start()]
    if not spec.batch:
        m = _COUNT_CJK.search(text)
        if m:
            spec.batch = _number(m["n"])
            text = text[:m.start()] + ("1枚" if m["unit"] == "枚" else "一" + m["unit"]) + text[m.end():]

    spec.prompt = _tidy(text)
    return spec
