"""One call, no tools: Claude Code and Codex answering a text-only job.

eki's own jobs (the prompt check, a digest, a review) need a model to
answer once from what it's given, nothing more. The programs do that
through their own CLIs, on your own login — eki never reads a token and
never calls a vendor's API itself.

Claude: `claude -p --output-format json --tools ""` with no MCP servers and
no saved session; the request goes on stdin and the answer is the result
JSON's `result`.

Codex: `codex exec --json` with the request on stdin, the answer being the
last `agent_message`. codex exec has no switch that removes its shell, so
the run gets a read-only sandbox in a fresh empty folder and an instruction
to answer from the request alone: the closest it comes to "no tools".

Both run in an empty temp folder, removed afterwards, so no project's
instructions are picked up on the way.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

from .base import Emit, Outcome, Turn, looks_like_limit

#: for a chore (row `chore`): eki's own job, answered from the request alone
CHORE = ("You are doing a job for eki. You have no tools and can't open files: everything "
         "you need is in the request. Answer from it in one go, in the form it asks for.")


class Limited(RuntimeError):
    """The program said its plan's limit is reached."""


def claude_argv(bin: str, model: Optional[str], system: str, effort: Optional[str] = None) -> List[str]:
    return ([bin, "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
             "--no-session-persistence", "--system-prompt", system]
            + (["--model", model] if model else []) + (["--effort", effort] if effort else []))


def codex_argv(bin: str, model: Optional[str], system: str, effort: Optional[str] = None) -> List[str]:
    return ([bin, "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
             "-c", f"developer_instructions={toml_string(system)}"]
            + (["-m", model] if model else [])
            + (["-c", f"model_reasoning_effort={toml_string(effort)}"] if effort else []) + ["-"])


def toml_string(s: str) -> str:
    """A TOML basic string (JSON's escapes are TOML's, bar DEL)."""
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def run(argv: List[str], stdin: str, timeout: float, cwd: Optional[str] = None,
        stop: Optional[threading.Event] = None) -> str:
    """Run once and give back stdout. A timeout kills it; a non-zero exit raises
    (`Limited` when it says a limit is reached). A stop kills it and gives "".

    The request is written by a thread of its own, all of it, then stdin is closed;
    stdout and stderr are drained by two more. (Polling communicate() every 0.25 s
    stopped writing after the first call: a slow start on a busy Mac with a request
    bigger than the pipe left the program waiting for the rest forever.)"""
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, process_group=0)
    got: Dict[str, bytes] = {"out": b"", "err": b""}

    def feed() -> None:
        try:
            proc.stdin.write(stdin.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass                          # it exited, or was killed, before reading it all

    def drain(stream: Any, key: str) -> None:
        got[key] = stream.read()

    threads = [threading.Thread(target=feed, daemon=True),
               threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    waited = 0.0
    while True:
        try:
            proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            waited += 0.25
            if stop is not None and stop.is_set():
                _kill(proc)
                return ""
            if waited >= timeout:
                _kill(proc)
                raise TimeoutError(f"{os.path.basename(argv[0])} gave no answer in {int(timeout)}s")
    for t in threads:
        t.join(5)
    text, err_text = got["out"].decode("utf-8", "replace"), got["err"].decode("utf-8", "replace")
    if proc.returncode != 0:
        said = (err_text.strip() or _failure(text) or f"exited {proc.returncode}")[-400:]
        if looks_like_limit(said) or looks_like_limit(_failure(text)):
            raise Limited(said)
        raise RuntimeError(f"{os.path.basename(argv[0])}: {said}")
    return text


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


def _failure(out: str) -> str:
    """What a failed run said on stdout, if it said it as JSON."""
    for line in reversed(out.strip().splitlines()):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            if ev.get("is_error"):
                return str(ev.get("result") or ev.get("subtype") or "")
            if ev.get("type") in ("error", "turn.failed"):
                return _message(ev)
    return ""


def claude_result(out: str) -> str:
    """The answer in `claude -p --output-format json`'s result."""
    try:
        data = json.loads(out)
    except ValueError:
        raise RuntimeError(f"claude: not a result: {out.strip()[:200]}")
    if not isinstance(data, dict):
        raise RuntimeError("claude: not a result")
    if data.get("is_error"):
        said = str(data.get("result") or data.get("subtype") or "error")[:400]
        raise Limited(said) if looks_like_limit(said) else RuntimeError(f"claude: {said}")
    return str(data.get("result") or "")


def codex_result(out: str) -> str:
    """The last `agent_message` in `codex exec --json`'s lines."""
    text: Optional[str] = None
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue                      # banners, log noise
        if not isinstance(ev, dict):
            continue
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
        elif ev.get("type") in ("turn.failed", "error"):
            said = _message(ev)[:400]
            raise Limited(said) if looks_like_limit(said) else RuntimeError(f"codex: {said}")
    if text is None:
        raise RuntimeError("codex: no answer")
    return text


def _message(ev: Dict[str, Any]) -> str:
    err = ev.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or ev.get("message") or "error")


def fold(messages: List[Dict[str, Any]]) -> Tuple[str, str]:
    """(system, stdin text) from chat messages: the system message apart, the
    rest one text — the request alone when there's nothing before it."""
    system = "\n\n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
    rest = [m for m in messages if m.get("role") != "system"]
    if len(rest) == 1:
        return system, _text(rest[0].get("content"))
    lines = [f"{'Assistant' if m.get('role') == 'assistant' else 'User'}: {_text(m.get('content'))}"
             for m in rest]
    return system, "\n\n".join(lines)


def _text(content: Any) -> str:
    if isinstance(content, list):          # parts: only the words go through
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def complete(kind: str, bin: str, messages: List[Dict[str, Any]], *, model: Optional[str] = None,
             effort: Optional[str] = None, timeout: float = 600, stop: Optional[threading.Event] = None, on_text=None) -> Dict[str, Any]:
    """One answer from `kind` ("claude" or "codex"): {"text", "tool_calls": []}."""
    system, prompt = fold(messages)
    argv = (claude_argv(bin, model, system, effort) if kind == "claude"
            else codex_argv(bin, model, system, effort))
    folder = tempfile.mkdtemp(prefix="eki-oneshot-")
    try:
        out = run(argv, prompt, timeout, cwd=folder, stop=stop)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if stop is not None and stop.is_set():
        return {"text": "", "tool_calls": []}
    text = claude_result(out) if kind == "claude" else codex_result(out)
    if on_text and text:
        on_text(text)
    return {"text": text, "tool_calls": []}


def chore(provider: Any, turn: Turn, emit: Emit) -> Outcome:
    """A chore run on a program: one call, one `text` event, no tools, no session."""
    messages = [{"role": "system", "content": CHORE}, *turn.history, {"role": "user", "content": turn.prompt}]
    try:
        got = provider.complete(messages, stop=turn.stop, model=turn.extra.get("model"),
                                effort=turn.extra.get("effort"))
    except Limited as e:
        emit("note", {"text": f"{provider.name}: limit reached"})
        return Outcome("limited", str(e)[:400])
    except (OSError, RuntimeError, ValueError) as e:      # TimeoutError is an OSError
        return Outcome("failed", f"{provider.name}: {e}"[:400])
    if turn.stop.is_set():
        return Outcome("cancelled", "stopped")
    if got["text"]:
        emit("text", {"text": got["text"]})
    return Outcome("done", finished=True)
