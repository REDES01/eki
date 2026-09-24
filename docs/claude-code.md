# Claude Code under eki

How eki drives Claude Code, and what of the terminal it draws itself.
The program is the genuine binary on your own login; eki runs it in its
two-way streaming mode (`--input-format stream-json --output-format
stream-json --permission-prompt-tool stdio`) and never reads a token.

## The protocol (eki/live.py)

One session per thread, kept open between turns and resumed by id after
the engine restarts. Messages are JSON lines.

| Direction | Message | eki |
|---|---|---|
| → | `control_request initialize` | says which system prompt addition and which in-process tool server (`sdkMcpServers`) it brings; the reply carries the commands, models, account, agents, output styles |
| ← | `system/init` | session id, model, permission mode, MCP servers, tools, version |
| → | `user` | a turn; slash commands are ordinary text |
| ← | `stream_event` | text and thinking deltas, streamed |
| ← | `assistant`, `user` | tool calls (a line each), tool results (errors only), the user echo with the uuid `/rewind` needs |
| ← | `control_request can_use_tool` | a permission prompt, or a question (`AskUserQuestion`) → a card; answered with `control_response` |
| ← | `control_request elicitation` | an MCP server asking → a form or a link card |
| ← | `control_request request_user_dialog` | a dialog eki has no drawing for → a plain card |
| ← | `control_request mcp_message` | one JSON-RPC message for eki's in-process server → answered from eki/mcpbridge.py |
| ← | `result` | the turn's end, with usage |

## The panels (Engine.claude_control, ClaudeCode.swift)

Each terminal panel is one control request. `POST /api/claude/control`
with `{"op": …, "conversation": …, "cwd": …, "args": {…}}`:

| Panel | op | request |
|---|---|---|
| `/mcp` | `mcp`, `mcp_toggle`, `mcp_reconnect`, `mcp_authenticate`, `mcp_oauth_callback`, `mcp_clear_auth`, `mcp_import`, `mcp_apply` | `mcp_status`, `mcp_toggle`, `mcp_reconnect`, `mcp_authenticate`, `mcp_oauth_callback_url`, `mcp_clear_auth`, `mcp_set_servers` |
| `/model` | `models`, `set_model` | `list_models` (or the handshake), `set_model`; effort through `update_settings` |
| Shift+Tab | `permission_mode` | `set_permission_mode` |
| `/permissions` | `rules` | `list_permission_rules` |
| `/usage` | `usage` | `get_usage` |
| `/context` | `context` | `get_context_usage` |
| `/rewind` | `rewind` (by turn id → the answer's `checkpoint`) | `rewind_files` |
| `/agents`, `/hooks` | `agents`, `hooks` | the handshake, `get_hooks_listing` |
| — | `rename`, `background`, `stop_task`, `reload_skills`, `settings`, `update_settings`, `thinking`, `interrupt`, `status`, `account` | the same names |

A request the running build doesn't know comes back as an error, shown as
such; nothing is guessed.

## Codex, the same panels (Engine.codex_control)

`POST /api/agent/control` takes `backend`; a Codex backend routes the same
op names to Codex's app-server and puts the replies in Claude Code's
shapes, so one panel draws both: `mcp` → `mcpServerStatus/list`,
`mcp_authenticate` → `mcpServer/oauth/login`, `mcp_apply`/`mcp_reconnect`
→ `config/mcpServer/reload`, `models` → `model/list`, `usage` →
`account/rateLimits/read` + `account/usage/read`, `rules` →
`permissionProfile/list`, `permission_mode` → sandbox + approval policy on
the next `turn/start`, `effort` → `effort` on `turn/start`, `skills` →
`skills/list`, `hooks` → `hooks/list`, `settings` → `config/read`,
`plugins` → `plugin/list`, `rewind` → `thread/rollback`, `rename` →
`thread/name/set`. Codex has no tasks, agents or memory panel; `/plugins`
is Codex's. `docs/claude-code-checklist.md` has the full table.

Commands and panels show only for the picked backend. Under Auto or a
local model, `/` shows a hint and a typed panel command is refused rather
than sent to whatever the router picks. There is no `/model` panel on
either side: eki's picker beside the composer owns the model.

## eki's tools, in-process (eki/mcpbridge.py)

Claude Code lets the host register an "SDK" MCP server: one with no
process and no port, whose messages travel as `mcp_message` control
requests. eki registers `eki` that way in every session, so the agent has:

- `eki_capabilities` — the backends on this Mac and what each does
- `eki_ask` — a step delegated to another backend (a local model, a prose
  model, or routed); a run of its own, the answer returned whole
- `eki_image` — a picture from the image model; the file and the path
- `eki_screenshot`, `eki_click`, `eki_type`, `eki_key`, `eki_scroll`,
  `eki_open_app` — the screen, on macOS. Screenshots come from
  `screencapture`, scaled to points so a pixel is a click coordinate; input
  goes through `Contents/Helpers/eki-hid` (mac/tools/hid.swift, CGEvent),
  with a System Events fallback in a dev checkout. macOS asks for Screen
  Recording and Accessibility once, for Eki.

Nesting stops at `EKI_MAX_DEPTH` (3): an agent asking eki asking an agent
(eki/nesting.py). Every program eki starts for a run gets `EKI_DEPTH` (the
run's depth plus one) and `EKI_RUN` (the run's id); `eki ask` and `eki mcp`
read them, so a request from inside a run arrives one level down, and one at
the limit is refused (`eki ask` exits 1 with HTTP 429). A nested run is routed
like any other — live quota and pace apply — and takes the budget of the run
it came from, so a goal's agent asking eki spends only what the goal may.

Codex has no in-process channel, so it runs `eki mcp` — the same handlers
over stdio, reaching the running engine through its HTTP API — declared for
it in the managed block eki keeps in `~/.codex/config.toml`.

## One MCP registry (eki/mcpregistry.py)

`~/.eki/mcp.json` holds servers declared in eki. Claude Code receives the
ones enabled for it as `--mcp-config` on each session (its `dynamic`
scope; `mcp_apply` adds them to an open session without a restart); Codex
gets them in the managed block between `# --- eki: MCP servers` markers,
rewritten whole, everything else in the file untouched. Gemini CLI's
`~/.gemini/settings.json` is JSON with no room for markers, so eki notes
the names it wrote there (`~/.eki/mcp-gemini.json`) and replaces only
those; it writes nothing before Gemini CLI has made `~/.gemini`, nor into a
file that isn't plain JSON (Gemini allows comments; eki won't drop them).
A server added before Gemini CLI was a side is on for it until turned off
there. Servers Claude
Code already has from its own config or claude.ai are listed in the panel
with their scope and can be imported with *Share with Codex*.

