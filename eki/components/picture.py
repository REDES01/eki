"""Picture cards: a service card whose target is a ComfyUI workflow.

    {"kind": "service", "about": "…", "target": "comfyui:<workflow>", "out": "assets/generated/<card>/"}

A call (`eki use <card> …`) takes `--prompt T` (or bare words), `--negative T`,
`--seed N`, `--size WxH` (width and height), `--source <picture>` (the
picture to work on, relative to the caller's folder) and `--<input> value`
for any other input the workflow declares (comfyui_workflows.describe). An
input the workflow doesn't have is refused before ComfyUI is touched. The
call is one turn of the target's provider with the workflow as its model and
the inputs as `{"inputs": {...}}`; each picture is then copied into `out`,
relative to the project folder that holds the card (a pool card with no
project: ~/.eki/images/<card>/), and the copies are the call's paths.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import paths, targets
from . import Call, Component, Result

#: the input a flag sets when its name isn't the input's own
FLAGS = {"source": "image"}
NUMBERS = ("seed", "width", "height")


def workflow(spec: dict) -> Tuple[str, str]:
    """(provider, workflow id) of a picture card; ValueError when the target names none."""
    t = targets.parse(spec.get("target"))
    from ..providers import comfyui_workflows as wf
    return t.provider, (t.model or t.tier or wf.DEFAULT)


def _describe(spec: dict, look: bool = False) -> Dict[str, Any]:
    from ..providers import comfyui_workflows as wf
    _, wid = workflow(spec)
    return wf.describe(wid, look=look)


def flag(name: str) -> str:
    return "--source" if name == "image" else f"--{name}"


def out_dir(comp: Component) -> Path:
    """Where the card's pictures go: `out` under the project folder holding its components/."""
    card = comp.card or (comp.folder if comp.scope == "project" else None)
    if card is None:
        return paths.home() / "images" / comp.name
    return Path(card).parent.parent / str(comp.spec.get("out") or f"assets/generated/{comp.name}/")


def _takes(names: List[str]) -> str:
    return ", ".join("source" if n == "image" else n for n in names) or "nothing"


def parse_args(comp: Component, args: List[str], names: List[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    """(inputs, source path as written) from the call's words; ValueError for anything the workflow lacks."""
    got: Dict[str, Any] = {}
    words: List[str] = []
    source: Optional[str] = None
    i = 0
    while i < len(args):
        a = str(args[i])
        if not a.startswith("--") or a == "--":
            if a != "--":
                words.append(a)
            i += 1
            continue
        key, eq, val = a[2:].partition("=")
        if not eq:
            if i + 1 >= len(args):
                raise ValueError(f"{a} needs a value")
            val, i = str(args[i + 1]), i + 1
        i += 1
        if key == "size":
            m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", val)
            if not ("width" in names and "height" in names):
                raise ValueError(f"unknown input --size; {comp.name} takes: {_takes(names)}")
            if not m:
                raise ValueError(f"--size takes WxH, e.g. 1024x768 (got {val!r})")
            got["width"], got["height"] = int(m.group(1)), int(m.group(2))
            continue
        name = FLAGS.get(key, key)
        if name not in names or key == "image":
            raise ValueError(f"unknown input --{key}; {comp.name} takes: {_takes(names)}")
        if name == "image":
            source = val
            continue
        if name in NUMBERS:
            if not re.fullmatch(r"-?\d+", val.strip()):
                raise ValueError(f"--{key} takes a whole number (got {val!r})")
            got[name] = int(val)
        else:
            got[name] = val
    if words:
        if "prompt" not in names:
            raise ValueError(f"unknown input --prompt; {comp.name} takes: {_takes(names)}")
        if "prompt" in got:
            raise ValueError("give the prompt once: as --prompt or as bare words, not both")
        got["prompt"] = " ".join(words)
    return got, source


def _copy(made: List[str], dest: Path) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for p in made:
        src = Path(p)
        to, n = dest / src.name, 2
        while to.exists():
            to, n = dest / f"{src.stem}-{n}{src.suffix}", n + 1
        shutil.copy2(src, to)
        out.append(str(to.resolve()))
    return out


def call(comp: Component, c: Call) -> Result:
    from . import service
    try:
        provider, wid = workflow(comp.spec)
        info = _describe(comp.spec)
    except (ValueError, KeyError) as e:
        return Result(error=f"{comp.name}: {e.args[0] if e.args else e}")
    if info["error"]:
        return Result(error=f"{comp.name}: {info['error']}")
    names = [i["name"] for i in info["inputs"]]
    try:
        inputs, source = parse_args(comp, list(c.args), names)
    except ValueError as e:
        return Result(error=str(e))
    images: List[str] = []
    if source is not None:
        f = Path(os.path.expanduser(source))
        f = f if f.is_absolute() else Path(c.caller_dir or ".") / f
        if not f.is_file():
            return Result(error=f"{comp.name}: no picture at {f}")
        images = [str(f.resolve())]
    elif info["needs_source"]:
        return Result(error=f"{comp.name} works on a picture: give --source <picture>")
    got = service.provider_turn(comp, c, provider, json.dumps({"inputs": inputs}), {"model": wid}, images)
    if got.error:
        return got
    try:
        copies = _copy(got.paths, out_dir(comp))
    except OSError as e:
        return Result(text=got.text, paths=got.paths, error=f"{comp.name}: can't save into {out_dir(comp)}: {e}")
    n = len(copies)
    return Result(text=f"{info['label']}: {n} picture{'s' if n != 1 else ''} saved into {out_dir(comp)}",
                  paths=copies)


def _default(v: Any) -> str:
    return "random" if v == "random" else ('""' if v in ("", None) else str(v))


def help_text(comp: Component) -> str:
    """What `eki use <card> --help` prints: about, workflow, inputs, models, out folder."""
    spec = comp.spec
    lines = [f"{comp.name} — {spec.get('about', '')}"]
    try:
        _, wid = workflow(spec)
        info = _describe(spec, look=True)
    except (ValueError, KeyError) as e:
        lines.append(f"target {spec.get('target')!r}: {e.args[0] if e.args else e}")
        return "\n".join(lines) + "\n"
    lines.append(f"draws with {targets.key(spec.get('target'))} ({info['label']})")
    if info["error"]:
        lines.append(info["error"])
    names = [i["name"] for i in info["inputs"]]
    usage = [f"eki use {comp.name} " + ("\"<prompt>\" " if "prompt" in names else "")
             + " ".join(f"[{flag(n)} …]" for n in names if n != "prompt")]
    if "width" in names and "height" in names:
        usage[0] += " [--size WxH]"
    lines += ["", "usage: " + usage[0], "", "inputs:"]
    for i in info["inputs"]:
        n = i["name"]
        what = "the picture to work on (a file)" if n == "image" else f"default {_default(i['default'])}"
        extra = " (or bare words)" if n == "prompt" else ""
        lines.append(f"  {flag(n):<14} {n:<10} {what}{extra}")
    if info["models"]:
        lines += ["", "models:"]
        for m in info["models"]:
            seen = {True: "found", False: "missing in ComfyUI's model folders", None: "not checked"}[m["found"]]
            lines.append(f"  {m['name']} — {seen}")
    lines += ["", f"saves into: {out_dir(comp)}"]
    return "\n".join(lines) + "\n"
