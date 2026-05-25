# claude-agents-mcp — Design

Date: 2026-05-25
Status: Draft

## Purpose

An MCP server that wraps Claude Code's background-agent capability so external
clients (e.g. Hermes) can spawn, observe, and abort agents through structured
tools instead of driving the TUI via tmux keystrokes.

Built in Python with FastMCP. Communicates over stdio.

## Scope

Four tools:

- `spawn_agent` — start a new background agent or resume an existing session.
- `list_agents` — list MCP-spawned agents and their status.
- `get_agent_output` — read parsed (or raw) JSONL events from an agent's session.
- `abort_agent` — terminate an agent via SIGTERM, escalating to SIGKILL.

Out of scope: TUI keystroke simulation, daemon-control-socket integration,
multi-turn streaming input, agents dispatched outside the MCP server.

## Approach

After investigating the `claude` CLI (`claude --help`, `claude agents --help`)
and the on-disk layout under `~/.claude/projects/`, the chosen approach is:

- **Spawn:** detached `claude -p --session-id <uuid>` with a UUID we generate.
  The session file is written to `~/.claude/projects/<cwd-slug>/<uuid>.jsonl`
  exactly as for any other Claude Code session.
- **List:** an in-process registry persisted to `~/.claude-agents-mcp/registry.json`,
  refreshed on every call.
- **Output:** tail the session JSONL file by byte offset; parse to a friendly
  event shape by default, with a `raw` escape hatch.
- **Abort:** SIGTERM the tracked pid; SIGKILL after a grace window.

