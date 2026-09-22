# Claude Code and Codex under eki — checklist

What each terminal can do, and whether eki does it. Checked 2026-09-22 on
xz's Mac against Claude Code 2.1.278 (Claude Pro) and Codex 0.154.0
(ChatGPT free). Engine items are exercised by `tests/check_claude_live.py`
(33 checks) and `tests/check_codex_live.py` (23 checks), both passing that
day; app items were driven in Eki.app by eki's own input helper and read
off screenshots.

Legend: ✅ works · 🟡 works with a caveat · ⬜ not done · ➖ terminal-only by nature

## The plan this was checked against

1. **Scope.** A program's commands and panels show only when that program
   is the picked backend: Claude Code's under `claude_code`, Codex's under
   `codex`/`codex-*`. Under Auto or a local model, `/` shows a hint and a
   typed panel command is refused, not sent to whatever the router picks.
   Commands shared by every provider under Auto are a later, careful step.
2. **No `/model` panel.** eki's picker beside the composer owns the model
   for every backend; `/model` typed says so. Effort, thinking and output
   style live in the Settings panel.
3. **Codex parity.** The same panels, drawn from Codex's app-server, with
   replies put in the shape Claude Code's control channel gives, so one
   panel draws both (`Engine.codex_control`).
4. **Verify.** Unit tests with the fake programs; the two live scripts
   against the real ones; the app driven by hand for each backend.

## Scoping (app, by screenshot)

| | Picked backend | `/` menu | Permissions + panels button | `/mcp` typed |
|---|---|---|---|---|
| ✅ | Auto | "Pick Claude Code or Codex beside the composer for its commands" | hidden | refused with that hint (was sent to qwen before the fix) |
| ✅ | claude_code | Claude Code's commands + its panels, no `/model` | shown | Claude Code's servers |
| ✅ | codex-qwen (Codex on a local model) | Codex's commands + its panels, no `/model` | shown | Codex's servers |
| ✅ | `/model` under either | — | — | "The model is picked beside the composer" |

## The conversation

| | Item | How |
|---|---|---|
| ✅ | Streaming answer | live turn, `Reply with exactly: pong` |
| ✅ | Thinking shown in grey | `thinking` events → `ThinkingLines` |
| ✅ | Tool calls as lines | `ActivityLines` |
| ✅ | Permission prompt → card, Allow / Always allow / Deny | `PermissionCard` (Settings → Permissions "ask") |
| ✅ | Questions (`AskUserQuestion`) → card | `AskCard` |
| ✅ | MCP server asking (elicitation) → form / link card | `ElicitationCard` (unit-tested; no live server asked that day) |
| ✅ | Dialogs eki has no drawing for → plain card | `DialogCard` |
| ✅ | Slash commands in the composer, narrowed as you type | `CommandMenu` |
| ✅ | Skills and plugin commands run as in the terminal | sent as text |
| ✅ | Background tasks finishing on their own | the thread stirs |
| ✅ | Session resumes after the engine restarts | `--resume` by id |
| ✅ | Context meter above the composer | `context` events |
| ✅ | Compaction noted in the thread | `compact_boundary` |

## The panels (typed as `/command`, or the sliders button)

