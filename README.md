# claude-agents-mcp

MCP server that wraps Claude Code background agents by driving the
`claude agents` TUI inside a persistent **tmux** session. External clients spawn,
observe, message, and abort agents through structured tools; the server
translates each tool call into verified TUI navigation (send-keys +
capture-pane).

This replaces the earlier `claude -p` (print mode) design — `-p` is being
removed from the Max plan, so the server now controls the interactive agents
view instead.

## How it works

The server owns one tmux session (default name `claude-agents-mcp`) running
`claude agents`. Every tool call:

1. Ensures the session exists and the TUI is up (restarting `claude agents` if
   it dropped to a shell, clearing the first-run trust dialog).
2. Sends keystrokes to reach the needed state.
3. **After every keypress, settles ~0.5 s, re-captures the pane, and verifies
   the resulting state before sending more keys.** No blind key sequences.

The TUI has two main views:

- **Overview** — header counts (`N awaiting input · N working · N completed`),
  collapsible Working/Completed sections, and a new-session input box. The
  selected row is rendered as a background color, invisible in `capture-pane -p`
  but visible in `capture-pane -e` (SGR `48;5;255`) — navigation matches the
  highlighted row, never a blind count of `Down` presses.
- **Chat** — one agent's conversation. The rule line above the input box carries
  the agent title. `Left` returns to the overview only when the input is empty.

Key bindings used: `Enter` opens the selected agent / submits the new-session
prompt; `Left` returns to overview; `Ctrl+X` then `Ctrl+X` deletes (and kills)
an agent; `C-a C-k` clears the input box reliably.

## Agent identity (important)

Agents are addressed by their **TUI title** — the `title` shown in
`list_agents`. A unique prefix is enough.

- The title is what the TUI displays and what selection/navigation match on.
- `claude agents --json` is **supplementary only**: its `name` field is a
  session-id prefix that does *not* equal the title, its ordering differs from
  the overview, and it can list stale/ghost sessions that were already deleted.
  Use `list_sessions` only when you need a best-effort `session_id`/`pid`.
- **Titles auto-update.** A freshly spawned agent's title starts as a truncated
  prompt and is replaced by a model-generated summary within ~a minute. Re-call
  `list_agents` to get current titles before acting on a recently spawned agent.

## Install

```sh
uv venv -p python3.11
uv pip install -e ".[dev]"
```

Requires `tmux` and `claude` on `PATH`.

## Run

```sh
.venv/bin/claude-agents-mcp        # stdio transport, FastMCP default
```

Wire into a client's MCP config:

```json
{
  "mcpServers": {
    "claude-agents": {
      "command": "/abs/path/to/.venv/bin/claude-agents-mcp"
    }
  }
}
```

## Tools

- `spawn_agent(prompt, cwd?)` — type a new-session prompt into the overview and
  submit it. Confirms the agent started (a new overview row appears) and returns
  `{spawned, status, note}` — **no reusable handle**. The TUI auto-generates the
  agent's display name a few seconds after start (derived from its work), so the
  name is not knowable at spawn time, and no navigable session id is exposed.
  There is no reconnect handle: to find/address the agent afterward, call
  `list_agents` and choose it by its (now-stable) name/description. `cwd` selects
  the agents-view working directory (effective on first spawn — one tmux session
  is bound to one cwd).
- `list_agents(status_filter?)` — parse the overview (the source of truth).
  Returns `[{title, status, description, age, section}]`. Filter by normalized
  status `running|done|awaiting_input`.
- `list_sessions()` — raw `claude agents --json` (supplementary; see above).
- `get_agent_output(agent)` — open the agent's chat and parse the **visible**
  viewport into `{title, agent, events:[{type, text}]}` where `type` is
  `user|assistant|recap`. Only the on-screen portion is captured (the TUI is a
  full-screen app), so this is the most recent conversation, not full history.
- `send_to_agent(agent, message)` — open the chat, type the raw message into the
  input box, submit. (Always free text; never touches a choice menu.)
- `select_option(agent, option)` — answer an agent's **choice-menu** question
  (AskUserQuestion renders as an arrow-key menu in the chat). `option` is the
  option number (`"2"`) or a case-insensitive substring of an option's
  label/description (`"Second"`). Navigates the highlight to the option (arrow
  keys, verifying after each press) and confirms. Raises `NOT_A_MENU` if the
  chat isn't a menu, `SELECT_FAILED` if it can't navigate/verify.
- `reply_to_agent(agent, answer)` — reply to an agent awaiting input.
  **Menu-aware**: if the chat is a choice menu, routes `answer` through the
  menu's free-text "Type something" option; otherwise types it into the normal
  input box (the `send_to_agent` path).
- `open_agent(agent)` / `return_to_overview()` — explicit navigation.
- `abort_agent(agent)` — select + `Ctrl+X` + confirm `Ctrl+X`. Deleting from the
  agents view kills the agent process.
- `get_tui_state()` — debug: `{view, chat_agent, input_text, counts, selected}`.

## Status values

From the overview section an agent sits in:

- `running` — in the **Working** section.
- `done` — in the **Completed** section.
- `awaiting_input` — in the **Awaiting input** section (agent asked a question;
  use `reply_to_agent`).

## Configuration

Environment variables:

- `CLAUDE_AGENTS_MCP_CLAUDE_BIN` — path to the `claude` binary (default `claude`).
- `CLAUDE_AGENTS_MCP_TMUX_SESSION` — tmux session name (default
  `claude-agents-mcp`).
- `CLAUDE_AGENTS_MCP_CWD` — working directory for the agents view (default: the
  server's cwd).

## Errors

`ToolError` with stable codes: `SPAWN_FAILED`, `LIST_FAILED`, `READ_FAILED`,
`SEND_FAILED`, `OPEN_FAILED`, `NAV_FAILED`, `ABORT_FAILED`, `AGENT_NOT_FOUND`,
`TUI_UNAVAILABLE`.

## Limits & caveats

- **Title instability** right after spawn (see *Agent identity*).
- `get_agent_output` returns only the visible chat viewport; long histories are
  truncated to what's on screen.
- TUI-spawned background agents do not reliably write `~/.claude/projects/.../
  <sid>.jsonl` transcripts, which is why output is read from the chat pane.
- One tmux session ⇒ one cwd. Spawning into a different `cwd` uses a separate
  controller/session.
- State detection and navigation are timing-sensitive; the server settles after
  each keypress and verifies, but a heavily loaded machine may need larger
  settle times.

## Module layout

- `tui_state.py` — pure parsing of `capture-pane` output (view classification,
  overview rows, chat transcript, selection highlight). No I/O; heavily tested.
- `tmux_controller.py` — tmux session lifecycle, key sending (literal text via
  paste-buffer), and verified navigation/actions.
- `status.py` — supplementary `claude agents --json` listing.
- `spawner.py` — spawn via the controller, detect the new agent by diffing
  overview titles.
- `server.py` — FastMCP tool definitions.

## Tests

```sh
.venv/bin/pytest -q
```

`tui_state` is tested against captured TUI fixtures; `tmux_controller` against a
fake tmux runner that returns scripted captures; `status`/`spawner`/`server`
with injected fakes. No real tmux, `claude`, or network calls in the suite.
