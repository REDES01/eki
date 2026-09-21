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


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def wait_answer(request_id):
    while True:
        msg = read()
        if msg.get("type") == "control_response" and msg["response"]["request_id"] == request_id:
            return msg["response"]["response"]


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


if "--resume" in ARGV and SESSION == "gone":
    sys.stderr.write("No conversation found with session ID: gone\n")
    sys.exit(1)

while True:
    msg = read()
    if msg.get("type") == "control_request" and msg["request"]["subtype"] == "initialize":
        out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"],
             "response": {"commands": [{"name": "compact", "description": "Summarise"},
                                       {"name": "doctor", "description": "Check"},
                                       {"name": "review", "description": "Review changes"}]}}})
        out({"type": "system", "subtype": "init", "session_id": SESSION, "model": MODEL,
             "permissionMode": "default", "terminal_slash_commands": ["doctor"]})
        continue
    if msg.get("type") == "control_request" and msg["request"]["subtype"] == "set_model":
        MODEL = msg["request"]["model"]
        out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"], "response": {}}})
        continue
    if msg.get("type") != "user":
        continue
    prompt = msg["message"]["content"][0]["text"]
    if prompt.startswith("/compact"):
        out({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual", "pre_tokens": 1000}})
        result("")
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
    elif "fail" in prompt:
        result("Something went wrong", err=True)
    else:
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu0", "name": "Read", "input": {"file_path": "/tmp/x/README.md"}}]}})
        text(f"Echo: {prompt} ({MODEL})")
        result(f"Echo: {prompt}")