| | Panel | Claude Code: op → request | Codex: request | Checked |
|---|---|---|---|---|
| ✅ | `/mcp` — status, tools, Reconnect, Enable/Disable | `mcp` → `mcp_status`, `mcp_toggle`, `mcp_reconnect` | `mcpServerStatus/list`; Reconnect = `config/mcpServer/reload`; no toggle (says so) | live + app, both |
| 🟡 | `/mcp` — Authenticate a connector | `mcp_authenticate` | `mcpServer/oauth/login` | the program runs its OAuth flow; not completed that day |
| ✅ | `/mcp` — eki's registry: Add, backends, Enable, Remove, Apply to session | `PUT/POST/DELETE /api/mcp`, `mcp_apply` → `mcp_set_servers` | managed block in config.toml + `config/mcpServer/reload`; `eki` (9 tools) seen connected | live |
| ✅ | `/mcp` — Share with Codex (import) | `mcp_import` | — (Codex → Claude import not needed: the registry already renders to both) | unit-tested |
| 🟡 | `/mcp` — Built-in computer-use, 24 tools | opt-in (`claude_builtin_computer_use`); connects, but its per-app approval is a dialog only Claude Code's own front ends show, so headless `request_access` grants nothing on 2.1.278 | Codex has its own `computer-use` (pending until used) | live: connected, then `granted: []` |
| ➖ | `/model` — pick a model | `models`, `set_model` | — removed: eki's picker owns the model | app |
| ✅ | `/model` — effort | `update_settings {effort}` | `effort` on `turn/start` | live (Settings panel) |
| ✅ | `/model` — thinking on/off | `thinking` → `set_max_thinking_tokens` | — (Codex: reasoning effort instead) | request accepted |
| ✅ | Permission mode menu (Shift+Tab) | `permission_mode` → `set_permission_mode` | `sandboxPolicy` + `approvalPolicy` on `turn/start`: plan = read-only, bypass = full access | live: plan ↔ bypass |
| ✅ | `/permissions` | `rules` → `list_permission_rules` | `permissionProfile/list` + this thread's sandbox | live + app |
| ✅ | `/usage`, `/cost` | `usage` → `get_usage` | `account/rateLimits/read` + `account/usage/read` (30-day window) | live + app |
| ✅ | `/context` | `context` → `get_context_usage` | the last request's tokens from `thread/tokenUsage/updated` | live + app |
| 🟡 | `/rewind`, *Rewind files* on an answer | `rewind` → `rewind_files` with the turn's checkpoint | `thread/rollback` (whole turns) | live: dry run |
| ✅ | `/tasks` — background tasks, Stop | `background`, `stop_task` | — (not in Codex) | live (none running) |
| ✅ | `/agents` | `agents` (from the handshake) | — (not in Codex) | live: 5 |
| ✅ | `/hooks` | `hooks` → `get_hooks_listing` | `hooks/list` | live |
| ✅ | `/skills`, Reload, Use | `skills` → `reload_skills` | `skills/list` | live: 17 + app |
| ✅ | `/plugins` | — | `plugin/list` | live: 3 marketplaces + app |
| ✅ | `/status`, `/help` | `status`, `account` | thread, account, model from `config/read` | live + app |
| ✅ | `/config`, `/output-style` | `settings`, `output_style` → `get_settings`, `update_settings` | `config/read` | live |
| ✅ | `/memory` | `memory` → `get_memory_dialog` | — (not in Codex) | live |
| ✅ | Rename session | `rename` → `rename_session` | `thread/name/set` | live |
| ✅ | Unknown request refused, not guessed | `nothing` → 409 | same | live |

## Tools eki gives the program

| | Item | Checked |
|---|---|---|
| ✅ | `eki` served in-process (`mcp_message`): `eki_capabilities`, `eki_ask`, `eki_image` | live: a turn called `eki_capabilities` and listed 9 backends |
| ✅ | Settings → *Give Claude Code eki's tools* on/off | live: `eki` disappears/returns |
| ✅ | Settings → *Computer use* on/off | live: the screen tools present or not; idle sessions reopened |
| ✅ | Computer use acting (screenshot, open app, keypress) | eki's own tools through `eki-hid`: a Claude thread took a screenshot and described it, opened Finder, pressed Escape — all under the engine |
| ✅ | macOS permission guidance | `eki-hid check`/`ask`: the helper refuses with a clear line when Accessibility or Screen Recording is missing and puts up the system prompt; eki turns that line into a card with the settings one click away, and the same buttons sit beside the switch in Settings (unit-tested; not triggered live since both were granted) |
| ✅ | Codex gets the same tools through `eki mcp` (with eki's own screen tools on a Mac) | managed block in `~/.codex/config.toml`; stdio server unit-tested |
| ✅ | `eki-hid` input helper builds and answers `screen` | 1728×1117 |

## Not covered

| | Item | Why |
|---|---|---|
| ➖ | `/login`, `/logout`, `/theme`, `/vim`, `/terminal-setup`, `/doctor`, `/color` | about the terminal; sign in once there |
| ⬜ | Images pasted into a prompt | not yet |
| ⬜ | `@file` completion in the composer | `file_suggestions` exists in the protocol; not yet |
| ✅ | Codex parity for the panels | done this pass; `tests/check_codex_live.py` |

## Re-running

    python tests/check_claude_live.py      # engine + protocol, ~2 min, spends two short Claude turns
    python tests/check_codex_live.py       # the same for Codex, one short turn
    ./mac/build_app.sh && open Eki.app      # then type /mcp, /model, /skills … in a Claude thread
