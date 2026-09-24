# What a run may do

A run **you** start — in a chat, from the command line, or by a goal you set
up — is the top of the tree: it runs under Settings → Permissions as it
always has. A run an **agent** starts through eki (`eki ask` from its shell,
`eki_ask` / `eki_image` from its tools) gets what its parent hands it, never
more. That is decided once in eki (`eki/grant.py`) and rendered into each
program's own settings.

## What a child is handed

| The agent asks for | The child gets |
|---|---|
| a question or a review (no folder) | **read** — reads and answers, edits nothing |
| `--repo` / `repo` — a delegated coding task | **write** — edits its thread's copy of the folder (`docs/worktrees.md`) |
| `--read-only` with a folder | **read** in that folder |
| `--allow CMD` / `commands` | those commands, and no others (none if none are named) |
| `--write PATH` | that path too, besides its copy |
| a picture (`--image`, `eki_image`) | writes where pictures go (`~/.eki/images`), nothing else |

Then it is cut to what the parent has: the lower level, only paths inside
the parent's, only commands the parent's cover (`git` covers `git log`; not
the other way round). The grant travels into the child's programs as
`EKI_GRANT`, so an `eki ask` from inside a narrowed run is narrowed again.

```
eki ask "review the diff in ~/code/thing" --read-only
eki ask "make the tests pass" --repo ~/code/thing --allow pytest --allow "git diff"
```

## How each program is told

A narrowed run runs headless — nobody is watching it, so what it may not do
is refused, not asked. It doesn't use a kept-open session.

- **Claude Code** (`claude -p`): `--permission-mode` (`default` to read,
  `acceptEdits` to write), `--allowedTools` with the read tools, the edit
  tools when it may write and `Bash(cmd:*)` per command, `--disallowedTools`
  for the rest, `--add-dir` per extra path. MCP tools aren't pre-allowed, so
  headless they are refused.
- **Codex** (`codex exec`), and the local models that borrow its harness
  through the gateway: `-s read-only` or `-s workspace-write` with the extra
  paths as `sandbox_workspace_write.writable_roots`. Codex takes no
  per-command list; its sandbox keeps whatever runs to the folder and off
  the network.
- **Gemini CLI**: `--approval-mode default` to read, `auto_edit` to write.
- Models without tools (MLX, GGUF, APIs) and image backends have nothing to
  render: they can't touch files.

The thread shows what the run was handed beside its routing reason.

## What it wasn't allowed

A refused tool call doesn't stop the run: the program is told no, and carries
on or fails. Claude Code lists what it was denied when it finishes
(`permission_denials`), and the thread says so under the answer, whether the
run finished or failed:

```
eki: this run wasn't allowed to run `pytest -q`; edit `~/code/thing/notes.md` —
`eki allow 3f2a…` allows it and runs the request again
```

*Allow and rerun* — `eki allow <run>`, or **Allow and rerun** under the
answer in the app — asks the same question again, once, under the run's grant
plus what it wanted: the command's program and verb (`git push`, `pytest`),
edits and the folder of a file it wanted to write. It stays a narrowed,
headless run; a refused MCP tool is described but no grant covers it. Asked
from inside a narrowed run, `eki allow` is still bounded by that run's grant.
Each refusal is noted in eki's journal (`friction`, `refused`).

## Known edges

- Only Claude Code says what it was denied. Codex's sandbox refuses inside
  the command (a write fails, the network is off), and `codex exec` doesn't
  report it as a refusal, so a Codex child's refusals show only in its answer.
- The grant is sent by the asking process; the bound that holds whatever it
  claims is the program's own sandbox and tool lists.
