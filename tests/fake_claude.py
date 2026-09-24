# SPDX-License-Identifier: Apache-2.0
"""A stand-in for Claude Code's two-way streaming mode, for tests.

Speaks just enough of the protocol: the initialize handshake, the init
event, streamed text, a question (when told to "ask"), a permission prompt
(when told to "run"), slash commands, and a result per turn.
"""
import json
import sys

ARGV = sys.argv[1:]
SESSION = next((ARGV[i + 1] for i, a in enumerate(ARGV) if a in ("--session-id", "--resume")), "sess-1")
MODEL = next((ARGV[i + 1] for i, a in enumerate(ARGV) if a == "--model"), "claude-sonnet-5")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


HELD = []          # messages that arrived while waiting for an answer: queued, like the program does


def read():
    if HELD:
        return HELD.pop(0)
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def wait_answer(request_id):
    while True:
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        msg = json.loads(line)
        if msg.get("type") == "control_response" and msg["response"]["request_id"] == request_id:
            return msg["response"]["response"]
        HELD.append(msg)


def text(s):
    out({"type": "stream_event", "event": {"type": "message_start"}})
    for piece in (s[:len(s) // 2], s[len(s) // 2:]):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": piece}}})
    out({"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": s}],
                                          "usage": {"input_tokens": 4, "cache_read_input_tokens": 12000,
                                                    "output_tokens": len(s)}}})


def result(s, err=False):
    out({"type": "result", "subtype": "success", "is_error": err, "result": s,
         "usage": {"input_tokens": 10, "output_tokens": len(s)},
         "modelUsage": {"claude-sonnet-5": {"inputTokens": 10, "outputTokens": len(s),
                                            "contextWindow": 200000}}})


def drill(prompt):
    """eki's restart drill (eki/drill.py): `[drill write NAME count N]` —
    count slowly (`d1 d2 …`, long enough to be cut off in the middle), then
    write NAME in the folder it works in: a change made, or a conflict
    resolved (the whole file, no markers left)."""
    import os as _os
    import re as _re
    import time as _t
    m = _re.search(r"\[drill(?: write (\S+?))?(?: count (\d+))?\]", prompt)
    name, n = (m.group(1), int(m.group(2) or 20)) if m else (None, 20)
    out({"type": "stream_event", "event": {"type": "message_start"}})
    for i in range(1, n + 1):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": f"d{i} "}}})
        _t.sleep(0.15)
    if name:
        with open(_os.path.join(_os.getcwd(), name), "w") as f:
            f.write("written by the drill's agent\n")
    whole = "".join(f"d{i} " for i in range(1, n + 1))
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": whole}]}})
    result(whole)


SDK_SERVERS = []
SDK_TOOLS = {}
AUTHED = False
PERMISSION_MODE = "default"
TURN = 0
LAST_UUID = ""
DISABLED = {}
FLAGS = {}

if "--resume" in ARGV and SESSION == "gone":
    sys.stderr.write("No conversation found with session ID: gone\n")
    sys.exit(1)

