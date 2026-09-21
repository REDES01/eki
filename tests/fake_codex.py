# SPDX-License-Identifier: Apache-2.0
"""A stand-in for `codex app-server`, for tests: JSON-RPC over stdio with
just the methods and notifications eki uses."""
import json
import sys

ARGV = sys.argv[1:]


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def notify(method, params):
    out({"jsonrpc": "2.0", "method": method, "params": params})


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def wait_reply(rid):
    while True:
        msg = read()
        if msg.get("id") == rid and "method" not in msg:
            return msg.get("result") or {}


THREAD = "thread-1"
MODEL = ""
turns = 0
usage = {"total": {"totalTokens": 0, "inputTokens": 0, "outputTokens": 0}}

while True:
    msg = read()
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        out({"jsonrpc": "2.0", "id": rid, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        MODEL = params.get("model") or ""
        THREAD = "thread-%d" % (turns + 1)
        out({"jsonrpc": "2.0", "id": rid, "result": {"thread": {"id": THREAD}}})
        notify("thread/started", {"thread": {"id": THREAD}})
    elif method == "thread/resume":
        if params.get("threadId") == "gone":
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "no such thread: gone"}})
            continue
        THREAD = params["threadId"]
        MODEL = params.get("model") or MODEL
        out({"jsonrpc": "2.0", "id": rid, "result": {"thread": {"id": THREAD}}})
    elif method == "thread/compact/start":
        out({"jsonrpc": "2.0", "id": rid, "result": {}})
        notify("thread/compacted", {"threadId": THREAD})
    elif method == "turn/interrupt":
        out({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "turn/start":
        turns += 1
        text = params["input"][0]["text"]
        model = params.get("model") or MODEL
        turn = {"id": "turn-%d" % turns}
        out({"jsonrpc": "2.0", "id": rid, "result": {"turn": turn}})
        notify("turn/started", {"threadId": THREAD, "turn": turn})
        if "ask" in text:
            notify("item/started", {"item": {"type": "agentMessage", "id": "m0", "text": ""}})
            out({"jsonrpc": "2.0", "id": 0, "method": "item/tool/requestUserInput",
                 "params": {"threadId": THREAD, "turnId": turn["id"], "itemId": "call_1", "isBlocking": True,
                            "questions": [{"id": "colour", "header": "Colour", "question": "Red or blue?",
                                           "isOther": True, "options": [{"label": "Red", "description": ""},
                                                                        {"label": "Blue", "description": ""}]}]}})
            answer = wait_reply(0)
            chosen = (answer.get("answers") or {}).get("colour", {}).get("answers", ["?"])[0]
            reply = f"You chose {chosen}."
        elif "run" in text:
            notify("item/started", {"item": {"type": "commandExecution", "id": "c1", "command": "/bin/zsh -lc 'rm -rf build'"}})
            out({"jsonrpc": "2.0", "id": 1, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": THREAD, "turnId": turn["id"], "itemId": "c1",
                            "command": "/bin/zsh -lc 'rm -rf build'", "cwd": "/tmp", "reason": "cleanup"}})
            decision = wait_reply(1).get("decision")
            if decision in ("accept", "acceptForSession"):
                notify("item/completed", {"item": {"type": "commandExecution", "id": "c1", "status": "completed"}})
                reply = f"Removed build ({decision})."
            else:
                notify("item/completed", {"item": {"type": "commandExecution", "id": "c1", "status": "declined"}})
                reply = "Understood, I won't."
        else:
            notify("item/started", {"item": {"type": "commandExecution", "id": "c0", "command": "/bin/zsh -lc 'ls'"}})
            notify("item/completed", {"item": {"type": "commandExecution", "id": "c0", "status": "completed"}})
            notify("item/started", {"item": {"type": "fileChange", "id": "f0",
                                             "changes": [{"path": "/tmp/x/a.py", "kind": {"type": "update"}}]}})
            reply = f"Echo: {text} ({model or 'default'})"
        notify("item/started", {"item": {"type": "agentMessage", "id": "m1", "text": "", "phase": "final_answer"}})
        half = len(reply) // 2
        notify("item/agentMessage/delta", {"itemId": "m1", "delta": reply[:half]})
        notify("item/agentMessage/delta", {"itemId": "m1", "delta": reply[half:]})
        notify("item/completed", {"item": {"type": "agentMessage", "id": "m1", "text": reply}})
        usage = {"total": {"totalTokens": 100 * turns, "inputTokens": 90 * turns, "outputTokens": 10 * turns},
                 "last": {"totalTokens": 100, "inputTokens": 90, "outputTokens": 10}}
        notify("thread/tokenUsage/updated", {"threadId": THREAD, "tokenUsage": usage})
        notify("turn/completed", {"threadId": THREAD, "turn": {**turn, "status": "completed", "error": None}})
    else:
        if rid is not None:
            out({"jsonrpc": "2.0", "id": rid, "result": {}})
