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
  Blocked when the agent is `awaiting_clarification` / `pending_reply` (use
  `reply_to_agent` instead).
- `reply_to_agent(session_id, answer)` — supply a human answer to a
  `needs_clarification` event. Writes a sidecar; the next
  `spawn_agent(session_id=...)` consumes it.

## Status state machine

- `running` — pid alive AND its cmdline contains the session id (PID-reuse guard).
- `done` — exit file has code 0 and no pending clarification.
- `errored` — exit file has non-zero code.
- `aborted` — `abort_agent` ran.
- `orphaned` — pid gone, no exit file (rare; e.g. SIGKILL of wrapper itself).
- `awaiting_clarification` — agent exited 0 after emitting a
  `needs_clarification` event; waiting on `reply_to_agent`.
- `pending_reply` — reply was provided; waiting for resume via `spawn_agent`.

Terminal states (`done`/`errored`/`aborted`/`orphaned`) persist until pruned
(default 24 h after `ended_at`). `awaiting_clarification` and `pending_reply`
are sticky — they persist until the session is resumed or pruned manually.

## Bidirectional clarification

Long-running agents can ask the human a question mid-task instead of guessing
or aborting. The flow:

1. **Agent** emits a JSONL line into its session log before exiting cleanly:

   ```json
   {"type": "needs_clarification",
    "question": "MySQL or Postgres?",
    "context": "two DBs configured",
    "urgency": "block",
    "timestamp": "..."}
   ```

2. **Server** sees exit 0 + the event and flips status to
   `awaiting_clarification`. The event is exposed by `get_agent_output`.
3. **Caller** asks the human, then calls `reply_to_agent(sid, answer)`. The
   server records the answer at `~/.claude-agents-mcp/pending/<sid>.json`
   alongside the original question and switches status to `pending_reply`.
4. **Caller** resumes with `spawn_agent(prompt=..., session_id=sid)`. The
   server reads the sidecar, prepends an injection block to the prompt:

   ```
   Previous question: MySQL or Postgres?
   Your answer: Postgres
   Please continue.

   <follow-up prompt if any>
   ```

   then deletes the sidecar and starts a `--resume` run.

The server is plumbing only — the agent decides what is ambiguous and emits
the event; the caller decides how to surface the question to the human.

## State on disk

- `~/.claude-agents-mcp/registry.json` — agent registry, atomic-written.
- `~/.claude-agents-mcp/exits/<sid>` — exit code recorded by spawn wrapper.
- `~/.claude-agents-mcp/pending/<sid>.json` — human reply waiting to be
  injected on the next resume (`{question, answer, replied_at}`).
- `~/.claude/projects/<cwd-slug>/<sid>.jsonl` — Claude Code session log
  (where output is read from; written by `claude` itself).

## Errors

`ToolError` with stable codes:

- `SESSION_NOT_FOUND` — unknown session id in registry.
- `ALREADY_FINISHED` — abort on terminal-state agent.
- `AWAITING_REPLY` — abort while agent is awaiting clarification or pending reply.
- `NOT_AWAITING_CLARIFICATION` — `reply_to_agent` called outside of
  `awaiting_clarification` state.
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
