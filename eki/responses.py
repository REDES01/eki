"""OpenAI's Responses API in front of the local model, so Codex can drive it.

Codex (0.156) speaks only Responses; the local servers (mlx_lm) speak only
chat completions. This module translates one to the other and back — the
protocol, nothing more. It holds no state: each request carries the whole
conversation (Codex sends it in full), so `previous_response_id` is refused,
and a restart of the engine mid-stream costs Codex one retry, not a turn.
It is not a harness: the tools, the loop and the sandbox are Codex's.

`to_chat` maps the request; `responses_stream.Translator` maps the answer;
`serve` does the one HTTP call between them. The server mounts it as
`POST /v1/responses`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from .responses_stream import Translator

#: tools Codex declares that aren't functions and that a local model can't be given
DROPPED_TOOLS = ("web_search", "web_search_preview", "local_shell", "image_generation")


class BadRequest(ValueError):
    """The request asks for something this adapter doesn't do: HTTP 400."""


class Upstream(RuntimeError):
    """The local server didn't take the request: HTTP 502, before any byte is sent."""


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])       # input_text, output_text, text; pictures are left out
    return "".join(parts)


def _tools(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Chat tools for the request's function tools, and the names of the
    freeform (`custom`) ones, which become a function with one `input` string."""
    tools, custom = [], []
    for t in body.get("tools") or []:
        kind = t.get("type")
        if kind == "function":
            fn = {"name": t.get("name", ""), "description": t.get("description") or "",
                  "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
        elif kind == "custom":
            custom.append(t.get("name", ""))
            fn = {"name": t.get("name", ""), "description": t.get("description") or "",
                  "parameters": {"type": "object", "required": ["input"],
                                 "properties": {"input": {"type": "string",
                                                          "description": "the tool's whole input, raw"}}}}
        else:
            continue
        tools.append({"type": "function", "function": fn})
    return tools, custom


def to_chat(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    """The one streaming chat-completions request for a Responses request."""
    if body.get("previous_response_id"):
        raise BadRequest("previous_response_id is not supported: send the whole input")
    system: List[str] = [str(body["instructions"])] if body.get("instructions") else []
    messages: List[Dict[str, Any]] = []
    items = body.get("input")
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    for item in items or []:
        kind = item.get("type", "message")
        if kind == "message":
            role, text = item.get("role", "user"), _text(item.get("content"))
            if role in ("system", "developer"):
                system.append(text)        # local chat templates take one system message, first
            else:
                messages.append({"role": role, "content": text})
        elif kind in ("function_call", "custom_tool_call"):
            args = item.get("arguments") if kind == "function_call" else json.dumps({"input": item.get("input", "")})
            call = {"id": item.get("call_id") or item.get("id") or "", "type": "function",
                    "function": {"name": item.get("name", ""), "arguments": args or "{}"}}
            last = messages[-1] if messages else None
            if last is not None and last["role"] == "assistant":
                last.setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif kind in ("function_call_output", "custom_tool_call_output"):
            messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""),
                             "content": _text(item.get("output"))})
        # reasoning items and the like: nothing a local model takes back
    if system:
        messages.insert(0, {"role": "system", "content": "\n\n".join(system)})
    out: Dict[str, Any] = {"model": model, "messages": messages, "stream": True,
                           "stream_options": {"include_usage": True}}
    tools, _ = _tools(body)
    if tools:
        out["tools"] = tools
        choice = body.get("tool_choice")
        out["tool_choice"] = choice if choice in ("none", "required") else "auto"
    if body.get("max_output_tokens"):
        out["max_tokens"] = int(body["max_output_tokens"])
    return out


def chunks(resp: Any) -> Any:
    """The parsed `data:` chunks of a chat-completions SSE stream."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            got = json.loads(data)
        except ValueError:
            continue
        if isinstance(got, dict):
            yield got


def serve(body: Dict[str, Any], base_url: str, model: str, write: Callable[[bytes], None], *,
          params: Optional[Dict[str, Any]] = None, timeout: float = 600) -> None:
    """Answer one Responses request from the chat server at `base_url`,
    writing SSE frames. Raises BadRequest or Upstream before the first write;
    a failure once streaming ends the stream with `response.failed`."""
    request = to_chat(body, model)
    request.update(params or {})
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(request).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise Upstream(f"local server {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from e
    except OSError as e:
        raise Upstream(f"local server at {base_url}: {e}") from e
    t = Translator(body.get("model") or model, custom=set(_tools(body)[1]),
                   prompt_chars=len(json.dumps(request["messages"])))
    with resp:
        for f in t.start():
            write(f)
        try:
            for chunk in chunks(resp):
                for f in t.feed(chunk):
                    write(f)
        except (OSError, ValueError) as e:
            if isinstance(e, (BrokenPipeError, ConnectionResetError)):
                raise                          # Codex went away: nothing left to tell it
            for f in t.fail(f"local server stream broke: {e}"[:300]):
                write(f)
            return
        for f in t.finish():
            write(f)


def local_for(model: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """The local provider a request is for: the one named by `model` (by entry
    name or by its `model` id), else the first `local` entry. KeyError if none."""
    from . import providers
    local = [(n, c) for n, c in providers.config().items() if c.get("kind") == "local" and not c.get("off")]
    for n, c in local:
        if model and model in (n, c.get("model")):
            return n, c
    if not local:
        raise KeyError("no local model in providers.json")
    return local[0]


def chat_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The sampling and template settings eki's own local turns use for this entry."""
    from .providers.local import DEFAULT_PARAMS
    return {"chat_template_kwargs": {"enable_thinking": bool(cfg.get("thinking"))},
            **(cfg.get("params") or DEFAULT_PARAMS)}