This is the hybrid approach from brainstorming option C: headless spawn,
session-file read, signal-based abort. It deliberately avoids the
`cc-daemon` control socket (brittle, undocumented, version-coupled) and the
TUI (the thing we're replacing).

Tradeoff: agents spawned through this MCP server **do not appear** in
`claude agents --json` (the daemon doesn't know about them), and vice versa.
The two lifecycles are intentionally separate.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ FastMCP server (Python, stdio)                      │
│                                                     │
│  Tool handlers ─► AgentRegistry                     │
│       │            (atomic JSON file)               │
│       ├─► Spawner  (subprocess.Popen, detached)     │
│       ├─► Reader   (JSONL tail + parse)             │
│       └─► Status   (state machine)                  │
└─────────────────────────────────────────────────────┘
        │                          │
        ▼                          ▼
  detached `claude` proc     ~/.claude/projects/<slug>/<uuid>.jsonl
  + exits/<uuid> file
```

### Status state machine

Status is recomputed on every `list_agents` and `get_agent_output` call:

1. `exits/<sid>` file present → read exit code. `0` → `done`, nonzero → `errored`. Terminal.
2. Registry says `aborted` → keep `aborted`. Terminal.
3. PID alive in `/proc` AND its cmdline contains `<sid>` → `running`.
   (cmdline check guards against PID reuse.)
4. Otherwise → `orphaned` (process died without the wrapper finishing —
   e.g. SIGKILL of the wrapper itself).

### Spawn wrapping

To preserve exit codes across MCP-server restarts, the spawned command is
wrapped in `sh -c` that records the claude exit code to a file:

```python
state_dir = Path.home() / ".claude-agents-mcp"
exits_dir = state_dir / "exits"
cmd = [
    "sh", "-c",
    f'claude -p --session-id {sid} {flags} -- {shlex.quote(prompt)}; '
    f'echo $? > {exits_dir}/{sid}'
]
subprocess.Popen(
    cmd, cwd=cwd, start_new_session=True,
    stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
)
```

`start_new_session=True` detaches the process so it survives MCP-server exit.

### Session-file path

Slug rule confirmed against `~/.claude/projects/`:

```python
slug = cwd.replace("/", "-")
log_path = Path.home() / ".claude" / "projects" / slug / f"{sid}.jsonl"
```

### Registry

- Path: `~/.claude-agents-mcp/registry.json`
- Schema (per entry, keyed by session_id):
  ```json
  {
    "<uuid>": {
      "pid": 12345,
      "name": "review the dialog refactor",
      "cwd": "/home/qian/projects/web",
      "started_at": 1779730570913,
      "ended_at": null,
      "status": "running",
      "log_path": "/home/qian/.claude/projects/-home-qian-projects-web/<uuid>.jsonl",
      "model": null,
      "permission_mode": "bypassPermissions",
      "resumed_from": null
    }
  }
  ```
- Atomic write: write to `registry.json.tmp` then `os.replace`.
- Saved on: spawn, abort, observed transition to a terminal state.
- TTL: entries in a terminal state are pruned 24 h after `ended_at`.

## Tool surface

### `spawn_agent`

```python
def spawn_agent(
    prompt: str,
    cwd: str | None = None,            # default: server cwd
    session_id: str | None = None,     # if set → --resume <id>
    name: str | None = None,           # display label
    model: str | None = None,          # "sonnet", "opus", ...
    agent: str | None = None,          # --agent <name>
    permission_mode: str = "bypassPermissions",
    effort: str | None = None,         # low|medium|high|xhigh|max
    extra_args: list[str] | None = None,
) -> dict:
    """Returns {session_id, pid, status, started_at, log_path}."""
```

If `session_id` is provided, the command uses `--resume <id>` instead of
`--session-id <id>`. The new pid is recorded; the registry entry's
`resumed_from` field tracks the original session.

### `list_agents`

```python
def list_agents(status: str | None = None) -> list[dict]:
    """Returns registry entries; refreshes status first.
    Optional status filter."""
```

### `get_agent_output`

```python
def get_agent_output(
    session_id: str,
    offset: int = 0,
    format: str = "parsed",            # "parsed" | "raw"
    limit: int | None = None,
) -> dict:
    """Returns {events, next_offset, eof, status}."""
```

Parsed event shape:
```json
{"type": "assistant_text", "ts": "2026-05-25T...", "content": "..."}
```
Kept types: `user`, `assistant_text`, `assistant_thinking`, `tool_use`,
`tool_result`, `system`. Dropped meta types in parsed mode: `last-prompt`,
`agent-setting`, `permission-mode`.

`raw` mode returns each JSONL line as a `dict` untouched.

`eof` is true when no more bytes are currently available AND status is terminal.

### `abort_agent`

```python
def abort_agent(
    session_id: str,
    grace_seconds: float = 2.0,
) -> dict:
    """SIGTERM → wait grace_seconds → SIGKILL.
    Returns {session_id, prior_status, status, exit_code?}."""
```

## Errors

Raised as `ToolError` with stable codes:

- `SESSION_NOT_FOUND` — unknown session_id in registry.
- `ALREADY_FINISHED` — abort called on a terminal-state agent.
- `LOG_MISSING` — session file does not exist (e.g. claude failed pre-write).
- `SPAWN_FAILED` — `claude` not on PATH or Popen raised.

## File layout

```
claude-agents-mcp/
├── pyproject.toml
├── README.md
├── src/claude_agents_mcp/
│   ├── __init__.py
│   ├── server.py            # FastMCP entry + tool defs
│   ├── registry.py
│   ├── spawner.py
│   ├── reader.py
│   ├── status.py
│   └── paths.py             # cwd→slug, log_path helpers
├── tests/
│   ├── test_registry.py
│   ├── test_paths.py
│   ├── test_reader.py
│   ├── test_status.py
│   └── test_spawn_integration.py
└── docs/superpowers/specs/2026-05-25-claude-agents-mcp-design.md
```

## Testing

- **Unit:** registry round-trip + atomic write, slug calculation, JSONL parser
  against fixture files, status state-machine (table-driven).
- **Integration:** one slow test that runs a cheap real `claude -p` with a
  trivial prompt, polls until terminal state, verifies parsed output and exit
  code. Skipped by default unless `CLAUDE_AGENTS_MCP_INTEGRATION=1`.
- No filesystem mocks; use `tmp_path` and override `HOME` via env.

## Open questions

None at this time.
