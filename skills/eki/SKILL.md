---
name: eki
description: Hand work to eki, the station on this Mac — ask another model (the local model, Claude Code, Codex) or start work that keeps running on its own. Use when a task needs a different model, a second agent on a separate job, or something that should run in the background.
---

eki runs every request as a *run*: routed to a provider, written down, and
kept going through restarts. From a shell:

```
eki ask "…"                    # routed (eki says where and why); prints the answer
eki ask --to local "…"         # a named provider: local, claude, codex (see `eki providers`)
eki ask -C <folder> "…"        # work in that folder
eki ask --bg "…"               # don't wait: prints a run id
eki ask --bg --background "…"  # only when the Mac has room (plugged in, memory free)
eki follow <run> | eki show <run> | eki runs | eki cancel <run>
eki route "…"                  # where a request would go, and why, without running it
```

Rules of thumb:

- A run you start from inside eki is one level down; don't start runs in a
  loop.
- `eki ask` blocks until the answer is done; use `--bg` for long work and
  come back with `eki follow <run>`.
- If a run asks a question, it waits for the person — `eki answer` lists
  what's waiting. Don't answer on their behalf.