while True:
    msg = read()
    if msg.get("type") == "control_request" and msg["request"]["subtype"] == "initialize":
        SDK_SERVERS = list(msg["request"].get("sdkMcpServers") or [])
        out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"],
             "response": {"commands": [{"name": "compact", "description": "Summarise"},
                                       {"name": "doctor", "description": "Check"},
                                       {"name": "review", "description": "Review changes"}],
                          "models": [{"value": "sonnet", "displayName": "Sonnet 5", "description": "fast"},
                                     {"value": "fable", "displayName": "Fable 5.1", "description": "best",
                                      "supportedEffortLevels": ["low", "high"]}],
                          "account": {"email": "x@example.com", "subscriptionType": "pro"},
                          "agents": [], "output_style": "default", "available_output_styles": ["default"]}}})
        out({"type": "system", "subtype": "init", "session_id": SESSION, "model": MODEL,
             "permissionMode": "default", "terminal_slash_commands": ["doctor"],
             "claude_code_version": "9.9.9", "tools": ["Bash", "Read"],
             "mcp_servers": [{"name": "docs", "status": "connected"},
                             {"name": "gmail", "status": "needs-auth"}]
                            + [{"name": n, "status": "pending"} for n in SDK_SERVERS]})
        # the handshake for an in-process server, as the program does it
        for n in SDK_SERVERS:
            for i, m in enumerate([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18"}},
                                   {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                   {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]):
                out({"type": "control_request", "request_id": f"mcp-{n}-{i}",
                     "request": {"subtype": "mcp_message", "server_name": n, "message": m}})
                reply = wait_answer(f"mcp-{n}-{i}")
                if m.get("method") == "tools/list":
                    SDK_TOOLS[n] = [t["name"] for t in reply["mcp_response"]["result"]["tools"]]
        continue
    if msg.get("type") == "control_request" and msg["request"]["subtype"] == "set_model":
        MODEL = msg["request"]["model"]
        out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"], "response": {}}})
        continue
    if msg.get("type") == "control_request":
        req, rid = msg["request"], msg["request_id"]
        sub = req["subtype"]
        reply = None
        if sub == "mcp_status":
            reply = {"mcpServers": [{"name": "docs", "status": "connected", "tools": [{"name": "search"}]},
                                    {"name": "gmail", "status": "needs-auth" if not AUTHED else "connected",
                                     "config": {"type": "http", "url": "https://mcp.example/gmail"}}]
                                   + [{"name": n, "status": "connected", "tools": [{"name": t} for t in ts]}
                                      for n, ts in SDK_TOOLS.items()]}
        elif sub == "mcp_authenticate":
            AUTHED = True
            reply = {"opened": "https://auth.example/start"}
        elif sub == "reload_skills":
            reply = {"skills": [{"name": "docx", "description": "Word documents", "argumentHint": ""}]}
        elif sub == "mcp_set_servers":
            for n, spec in (req.get("servers") or {}).items():
                if spec.get("type") != "sdk":
                    SDK_TOOLS.setdefault(n, ["screenshot"])
            reply = {}
        elif sub == "mcp_toggle":
            DISABLED[req["serverName"]] = not req["enabled"]
            reply = {}
        elif sub == "mcp_reconnect" and DISABLED.get(req["serverName"]):
            out({"type": "control_response", "response": {"subtype": "error", "request_id": rid,
                 "error": f"MCP server {req['serverName']} is disabled — enable it (mcp_toggle) before reconnecting"}})
            continue
        elif sub == "apply_flag_settings":
            FLAGS.update(req.get("settings") or {})
            reply = {}
        elif sub == "update_settings" and "effort" in (req.get("settings") or {}):
            out({"type": "control_response", "response": {"subtype": "error", "request_id": rid,
                                                          "error": "update_settings keys not allowed: effort"}})
            continue
        elif sub == "get_settings":
            reply = {"applied": {"effort": FLAGS.get("effortLevel", "high")}}
        elif sub in ("mcp_reconnect", "mcp_clear_auth",
                     "rename_session", "stop_task", "set_max_thinking_tokens", "update_settings"):
            reply = {}
        elif sub == "set_permission_mode":
            PERMISSION_MODE = req["mode"]
            reply = {}
        elif sub == "get_usage":
            reply = {"session": {"total_cost_usd": 0.12}, "subscription_type": "pro",
                     "rate_limits": {"five_hour": {"utilization": 0.3, "resets_at": "2030-01-01T00:00:00Z"}}}
        elif sub == "get_context_usage":
            reply = {"totalTokens": 12000, "maxTokens": 1000000, "percentage": 1.2,
                     "categories": [{"name": "System prompt", "tokens": 3000, "kind": "used", "color": "blue"}]}
        elif sub == "list_permission_rules":
            reply = {"state": {"rules": [{"toolName": "Bash", "ruleContent": "git *", "behavior": "allow"}]}}
        elif sub == "rewind_files":
            reply = {"rewound": "known" if req["user_message_id"] == LAST_UUID else "unknown",
                     "dry_run": bool(req.get("dry_run"))}
        elif sub == "interrupt":
            reply = {"still_queued": []}
        if reply is None:
            out({"type": "control_response", "response": {"subtype": "error", "request_id": rid,
                                                          "error": f"unknown request {sub}"}})
        else:
            out({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                          "response": reply}})
        continue
    if msg.get("type") != "user":
        continue
    prompt = msg["message"]["content"][0]["text"]
    pictures = [b for b in msg["message"]["content"] if b.get("type") == "image"]
    if pictures:
        prompt += f" [+{len(pictures)} image, {pictures[0]['source']['media_type']}]"
    TURN += 1
    LAST_UUID = msg.get("uuid", "")
    if prompt.startswith("/compact"):
        out({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual", "pre_tokens": 1000}})
        result("")
    elif "[drill deaf]" in prompt:
        # the drill's worker that stops reading its input: it answers, then
        # never reads another line
        text("not reading any more")
        result("not reading any more")
        import time as _t
        _t.sleep(600)
        sys.exit(0)
    elif "[drill" in prompt:
        drill(prompt)
    elif "ask" in prompt:
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "Red or blue?", "header": "Colour", "multiSelect": False,
                                      "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]}}]}})
        out({"type": "control_request", "request_id": "req-ask", "request": {
            "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "tool_use_id": "tu1",
            "input": {"questions": [{"question": "Red or blue?", "header": "Colour", "multiSelect": False,
                                     "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]}}})
        answer = wait_answer("req-ask")
        chosen = (answer.get("updatedInput") or {}).get("answers", {}).get("Red or blue?", "?")
        text(f"You chose {chosen}.")
        result(f"You chose {chosen}.")
    elif "run" in prompt:
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu2", "name": "Bash", "input": {"command": "rm -rf build"}}]}})
        out({"type": "control_request", "request_id": "req-perm", "request": {
            "subtype": "can_use_tool", "tool_name": "Bash", "tool_use_id": "tu2",
            "input": {"command": "rm -rf build"}, "title": "Bash command",
            "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm -rf build"}]}]}})
        answer = wait_answer("req-perm")
        if answer.get("behavior") == "allow":
            text("Removed build.")
            result("Removed build.")
        else:
            text("Understood, I won't.")
            result("Understood, I won't.")
    elif "slowly" in prompt:
        # a long turn, a word at a time: long enough for an engine to go
        # away and come back in the middle of it (eki/workers.py)
        import re as _re
        import time as _t
        n = int((_re.search(r"to (\d+)", prompt) or [0, 20])[1])
        out({"type": "stream_event", "event": {"type": "message_start"}})
        for i in range(1, n + 1):
            out({"type": "stream_event", "event": {"type": "content_block_delta",
                                                   "delta": {"type": "text_delta", "text": f"{i} "}}})
            _t.sleep(0.1)
        whole = "".join(f"{i} " for i in range(1, n + 1))
        out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": whole}]}})
        result(whole)
    elif "fail" in prompt:
        result("Something went wrong", err=True)
    elif "tools" in prompt:
        # calling eki's own tool through the in-process server
        server = next(iter(SDK_TOOLS), None)
        if not server:
            text("No tools.")
            result("No tools.")
        else:
            out({"type": "control_request", "request_id": "mcp-call",
                 "request": {"subtype": "mcp_message", "server_name": server,
                             "message": {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                         "params": {"name": "eki_capabilities", "arguments": {}}}}})
            reply = wait_answer("mcp-call")["mcp_response"]
            said = reply["result"]["content"][0]["text"]
            text(f"Tools said: {said}")
            result(f"Tools said: {said}")
    elif "elicit" in prompt:
        out({"type": "control_request", "request_id": "req-elicit", "request": {
            "subtype": "elicitation", "mcp_server_name": "gmail", "message": "Which mailbox?",
            "mode": "form", "requested_schema": {"type": "object", "properties": {"box": {"type": "string"}}}}})
        answer = wait_answer("req-elicit")
        box = (answer.get("content") or {}).get("box", "?")
        text(f"Mailbox {box}.")
        result(f"Mailbox {box}.")
    elif "think" in prompt:
        out({"type": "stream_event", "event": {"type": "message_start"}})
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "thinking_delta", "thinking": "Hmm…"}}})
        text("Thought about it.")
        result("Thought about it.")
    elif "background" in prompt:
        # a task started in the background: the turn ends, and a moment
        # later the program carries on by itself with a turn of its own
        out({"type": "system", "subtype": "task_started", "task_id": "t1", "description": "curl model.gguf"})
        text("Started the download in the background.")
        result("Started the download in the background.")
        import time as _t
        _t.sleep(0.3)
        out({"type": "system", "subtype": "task_progress", "task_id": "t1", "description": "curl model.gguf",
             "summary": "2.0 of 7.6 GB", "usage": {"total_tokens": 0, "tool_uses": 1, "duration_ms": 300}})
        out({"type": "system", "subtype": "task_notification", "task_id": "t1", "status": "completed",
             "output_file": "/tmp/t1.txt", "summary": "model.gguf downloaded"})
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu9", "name": "Bash", "input": {"command": "ls downloads"}}]}})
        text("The download finished; the file is in place.")
        result("The download finished; the file is in place.")
    else:
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu0", "name": "Read", "input": {"file_path": "/tmp/x/README.md"}}]}})
        text(f"Echo: {prompt} ({MODEL})")
        result(f"Echo: {prompt}")
