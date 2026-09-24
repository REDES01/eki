# SPDX-License-Identifier: Apache-2.0
"""Capability commands: `eki image`, `eki write` — one per kind of work —
and `eki capabilities`, what this Mac can do right now.

`eki ask` is for a person watching a terminal: it streams, it can be let go
of, it prints the route. These are for an agent with a shell that wants a
thing made (ROADMAP, Stage 4): they block until the work is done, print
nothing but the path of what was made (or one JSON object with `--json`),
and say how it went in the exit code:

    0  made; the path is printed
    1  the run failed or was cancelled
    3  the engine couldn't be reached or refused the request
    4  the run finished but made nothing (no picture in the answer)
    5  it took longer than --timeout (the run carries on in the engine)

`eki capabilities` is what an agent reads before these: every backend the
engine has, whether it is up, what it does, and the command that reaches
it — plus how deep the caller already is, since past the limit eki refuses
(eki/nesting.py). Exit 0, or 3 when the engine can't be reached.

Files are the handoff: what is made lands where `-o` says — the current
folder by default — never over a file that is already there, and the path
is what gets passed on.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from . import nesting

OK, FAILED, UNREACHABLE, NOTHING, TIMEOUT = 0, 1, 3, 4, 5
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+\.(?:png|jpe?g|webp|gif))\)", re.I)


class Failure(Exception):
    def __init__(self, code: int, message: str, run: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.run = run or {}


# ---- the run -----------------------------------------------------------

def run_and_wait(service: str, body: Dict[str, Any], timeout: float,
                 ensure: Callable[[str], None]) -> Tuple[Dict[str, Any], str, str]:
    """Start one run and wait for it to end: (run, conversation, answer)."""
    if os.environ.get("EKI_INSIDE"):
        body["via"] = "agent"                  # a program's request, not the person's
    try:
        ensure(service)
    except SystemExit:
        raise Failure(UNREACHABLE, f"the engine isn't running on {service}")
    try:
        with httpx.Client(base_url=service, timeout=30) as http:
            r = http.post("/api/ask", json=body)
            if r.status_code >= 400:
                raise Failure(UNREACHABLE, f"the engine refused it: {r.status_code} {r.text[:200]}")
            started = r.json()
            rid, cid = started["run"], started.get("conversation", "")
            deadline = time.time() + timeout
            while True:
                run = http.get(f"/api/runs/{rid}").json()
                if run.get("state") in ("done", "failed", "cancelled", "interrupted"):
                    break
                if time.time() > deadline:
                    raise Failure(TIMEOUT, f"still running after {timeout:.0f}s: eki watch {rid}", run)
                time.sleep(1.0)
            answer = ""
            if cid:
                r = http.get(f"/api/conversations/{cid}")
                if r.status_code < 400:
                    turns = [t for t in r.json().get("turns") or [] if t.get("role") == "assistant"]
                    answer = str(turns[-1].get("content") or "") if turns else ""
    except httpx.HTTPError as e:
        raise Failure(UNREACHABLE, f"lost the engine: {e}")
    if run.get("state") != "done":
        raise Failure(FAILED, run.get("error") or f"the run was {run.get('state')}", run)
    return run, cid, (answer or run.get("output") or "").strip()


# ---- where the output goes ---------------------------------------------------

def slug(prompt: str, fallback: str) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())[:6]
    return "-".join(words)[:48].strip("-") or fallback


def free(path: Path) -> Path:
    """`path`, or `name-2.ext`, `name-3.ext` … — never over an existing file."""
    n, stem, ext = 2, path.stem, path.suffix
    while path.exists():
        path = path.with_name(f"{stem}-{n}{ext}")
        n += 1
    return path


def destinations(out: str, count: int, default_name: str, ext: str) -> List[Path]:
    """Where `count` files go. `out` is a folder (existing, or ending in /),
    a file name (numbered when there are several), or empty for here."""
    target = Path(os.path.expanduser(out or "."))
    if not out or out.endswith(os.sep) or target.is_dir():
        target = target / f"{default_name}{ext}"
    if not target.suffix:
        target = target.with_suffix(ext)
    target.parent.mkdir(parents=True, exist_ok=True)
    if count == 1:
        return [free(target)]
    picked: List[Path] = []
    for i in range(1, count + 1):
        p = free(target.with_name(f"{target.stem}-{i}{target.suffix}"))
        while p in picked:
            p = free(p.with_name(f"{p.stem}-x{p.suffix}"))
        picked.append(p)
    return picked


def images_in(answer: str) -> List[str]:
    return [os.path.expanduser(p.replace("%20", " ")) for p in IMAGE_RE.findall(answer)]


# ---- the commands ------------------------------------------------------------

def image(args: Any, ensure: Callable[[str], None]) -> int:
    """A picture (or `--count` of them) from the image model, copied out of
    eki's own folder to where `-o` says."""
    body: Dict[str, Any] = {"prompt": args.prompt, "backend": args.model or "", "images": True}
    for k, v in (("width", args.width), ("height", args.height), ("batch", args.count)):
        if v:
            body[k] = v

    def make(run: Dict[str, Any], answer: str) -> List[Path]:
        made = [p for p in images_in(answer) if os.path.exists(p)]
        if not made:
            raise Failure(NOTHING, "the run finished without a picture", run)
        ext = Path(made[0]).suffix or ".png"
        dests = destinations(args.output, len(made), slug(args.prompt, "image"), ext)
        for src, dst in zip(made, dests):
            shutil.copy2(src, dst)
        return dests

    return _finish(args, body, ensure, make)


