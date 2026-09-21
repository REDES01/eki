# SPDX-License-Identifier: Apache-2.0
"""A live check of Codex under eki, against the real program on this Mac.

Not a unit test: needs the engine running and a Codex login. Run
`python tests/check_codex_live.py`; each line is an item of
docs/claude-code-checklist.md (Codex column).
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
def ctl(op, cid="", **args): return post("/api/agent/control", {"op": op, "conversation": cid, "cwd": "", "backend": "codex", "args": args})
res = []
def check(name, ok, note=""): res.append((name, ok, note)); print(("PASS " if ok else "FAIL "), name, str(note)[:150])

r = ctl("mcp"); check("mcp status", "servers" in r and r["servers"], str([(s["name"], s["status"], len(s["tools"])) for s in r.get("servers", [])]))
r = ctl("models"); check("models + account", bool(r.get("models")) and r.get("account", {}).get("email"), str([m["value"] for m in r.get("models", [])][:5]))
r = ctl("usage"); check("usage (rate limits)", bool(r.get("usage", {}).get("rate_limits", {}).get("model_scoped")), json.dumps(r.get("usage", {}).get("rate_limits"))[:150])
r = ctl("context"); check("context", "context" in r, json.dumps(r.get("context"))[:120])
r = ctl("rules"); check("permission profiles", bool(r.get("rules", {}).get("state", {}).get("rules")), json.dumps(r)[:120])
r = ctl("skills"); check("skills", len(r.get("skills", [])) > 0, str(len(r.get("skills", []))))
r = ctl("hooks"); check("hooks", "hooks" in r and "__error" not in r, json.dumps(r)[:100])
r = ctl("settings"); check("config/read", "config" in r.get("settings", {}), json.dumps(r)[:100])
r = ctl("plugins"); check("plugins", "marketplaces" in r.get("plugins", {}), str(len(r.get("plugins", {}).get("marketplaces", []))))
r = ctl("status"); check("status", r.get("alive") is True, f"model={r.get('model')!r}")
r = ctl("permission_mode", mode="plan"); check("permission mode plan (read-only sandbox)", r.get("permission_mode") == "plan", json.dumps(r)[:80])
r = ctl("permission_mode", mode="bypassPermissions"); check("permission mode back", r.get("permission_mode") == "bypassPermissions", "")
r = ctl("effort", effort="low"); check("effort", r.get("effort") == "low", "")
r = ctl("mcp_apply"); check("mcp_apply (config reload)", "servers" in r and "__error" not in r, str(r.get("__error", ""))[:100])
r = ctl("mcp_toggle", name="x", enabled=False); check("toggle refused with a reason", "__error" in r, r.get("__error", "")[:80])
r = ctl("nothing"); check("unknown op refused", "__error" in r, r.get("__error", ""))
# the commands for the picked backend, and nothing for Auto
cmds = [c["name"] for c in get("/api/commands?backend=codex")["commands"]]
check("codex commands + panels, no /model", "compact" in cmds and "mcp" in cmds and "plugins" in cmds and "model" not in cmds and "memory" not in cmds, str(cmds)[:150])
check("Auto: no commands", get("/api/commands")["commands"] == [], "")
# a real turn, then rename and rollback on that thread
started = post("/api/ask", {"prompt": "Reply with exactly: pong", "backend": "codex", "conversation": "", "repo": ""})
cid, rid = started["conversation"], started["run"]
for _ in range(150):
    run = get(f"/api/runs/{rid}")
    if run["state"] in ("done", "failed", "cancelled"): break
    time.sleep(1)
check("live turn done", run["state"] == "done", run.get("error") or run["state"])
turns = get(f"/api/conversations/{cid}")["turns"]
check("answer text", "pong" in turns[-1]["content"].lower(), turns[-1]["content"][:60])
r = ctl("rename", cid, title="eki codex check"); check("rename thread", "__error" not in r, json.dumps(r)[:80])
r = ctl("rewind", cid, turns=1, dry_run=True); check("rewind dry run", r.get("rewind", {}).get("canRewind") is True, json.dumps(r)[:80])
r = ctl("status", cid); check("status after a turn", bool(r.get("session")), f"model={r.get('model')} mode={r.get('permission_mode')}")
print("\n%d/%d passed" % (sum(1 for _, ok, _ in res if ok), len(res)))
