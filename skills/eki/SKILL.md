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
eki providers                  # each provider, whether it can take work, and what it can do (its can tags)
```

Rules of thumb:

- A run you start from inside eki is one level down; don't start runs in a
  loop.
- `eki ask` blocks until the answer is done; use `--bg` for long work and
  come back with `eki follow <run>`.
- If a run asks a question, it waits for the person — `eki answer` lists
  what's waiting. Don't answer on their behalf.

## eki self — eki changes itself

A goal in words is planned into small items; each item is built by an agent
in a git worktree of its own and judged by `bin/check`; a change that passes
is proposed as a branch `self/<id>` (merge it, or `eki swap self/<id>` to put
it live). Use it when the change is to eki itself — its code, docs or skills —
not to the person's project. Don't start it in a loop, or from inside a
self-build item.

Fit items join a queue (under autonomy `apply` by themselves, under `propose`
by `eki self apply`): each is rebased on the ones ahead, checked again, landed
in eki's integration repo and put live by the train. An item that touches a
hard-locked file (the launcher, `bin/check`, the drill, quotas, …) waits for a
person's `eki self apply <item> --yes`.

```
eki self "…"            # plan a goal into items and build them side by side
eki self --one "…"      # no planning: the goal is one item
eki self                # the board: goals, their items, and where each one stands
eki self show <item>    # one item in full
eki self diff <item>    # what it changed
eki self follow <item>  # its build run, live
eki self drop <item> | eki self retry <item>
eki self apply <item> [--yes]   # queue a proposed item (--yes: one that touches locked files)
eki self release        # put integration main live now (the train, by hand)
eki self autonomy apply|propose # queue and release by itself, or wait for you
```

## Other commands

```
eki threads [<thread>]          # list threads, or print one as a conversation
eki engine status|start|stop    # start, stop or check the engine; install it as a login agent
eki providers                   # list providers and whether each can take work now
eki models [start|stop <name>]  # list local models; start or stop one
eki skills [add|remove|sync]    # list, add or remove skills (shared by every program)
eki mcp [add|remove <name>]     # list, add or remove MCP servers (shared by every program)
eki open                        # open eki's window
eki drill                       # prove a restart loses nothing (runs in a throwaway home)
eki swap <commit> | --back      # make a build of a commit, check it, and put it live
eki builds                      # list eki's builds: current, previous, and how the last swap went
eki observe [--since 24h] [--kind fault|handoff|…] [--full]  # what eki wrote down: faults, handoffs, corrections, limits
```
