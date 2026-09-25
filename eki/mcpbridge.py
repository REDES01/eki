# SPDX-License-Identifier: Apache-2.0
"""eki's own tools, served to Claude Code in-process.

Claude Code's streaming mode lets the host register an "SDK" MCP server: a
server with no process and no port. The program sends each MCP message
(initialize, tools/list, tools/call …) as a `control_request` of subtype
`mcp_message` and waits for the JSON-RPC reply in the response. This module
is that server: the JSON-RPC handling and the tools eki offers the agent —
the other backends on this Mac (a picture from the image model, an answer
from a local model), what this Mac can do right now, and the screen itself.

The tools are the same ones the `eki` command line gives an agent with a
shell (ROADMAP, Stage 3); here they need no shell and no second process.
Codex gets the same tools through `eki mcp` (a stdio server over the same
handlers) — see eki/cli/serve.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import grant as grant_mod
from . import nesting
from . import notes
from . import produce

log = logging.getLogger("eki.mcp")

PROTOCOL = "2025-06-18"
#: nested calls stop somewhere: an agent asking eki asking an agent… (nesting)
MAX_DEPTH = nesting.MAX_DEPTH
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+\.(?:png|jpe?g|webp|gif))\)", re.I)


class Tool:
    def __init__(self, name: str, description: str, schema: Dict[str, Any],
                 handler: Callable[..., Awaitable[List[Dict[str, Any]]]],
                 read_only: bool = False, destructive: bool = False):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler
        self.read_only = read_only
        self.destructive = destructive

    def listing(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.schema,
                "annotations": {"readOnlyHint": self.read_only,
                                "destructiveHint": self.destructive}}


def text(s: str) -> Dict[str, Any]:
    return {"type": "text", "text": s}


def image_block(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    ext = path.rsplit(".", 1)[-1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}


class Bridge:
    """One in-process MCP server. `handle()` takes a JSON-RPC message and
    returns the reply; notifications get an empty result the way the
    program's own SDK answers them."""

    name = "eki"

    def __init__(self, engine: Any = None, conversation: str = "", depth: int = 0,
                 name: str = "eki", screen: bool = True, parent: str = "",
                 folder: str = ""):
        self.engine = engine
        #: where the agent works, for its project's memory when a call names none
        self.folder = folder
        self.conversation = conversation
        #: the depth what these tools ask arrives at, and the run they ask from
        self.depth = depth
        self.parent = parent
        self.name = name
        #: computer use — the screen tools — can be off while the rest stay
        self.screen = screen
        self.tools: Dict[str, Tool] = {}
        self._register_defaults()

    # ---- JSON-RPC --------------------------------------------------------

    async def handle(self, message: Any) -> Dict[str, Any]:
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "not a JSON-RPC message"}}
        mid = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if mid is None:                             # a notification: acknowledged, nothing more
            return {"jsonrpc": "2.0", "result": {}, "id": 0}
        try:
            if method == "initialize":
                result: Any = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                               "capabilities": {"tools": {}},
                               "serverInfo": {"name": self.name, "version": _version()},
                               "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t.listing() for t in self.tools.values()]}
            elif method == "tools/call":
                result = await self.call(str(params.get("name") or ""), params.get("arguments") or {})
            elif method in ("resources/list", "resources/templates/list"):
                result = {"resources": [], "resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"unknown method {method}"}}
        except Exception as e:                      # noqa: BLE001
            log.warning("eki tool %s: %s", method, e)
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:400]}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    async def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            return {"content": [text(f"no tool named {name}")], "isError": True}
        try:
            content = await tool.handler(**arguments)
        except TypeError as e:
            return {"content": [text(f"bad arguments for {name}: {e}")], "isError": True}
        except Exception as e:                      # noqa: BLE001
            return {"content": [text(f"{name} failed: {e}")], "isError": True}
        return {"content": content or [text("(nothing)")]}

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    # ---- the tools ---------------------------------------------------------

    def _register_defaults(self) -> None:
        self.add(Tool("eki_capabilities",
                      "What this Mac can do right now through eki: the backends (local "
                      "models, image graphs, the CLIs, API providers), whether each is up, "
                      "and what it is for. Read this before choosing eki_ask or eki_image.",
                      {"type": "object", "properties": {}}, self.capabilities, read_only=True))
        self.add(Tool("eki_ask",
                      "Ask another model on this Mac through eki and get its answer: a local "
                      "model for a cheap or private step, a prose model for writing, or let "
                      "eki's router pick. Runs as its own thread; the answer is returned "
                      "whole. Not for work you can do yourself. The run gets only what you "
                      "hand it: read-only unless you give it a repo, and in a repo only the "
                      "commands you list.",
                      {"type": "object",
                       "properties": {"prompt": {"type": "string"},
                                      "backend": {"type": "string",
                                                  "description": "a provider key from eki_capabilities; "
                                                                 "empty lets eki route"},
                                      "repo": {"type": "string",
                                               "description": "a folder to work on, in its own copy; "
                                                              "empty for a question or a review"},
                                      "commands": {"type": "array", "items": {"type": "string"},
                                                   "description": "commands the run may use, e.g. "
                                                                  "'pytest', 'git diff'"},
                                      "read_only": {"type": "boolean",
                                                    "description": "look at the repo, change nothing"}},
                       "required": ["prompt"]}, self.ask))
        self.add(Tool("eki_image",
                      "Generate a picture with the image model on this Mac (ComfyUI through "
                      "eki). Returns the image and its file path. Give a full prompt: "
                      "subject, style, composition.",
                      {"type": "object",
                       "properties": {"prompt": {"type": "string"},
                                      "width": {"type": "integer"}, "height": {"type": "integer"},
                                      "count": {"type": "integer", "minimum": 1, "maximum": 4}},
                       "required": ["prompt"]}, self.image))
        self.add(Tool("eki_recall",
                      "Read eki's memory — plain notes every agent on this Mac shares, the "
                      "project's and the person's own. No arguments lists every note; `name` "
                      "reads one whole; `query` finds the notes that mention those words. Look "
                      "here before asking the person something they may have said before.",
                      {"type": "object",
                       "properties": {"name": {"type": "string", "description": "a note to read whole"},
                                      "query": {"type": "string", "description": "words to look for"},
                                      "scope": {"type": "string", "enum": ["", "project", "global"],
                                                "description": "empty reads both, the project's first"},
                                      "folder": {"type": "string",
                                                 "description": "the folder you work in, for its project's notes"}}},
                      self.recall, read_only=True))
        self.add(Tool("eki_remember",
                      "Keep a fact in eki's memory, where every agent on this Mac reads it: "
                      "something the person told you, a decision, where a thing lives. One "
                      "fact per note, in plain words. It goes to the project's notes when the "
                      "folder is in one, the person's own otherwise; the same name replaces a "
                      "note (or adds to it with `append`). How-to belongs in a skill, not here.",
                      {"type": "object",
                       "properties": {"text": {"type": "string"},
                                      "name": {"type": "string",
                                               "description": "short, for its file name (default: its first line)"},
                                      "description": {"type": "string", "description": "one line on what it is"},
                                      "scope": {"type": "string", "enum": ["", "project", "global"]},
                                      "folder": {"type": "string",
                                                 "description": "the folder you work in, for its project's notes"},
                                      "append": {"type": "boolean"}},
                       "required": ["text"]}, self.remember))
        if sys.platform == "darwin" and self.screen:
            self.add(Tool("eki_screenshot",
                          "A screenshot of the Mac's main display, scaled to logical points so "
                          "that a pixel in the image is a coordinate for eki_click. Returns "
                          "the image and its size.",
                          {"type": "object", "properties": {
                              "display": {"type": "integer", "description": "1 = main"}}},
                          self.screenshot, read_only=True))
            self.add(Tool("eki_click",
                          "Click at a point on screen (logical coordinates from eki_screenshot).",
                          {"type": "object",
                           "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                          "button": {"type": "string", "enum": ["left", "right"]},
                                          "double": {"type": "boolean"}},
                           "required": ["x", "y"]}, self.click))
            self.add(Tool("eki_type",
                          "Type text into whatever has keyboard focus.",
                          {"type": "object", "properties": {"text": {"type": "string"}},
                           "required": ["text"]}, self.type_text))
            self.add(Tool("eki_key",
                          "Press a key or shortcut, e.g. 'return', 'escape', 'cmd+s', 'cmd+shift+t'.",
                          {"type": "object", "properties": {"keys": {"type": "string"}},
                           "required": ["keys"]}, self.key))
            self.add(Tool("eki_scroll",
                          "Scroll at a point: negative dy scrolls down.",
                          {"type": "object",
                           "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                          "dx": {"type": "integer"}, "dy": {"type": "integer"}},
                           "required": ["x", "y"]}, self.scroll))
            self.add(Tool("eki_open_app",
                          "Open or bring an application to the front by name (e.g. 'Safari', "
                          "'Xcode', 'Simulator').",
                          {"type": "object", "properties": {"name": {"type": "string"}},
                           "required": ["name"]}, self.open_app))

    async def capabilities(self) -> List[Dict[str, Any]]:
        if self.engine is None:
            return [text("eki engine not attached")]
        rows = await self.engine.describe()
        lines = []
        for b in rows:
            ok = b.get("ok", b.get("healthy"))
            state = "up" if ok else "down"
            does = produce.does(b.get("capabilities") or {})
            lines.append(f"- {b.get('key')}: {b.get('label') or ''} [{b.get('kind')}] {state}"
                         + (f" ({does})" if does else "")
                         + (f" — {b.get('detail')}" if b.get("detail") else ""))
        return [text("Backends on this Mac (key: label [kind] state (what it does)):\n"
                     + "\n".join(lines))]

    async def ask(self, prompt: str, backend: str = "", repo: str = "",
                  commands: Optional[List[str]] = None, read_only: bool = False
                  ) -> List[Dict[str, Any]]:
        if self.engine is None:
            return [text("eki engine not attached")]
        if self.depth >= MAX_DEPTH:
            return [text(f"eki_ask refused: nesting depth {self.depth} reached")]
        content = await self._run(prompt, backend_key=backend, repo=os.path.expanduser(repo) if repo else "",
                                  wants={"read_only": bool(read_only),
                                         "commands": [str(c) for c in commands or []]})
        return [text(content)]

    async def recall(self, name: str = "", query: str = "", scope: str = "",
                     folder: str = "") -> List[Dict[str, Any]]:
        where = os.path.expanduser(folder or self.folder)
        if name:
            note = notes.read(name, where, scope)
            if note:
                return [text(f"{note['name']} ({note['scope']}, {note['path']}):\n\n{note['text']}")]
            if not query:
                query = name
        rows = notes.search(query, where, scope) if query else notes.listing(where, scope)
        if not rows:
            return [text(f"no note mentions {query!r}" if query else "memory is empty")]
        lines = []
        for n in rows:
            lines.append(f"- {n['name']} [{n['scope']}]: {n['description']}")
            lines += [f"    {ln[:160]}" for ln in n.get("lines") or ()]
        return [text(("Notes that mention it" if query else "Notes (name [scope]: what it is)")
                     + " — read one whole with eki_recall(name=…):\n" + "\n".join(lines))]

    async def remember(self, text: str, name: str = "", description: str = "", scope: str = "",
                       folder: str = "", append: bool = False) -> List[Dict[str, Any]]:
        if grant_mod.from_env().level == "read":
            raise RuntimeError("a read-only run can't write to memory")
        where = os.path.expanduser(folder or self.folder)
        note = notes.write(text, name=name, where=where, scope=scope, description=description,
                           source=f"thread {self.conversation}" if self.conversation else "an agent",
                           append=append)
        said = f"{'remembered' if note['made'] else 'updated'} {note['name']} ({note['scope']}) at {note['path']}"
        return [{"type": "text", "text": said}]            # `text` is the note here

    async def image(self, prompt: str, width: int = 0, height: int = 0, count: int = 1
                    ) -> List[Dict[str, Any]]:
        if self.engine is None:
            return [text("eki engine not attached")]
        if self.depth >= MAX_DEPTH:
            return [text(f"eki_image refused: nesting depth {self.depth} reached")]
        content = await self._run(prompt, images=True,
                                  image={"width": width or None, "height": height or None,
                                         "batch": count if count and count > 1 else None})
        blocks: List[Dict[str, Any]] = []
        for path in IMAGE_RE.findall(content):
            path = path.replace("%20", " ")
            block = image_block(os.path.expanduser(path))
            if block:
                blocks.append(block)
                blocks.append(text(f"saved at {path}"))
        return blocks or [text(content)]

    async def _run(self, prompt: str, **kw: Any) -> str:
        """A run of its own in a fresh thread, waited for; the answer text.
        It gets what this server's run hands it, never more (eki/grant.py).
        Below the thread this server serves: whose work it is carries down."""
        started = await self.engine.ask(prompt, conversation="", via="agent",
                                        parent=grant_mod.from_env().to_json(),
                                        depth=self.depth, parent_run=self.parent,
                                        parent_thread=self.conversation, **kw)
        rid = started["run"]
        runner = self.engine.runner
        q = runner.subscribe(rid)
        try:
            deadline = time.time() + 900
            while time.time() < deadline:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                if ev.get("event") == "state" and ev.get("state") in ("done", "failed", "cancelled"):
                    break
        finally:
            runner.unsubscribe(rid, q)
        run = self.engine.runs.get(rid) or {}
        if run.get("state") == "failed":
            raise RuntimeError(run.get("error") or "the run failed")
        turns = self.engine.store.turns(started["conversation"])
        answers = [t for t in turns if t.get("role") == "assistant"]
        return (answers[-1].get("content") if answers else run.get("output") or "").strip()

    # ---- the screen (macOS) --------------------------------------------------

    async def screenshot(self, display: int = 1) -> List[Dict[str, Any]]:
        helper = _helper()
        if helper:
            # without Screen Recording, screencapture quietly gives the
            # wallpaper alone; ask first, and put up macOS's prompt once
            state = await _exec([helper, "check"], quiet=True)
            if "screen=0" in state:
                await _exec([helper, "ask", "screen"], quiet=True)
                raise RuntimeError("Screen Recording permission not granted for eki-hid (eki's input "
                                   "helper): System Settings › Privacy & Security › Screen Recording")
        path = os.path.join(tempfile.gettempdir(), f"eki-shot-{int(time.time() * 1000)}.png")
        args = ["screencapture", "-x", "-t", "png"]
        if display and display > 1:
            args += ["-D", str(display)]
        await _exec(args + [path])
        if not os.path.exists(path):
            return [text("screencapture produced nothing — Screen Recording permission "
                         "may be needed for Eki in System Settings › Privacy & Security")]
        w, h = await _hid("screen")
        if w and h:
            # Retina captures are 2x: scale to points so coordinates match clicks
            await _exec(["sips", "-z", str(h), str(w), path], quiet=True)
        block = image_block(path)
        try:
            os.remove(path)
        except OSError:
            pass
        return [block, text(f"{w}×{h} points")] if block else [text("couldn't read the screenshot")]

    async def click(self, x: float, y: float, button: str = "left", double: bool = False
                    ) -> List[Dict[str, Any]]:
        await _hid("click", str(x), str(y), button, "double" if double else "single")
        return [text(f"clicked {button} at {x:.0f},{y:.0f}" + (" twice" if double else ""))]

    async def type_text(self, **kw: Any) -> List[Dict[str, Any]]:
        s = str(kw.get("text", ""))
        await _hid("type", s)
        return [text(f"typed {len(s)} characters")]

    async def key(self, keys: str) -> List[Dict[str, Any]]:
        await _hid("key", keys)
        return [text(f"pressed {keys}")]

    async def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> List[Dict[str, Any]]:
        await _hid("scroll", str(x), str(y), str(dx), str(dy))
        return [text(f"scrolled {dx},{dy} at {x:.0f},{y:.0f}")]

    async def open_app(self, name: str) -> List[Dict[str, Any]]:
        await _exec(["open", "-a", name])
        return [text(f"opened {name}")]