## Coverage: the terminal, feature by feature

| In the terminal | In eki |
|---|---|
| Streaming answer, thinking in grey | the thread; thinking above the tool lines |
| Tool calls as they run | a line each (`ActivityLines`) |
| Permission prompts, Always allow | `PermissionCard` (rules added through the program) |
| Questions (`AskUserQuestion`) | `AskCard` |
| Plan mode, Shift+Tab | the permission-mode menu beside the composer |
| Slash commands, skills, plugins' commands | the `/` menu, sent as text; skills and plugin commands run as in the terminal |
| `/mcp`, sign-in of a connector | the `/mcp` panel; Authenticate runs the program's OAuth flow |
| `/model`, `/effort`, thinking on/off | the `/model` panel (`set_model`, `update_settings`, `set_max_thinking_tokens`) |
| `/permissions` | the panel (`list_permission_rules`) |
| `/usage`, `/cost` | the panel (`get_usage`) |
| `/context` | the panel (`get_context_usage`) |
| `/rewind` | the panel, and *Rewind files* on an answer (`rewind_files`) |
| `/tasks` (background) | the panel, with Stop (`background_tasks`, `stop_task`) |
| `/agents`, `/hooks` | panels |
| `/status`, `/help` | the Status panel |
| `/config`, `/output-style` | the Settings panel (`get_settings`, `update_settings`) |
| `/memory` | the Memory panel (`get_memory_dialog`) |
| `/skills`, `/reload-skills` | the Skills panel: the list with a filter, Reload, and *Use* to put `/name` in the composer (`reload_skills`) |
| `/compact`, `/clear`, `/init`, `/review`, `/rename` … | sent as text; the program does them (a compaction is noted in the thread) |
| Background tasks finishing on their own | the thread stirs and shows the turn |
| Resume a session | a thread reopens its session by id |
| MCP servers asking (elicitation) | `ElicitationCard`: a form or a link |
| Built-in `computer-use` server | opt-in (Settings → Routing): started from the binary (`claude --computer-use-mcp`) and added after the handshake; connects, but its per-app approval dialog never reaches a headless host on 2.1.278, so it grants nothing. eki's own screen tools (`eki_screenshot`…) are the computer use that works |
| `/login`, `/logout`, `/theme`, `/vim`, `/terminal-setup`, `/doctor`, `/color` | terminal-only; sign in once in a terminal |
| Images pasted into the prompt, `@file` completion | not yet |

## Computer use on and off

Settings → Routing: *Give Claude Code eki's tools* is the in-process
server (`eki_ask`, `eki_image`, `eki_capabilities`); *Computer use* adds
Claude Code's own built-in computer-use server to each session on a Mac
(the terminal's 24 tools — the headless program doesn't bring it by itself,
but its binary runs it as a stdio server, `claude --computer-use-mcp`, and
the reserved name only takes after the handshake, so eki sends it with
`mcp_set_servers`). Turning either off closes idle sessions so the next
turn opens without those tools; a thread mid-turn keeps what it had until
it ends. Codex has no built-in, so for it *Computer use* means eki's own
screen tools in `eki mcp`. macOS asks for Accessibility and Screen
Recording for the process that runs the server the first time it acts.

## What the terminal has that eki doesn't draw

`/login`, `/theme`, `/vim`, `/terminal-setup`, `/doctor`, `/resume`: they
are about the terminal, not the conversation. Sign in once in a terminal;
eki uses that login by running the program as you.