def write(args: Any, ensure: Callable[[str], None]) -> int:
    """Text from a chosen model (or the routed one) into a file; `-o -`
    prints the text itself instead, for a pipe."""
    body: Dict[str, Any] = {"prompt": args.prompt, "backend": args.model or ""}
    extra: Dict[str, Any] = {}

    def make(run: Dict[str, Any], answer: str) -> List[Path]:
        if not answer:
            raise Failure(NOTHING, "the run finished without an answer", run)
        if args.output == "-":
            if args.json:
                extra["text"] = answer
            else:
                sys.stdout.write(answer + "\n")
            return []
        dest = destinations(args.output, 1, slug(args.prompt, "text"), ".md")[0]
        dest.write_text(answer + "\n")
        return [dest]

    return _finish(args, body, ensure, make, extra)


# ---- what this Mac can do ----------------------------------------------------

def does(caps: Dict[str, Any]) -> str:
    """What a backend does, in a few words: the flags it has, what it makes,
    what a request must bring. Shared with the `eki_capabilities` tool."""
    said = ", ".join(k for k in ("repo", "tools", "vision", "images_out", "text", "web") if caps.get(k))
    if caps.get("produces"):
        said += ("; " if said else "") + "makes " + "/".join(caps["produces"])
    if caps.get("needs"):
        said += ("; " if said else "") + "needs " + "/".join(caps["needs"])
    return said


def reach(b: Dict[str, Any]) -> List[str]:
    """The commands that reach this backend, most specific first."""
    key, caps = b.get("key", ""), b.get("capabilities") or {}
    made = set(caps.get("produces") or [])
    use: List[str] = []
    if caps.get("images_out") or "image" in made:
        use.append(f'eki image "…" -m {key} -o <folder>/')
    if caps.get("text", True) and not caps.get("repo"):
        use.append(f'eki write "…" -m {key} -o <file>')
    if caps.get("repo"):
        use.append(f'eki ask "…" --backend {key} -r <folder>')
    elif caps.get("text", True):
        use.append(f'eki ask "…" --backend {key}')
    return use


def capabilities(args: Any, ensure: Callable[[str], None]) -> int:
    """Every backend the running engine has, up or down, and what reaches
    it: the engine's own view (it knows what is healthy right now), not a
    reading of the config file."""
    try:
        ensure(args.service)
        with httpx.Client(base_url=args.service, timeout=30) as http:
            r = http.get("/api/backends")
            if r.status_code >= 400:
                raise Failure(UNREACHABLE, f"the engine refused it: {r.status_code} {r.text[:200]}")
            rows = r.json()
    except SystemExit:
        return _unreachable(args, f"the engine isn't running on {args.service}")
    except httpx.HTTPError as e:
        return _unreachable(args, f"lost the engine: {e}")
    except Failure as f:
        return _unreachable(args, str(f))
    depth, _ = nesting.caller()
    backends = [{"key": b.get("key", ""), "label": b.get("label") or "", "kind": b.get("kind") or "",
                 "up": bool(b.get("ok")), "tier": b.get("tier"), "does": does(b.get("capabilities") or {}),
                 "capabilities": b.get("capabilities") or {}, "detail": b.get("detail") or "",
                 "use": reach(b) if b.get("ok") else []}
                for b in rows]
    if args.json:
        print(json.dumps({"ok": True, "depth": depth, "max_depth": nesting.MAX_DEPTH,
                          "can_ask": depth < nesting.MAX_DEPTH, "backends": backends, "error": ""}))
        return OK
    up = [b for b in backends if b["up"]]
    print(f"eki on this Mac: {len(up)} of {len(backends)} backends up.")
    if depth >= nesting.MAX_DEPTH:
        print(f"You are at nesting depth {depth} of {nesting.MAX_DEPTH}: eki will refuse what you ask — "
              "do this step yourself.")
    elif depth:
        print(f"You are at nesting depth {depth} of {nesting.MAX_DEPTH}: eki takes requests below that.")
    for group, rows_ in (("up", up), ("down", [b for b in backends if not b["up"]])):
        if not rows_:
            continue
        print(f"\n{group}:")
        for b in rows_:
            print(f"  {b['key']}: {b['label']} [{b['kind']}]"
                  + (f" ({b['does']})" if b["does"] else "")
                  + (f" — {b['detail']}" if b["detail"] else ""))
            for cmd in b["use"]:
                print(f"      {cmd}")
    return OK


def _unreachable(args: Any, error: str) -> int:
    if args.json:
        print(json.dumps({"ok": False, "backends": [], "error": error}))
    else:
        print(f"! {error}", file=sys.stderr)
    return UNREACHABLE


def _finish(args: Any, body: Dict[str, Any], ensure: Callable[[str], None],
            make: Callable[[Dict[str, Any], str], List[Path]],
            extra: Optional[Dict[str, Any]] = None) -> int:
    run: Dict[str, Any] = {}
    cid = ""
    try:
        run, cid, answer = run_and_wait(args.service, body, args.timeout, ensure)
        paths = [str(p.resolve()) for p in make(run, answer)]
    except Failure as f:
        run = f.run or run
        if args.json:
            _json(False, run, cid, [], str(f))
        else:
            print(f"! {f}", file=sys.stderr)
        return f.code
    if args.json:
        _json(True, run, cid, paths, "", extra)
    else:
        for p in paths:
            print(p)
    return OK


def _json(ok: bool, run: Dict[str, Any], cid: str, paths: List[str], error: str,
          extra: Optional[Dict[str, Any]] = None) -> None:
    print(json.dumps({**(extra or {}), "ok": ok, "paths": paths, "run": run.get("id", ""),
                      "conversation": cid or run.get("conversation_id", ""),
                      "backend": run.get("backend") or "", "state": run.get("state", ""),
                      "error": error}))