INSTRUCTIONS = ("eki is the model hub on this Mac. Use eki_capabilities to see the other "
                "backends, eki_ask to delegate a step to one of them, eki_image for pictures, "
                "eki_recall / eki_remember for the notes every agent here shares, and the eki_screenshot / eki_click / eki_type / eki_key tools to drive the "
                "screen when a task needs a GUI. Paths returned are real files on this Mac.")


def _version() -> str:
    try:
        from . import __version__            # type: ignore[attr-defined]
        return str(__version__)
    except Exception:                       # noqa: BLE001
        return "0"


async def _exec(argv: List[str], quiet: bool = False, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{argv[0]} timed out")
    if proc.returncode != 0 and not quiet:
        raise RuntimeError((err or out).decode("utf-8", "replace").strip()[:300]
                           or f"{argv[0]} failed")
    return out.decode("utf-8", "replace")


def _helper() -> Optional[str]:
    """The compiled input helper (mac/tools/hid.swift), if the app carries
    one; a dev checkout falls back to osascript."""
    here = os.path.dirname(sys.executable)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.environ.get("EKI_HID"),
                 # bundled: Contents/Resources/python/bin/python3 → Contents/Helpers
                 os.path.join(here, "..", "..", "..", "Helpers", "eki-hid"),
                 # a dev checkout: the app built beside the engine
                 os.path.join(root, "Eki.app", "Contents", "Helpers", "eki-hid"),
                 os.path.expanduser("~/.eki/bin/eki-hid")):
        if cand and os.access(cand, os.X_OK):
            return cand
    return shutil.which("eki-hid")


