# SPDX-License-Identifier: Apache-2.0
"""A live check of Claude Code under eki, against the real program on this Mac.

Not a unit test: needs the engine running (`eki agent status`) and a Claude
login. Run `python tests/check_claude_live.py`; every line is one item of
docs/claude-code-checklist.md.
"""
import json, sys, time, urllib.request
B = "http://127.0.0.1:8787"
def post(path, body, timeout=180, method="POST"):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)
    except urllib.error.HTTPError as e: return {"__error": e.read().decode()[:200]}
def get(path):
    with urllib.request.urlopen(B + path, timeout=60) as r: return json.load(r)
def ctl(op, cid="", **args): return post("/api/claude/control", {"op": op, "conversation": cid, "cwd": "", "args": args})
res = []
def check(name, ok, note=""): res.append((name, ok, note)); print(("PASS " if ok else "FAIL "), name, note[:150])

r = ctl("mcp"); check("mcp status", "servers" in r and any(s["name"]=="eki" for s in r.get("servers",[])), str([(s["name"], s["status"]) for s in r.get("servers", [])]))
eki = next((s for s in r.get("servers",[]) if s["name"]=="eki"), {}); check("eki tools served (screen ones go to Codex; Claude has computer-use)", len(eki.get("tools",[]))==3, str([t["name"] for t in eki.get("tools",[])]))
cu = next((s for s in r.get("servers",[]) if s["name"]=="computer-use"), {}); check("built-in computer-use declared", len(cu.get("tools",[]))==24, str(cu.get("status")))
r = ctl("models"); check("models + account", bool(r.get("models")) and r.get("account",{}).get("email"), str([m["value"] for m in r.get("models",[])]))
r = ctl("account"); check("account/version", "account" in r, str(r.get("version")))
r = ctl("usage"); check("usage", "rate_limits" in r.get("usage",{}), json.dumps(r.get("usage",{}).get("rate_limits",{}))[:120])
r = ctl("context"); check("context", r.get("context",{}).get("maxTokens",0)>0, f"{r.get('context',{}).get('totalTokens')}/{r.get('context',{}).get('maxTokens')}")
r = ctl("rules"); check("permission rules", "rules" in r, json.dumps(r)[:100])
r = ctl("skills"); check("skills", len(r.get("skills",[]))>0, str(len(r.get("skills",[]))))
r = ctl("reload_skills"); check("reload_skills", "skills" in r, "")
r = ctl("hooks"); check("hooks", "hooks" in r, json.dumps(r)[:100])
r = ctl("agents"); check("agents", "agents" in r, str(len(r.get("agents",[]))))
r = ctl("memory"); check("memory", "memory" in r and "__error" not in r, json.dumps(r)[:120])
r = ctl("settings"); check("settings", "settings" in r and "__error" not in r, json.dumps(r)[:100])
r = ctl("background"); check("background tasks", "tasks" in r and "__error" not in r, json.dumps(r)[:100])
r = ctl("status"); check("status", r.get("alive") is True, f"model={r.get('model')!r} mode={r.get('permission_mode')!r}")
r = ctl("permission_mode", mode="plan"); check("set permission mode plan", r.get("permission_mode")=="plan", json.dumps(r)[:100])
r = ctl("permission_mode", mode="bypassPermissions"); check("set permission mode back", r.get("permission_mode")=="bypassPermissions", "")
r = ctl("set_model", model="sonnet"); check("set_model sonnet", "model" in r and "__error" not in r, json.dumps(r)[:100])
r = ctl("thinking", max_tokens=None, display="summarized"); check("thinking on", "__error" not in r, json.dumps(r)[:100])
r = ctl("output_style", style="default"); check("output style", "__error" not in r, json.dumps(r)[:100])
for lvl in ("low", "max"):
    ctl("effort", effort=lvl)
    got = ctl("settings").get("settings", {}).get("applied", {}).get("effort")
    check(f"effort {lvl} applied (read back)", got == lvl, f"applied={got}")
r = ctl("nothing"); check("unknown op refused", "__error" in r, r.get("__error",""))

# registry → claude session (mcp_apply) and codex config
reg = post("/api/mcp/eki-check-fs", {"name":"eki-check-fs","command":"npx -y @modelcontextprotocol/server-filesystem /tmp","backends":["claude","codex"]}, method="PUT")
check("registry put", "eki-check-fs" in reg.get("servers",{}), "")
toml = open(__import__("os").path.expanduser("~/.codex/config.toml")).read()
check("codex config block", "[mcp_servers.eki-check-fs]" in toml and "[mcp_servers.eki]" in toml, "")
r = ctl("mcp_apply"); names = [s["name"] for s in r.get("servers",[])]
check("mcp_apply adds to open session", "eki-check-fs" in names and "eki" in names, str(names))
import urllib.request as u
req = u.Request(B + "/api/mcp/eki-check-fs", method="DELETE"); u.urlopen(req).read()
toml = open(__import__("os").path.expanduser("~/.codex/config.toml")).read()
check("registry remove + codex block rewritten", "[mcp_servers.eki-check-fs]" not in toml, "")

# a real turn: activity, checkpoint on the answer, rewind dry run, then a screenshot tool call
started = post("/api/ask", {"prompt": "Reply with exactly: pong", "backend": "claude_code", "conversation": "", "repo": ""})
cid, rid = started["conversation"], started["run"]
for _ in range(120):
    run = get(f"/api/runs/{rid}")
    if run["state"] in ("done","failed","cancelled"): break
    time.sleep(1)
check("live turn done", run["state"]=="done", run.get("error") or run["state"])
turns = get(f"/api/conversations/{cid}")["turns"]
meta = json.loads(turns[-1].get("meta") or "{}")
check("answer text", "pong" in turns[-1]["content"].lower(), turns[-1]["content"][:60])
check("checkpoint kept on the answer", bool(meta.get("checkpoint")), str(meta.get("checkpoint")))
r = ctl("rewind", cid, turn=turns[-1]["id"], dry_run=True); check("rewind dry run", "rewind" in r and "__error" not in r, json.dumps(r)[:120])
r = ctl("status", cid); check("status after a turn", bool(r.get("model")) and bool(r.get("session")), f"model={r.get('model')} mode={r.get('permission_mode')} version={r.get('version')}")
r = ctl("rename", cid, title="eki check"); check("rename session", "__error" not in r, json.dumps(r)[:80])
started = post("/api/ask", {"prompt": "Use eki_capabilities, then the computer-use screenshot tool (mcp__computer-use__screenshot, calling request_access first if it asks), and answer in one line with what eki_capabilities said and whether the screenshot worked; if it errored, quote the error exactly.", "backend": "claude_code", "conversation": cid, "repo": ""})
rid = started["run"]
for _ in range(180):
    run = get(f"/api/runs/{rid}")
    if run["state"] in ("done","failed","cancelled"): break
    time.sleep(1)
turns = get(f"/api/conversations/{cid}")["turns"]
check("tools turn (eki_capabilities + computer-use screenshot)", run["state"]=="done" and "qwen" in turns[-1]["content"].lower(), turns[-1]["content"][:220])
print("\n%d/%d passed" % (sum(1 for _,ok,_ in res if ok), len(res)))
json.dump(res, open("/tmp/eki_check.json","w"))
