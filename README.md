# claude-agents-mcp

MCP server that wraps Claude Code background agents. Lets external clients
spawn, observe, and abort agents through structured tools instead of driving
the TUI via tmux keystrokes.

## Install

```sh
uv venv -p python3.11
uv pip install -e ".[dev]"
```

## Run

```sh
.venv/bin/claude-agents-mcp        # stdio transport, FastMCP default
```

Or wire it into a client's MCP config:

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

- `spawn_agent(prompt, cwd?, session_id?, name?, model?, agent?, permission_mode?, effort?, extra_args?)`
  - New session if `session_id` omitted; `--resume` if provided.
  - Default `permission_mode="bypassPermissions"` (headless needs it).
- `list_agents(status_filter?)` — refreshes status, returns registry entries.
- `get_agent_output(session_id, offset?, format?, limit?)`
  - `format="parsed"` (default) returns `{type, role?, content, ...}` events.
  - `format="raw"` returns each JSONL line untouched.
  - Page with `offset` from the previous call's `next_offset`.
- `abort_agent(session_id, grace_seconds=2.0)` — SIGTERM → SIGKILL after grace.

## Status state machine

- `running` — pid alive AND its cmdline contains the session id (PID-reuse guard).
- `done` — exit file has code 0.
- `errored` — exit file has non-zero code.
- `aborted` — `abort_agent` ran.
- `orphaned` — pid gone, no exit file (rare; e.g. SIGKILL of wrapper itself).

Final states persist until pruned (default 24 h after `ended_at`).

## State on disk

- `~/.claude-agents-mcp/registry.json` — agent registry, atomic-written.
- `~/.claude-agents-mcp/exits/<sid>` — exit code recorded by spawn wrapper.
- `~/.claude/projects/<cwd-slug>/<sid>.jsonl` — Claude Code session log
  (where output is read from; written by `claude` itself).

## Errors

`ToolError` with stable codes:

- `SESSION_NOT_FOUND` — unknown session id in registry.
- `ALREADY_FINISHED` — abort on terminal-state agent.
- `SPAWN_FAILED` — `claude` not on `PATH` or `Popen` raised.

## Limits

- Agents spawned through this server **do not appear** in `claude agents --json`
  (the cc-daemon doesn't know about them), and vice versa. Two lifecycles,
  intentionally separate.
- No concurrency cap — caller manages it.
- Multi-turn: re-call `spawn_agent` with the existing `session_id` to `--resume`.

## Tests

```sh
.venv/bin/pytest -q
```

Unit tests use a fake `claude` binary via `CLAUDE_AGENTS_MCP_CLAUDE_BIN`.
No real network calls.