def _moved() -> None:
    """The screen tools clicked or typed: marked, so it isn't taken for you at
    the keyboard (a goal that uses the screen steps out when you're back)."""
    from . import shift
    shift.note_input()


async def _hid(*args: str) -> Any:
    """Drive the input helper; without it, the little that osascript can do."""
    helper = _helper()
    moves = args[0] in ("click", "type", "key", "scroll", "move", "drag")
    if helper:
        out = await _exec([helper, *args])
        if moves:
            _moved()
        if args[0] == "screen":
            try:
                w, h = out.split()[:2]
                return int(float(w)), int(float(h))
            except ValueError:
                return 0, 0
        return out
    # fallback: System Events
    if moves:
        _moved()
    cmd = args[0]
    if cmd == "screen":
        out = await _exec(["osascript", "-e",
                           'tell application "Finder" to get bounds of window of desktop'])
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", out)]
        return (nums[2], nums[3]) if len(nums) >= 4 else (0, 0)
    if cmd == "click":
        x, y = args[1], args[2]
        script = f'tell application "System Events" to click at {{{x}, {y}}}'
        return await _exec(["osascript", "-e", script])
    if cmd == "type":
        s = args[1].replace("\\", "\\\\").replace('"', '\\"')
        return await _exec(["osascript", "-e", f'tell application "System Events" to keystroke "{s}"'])
    if cmd == "key":
        mods = []
        key = ""
        for part in args[1].lower().split("+"):
            if part in ("cmd", "command"):
                mods.append("command down")
            elif part in ("shift",):
                mods.append("shift down")
            elif part in ("alt", "option"):
                mods.append("option down")
            elif part in ("ctrl", "control"):
                mods.append("control down")
            else:
                key = part
        codes = {"return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
                 "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126}
        using = f" using {{{', '.join(mods)}}}" if mods else ""
        if key in codes:
            script = f'tell application "System Events" to key code {codes[key]}{using}'
        else:
            script = f'tell application "System Events" to keystroke "{key}"{using}'
        return await _exec(["osascript", "-e", script])
    if cmd == "scroll":
        raise RuntimeError("scrolling needs the eki-hid helper (build the app with build_app.sh)")
    raise RuntimeError(f"unknown input command {cmd}")


# ---- a stdio server over the same handlers, for Codex and any other client -----

async def serve_stdio(bridge: Bridge) -> None:
    """Speak MCP on stdin/stdout: `eki mcp`. Codex (and anything else that
    runs stdio servers) gets exactly the tools Claude Code gets in-process."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") is None:
            continue                                # notifications need no reply
        reply = await bridge.handle(message)
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


class RemoteEngine:
    """The running engine, reached over its HTTP API — what `eki mcp` uses
    when Codex (or anything else) starts eki's tool server as a process.
    Has the three methods the tools call on an in-process engine."""

    def __init__(self, service: str = "http://127.0.0.1:8787"):
        import httpx
        self.service = service.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30)

    async def describe(self) -> List[Dict[str, Any]]:
        r = await self._client.get(self.service + "/api/backends")
        r.raise_for_status()
        return r.json()

    async def ask(self, prompt: str, conversation: str = "", **kw: Any) -> Dict[str, str]:
        wants = kw.get("wants") or {}
        body = {"prompt": prompt, "conversation": conversation,
                "backend": kw.get("backend_key", "") or "", "images": bool(kw.get("images")),
                "via": "agent", "repo": kw.get("repo") or "",
                "parent": kw.get("parent") or {},
                "read_only": bool(wants.get("read_only")), "commands": wants.get("commands") or [],
                "depth": int(kw.get("depth") or 0), "parent_run": kw.get("parent_run") or "",
                "parent_thread": kw.get("parent_thread") or ""}
        # Codex starts this server in its own folder: calls from it belong
        # to the project that folder is in
        produce.located(body)
        image = kw.get("image") or {}
        for k in ("width", "height", "batch"):
            if image.get(k):
                body[k] = image[k]
        r = await self._client.post(self.service + "/api/ask", json=body)
        r.raise_for_status()
        return r.json()

    async def wait(self, rid: str, timeout: float = 900) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = await self._client.get(self.service + f"/api/runs/{rid}")
            r.raise_for_status()
            run = r.json()
            if run.get("state") in ("done", "failed", "cancelled"):
                return run
            await asyncio.sleep(1.0)
        raise RuntimeError("the run took too long")

    async def answer_text(self, cid: str) -> str:
        r = await self._client.get(self.service + f"/api/conversations/{cid}")
        r.raise_for_status()
        turns = (r.json() or {}).get("turns") or []
        answers = [t for t in turns if t.get("role") == "assistant"]
        return str(answers[-1].get("content") if answers else "").strip()


class RemoteBridge(Bridge):
    """The same tools, over the HTTP API."""

    async def _run(self, prompt: str, **kw: Any) -> str:
        started = await self.engine.ask(prompt, conversation="", via="agent",
                                        parent=grant_mod.from_env().to_json(),
                                        depth=self.depth, parent_run=self.parent,
                                        parent_thread=self.conversation, **kw)
        run = await self.engine.wait(started["run"])
        if run.get("state") == "failed":
            raise RuntimeError(run.get("error") or "the run failed")
        return await self.engine.answer_text(started["conversation"])
