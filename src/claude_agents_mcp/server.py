from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import paths, reader, spawner, status
from .registry import TERMINAL_STATUSES, Registry
from .spawner import SpawnError, SpawnRequest, spawn as spawn_impl


_DEFAULT_SERVER_CWD = os.getcwd()


def _registry() -> Registry:
    paths.ensure_state_dirs()
    return Registry(path=paths.registry_path())


def _entry_dict(entry) -> dict[str, Any]:
    return entry.to_dict()


mcp = FastMCP(
    name="claude-agents-mcp",
    instructions=(
        "Spawn, observe, and abort Claude Code background agents. "
        "Use spawn_agent to start, list_agents to enumerate, "
        "get_agent_output to read parsed events, abort_agent to terminate."
    ),
)


@mcp.tool()
def spawn_agent(
    prompt: str,
    cwd: str | None = None,
    session_id: str | None = None,
    name: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    permission_mode: str = "bypassPermissions",
    effort: str | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Spawn a new agent or resume an existing session.

    Returns: {session_id, pid, status, started_at, log_path}.
    """
    reg = _registry()
    req = SpawnRequest(
        prompt=prompt,
        cwd=cwd or _DEFAULT_SERVER_CWD,
        session_id=session_id,
        name=name,
        model=model,
        agent=agent,
        permission_mode=permission_mode,
        effort=effort,
        extra_args=extra_args,
    )
    try:
        result = spawn_impl(req, reg)
    except SpawnError as e:
        raise ToolError(f"SPAWN_FAILED: {e}") from e

    entry = reg.get(result.session_id)
    return _entry_dict(entry)


@mcp.tool()
def list_agents(status_filter: str | None = None) -> list[dict[str, Any]]:
    """List MCP-spawned agents. Refreshes status first.
    Optional status_filter: running|done|errored|aborted|orphaned.
    """
    reg = _registry()
    reg.prune()
    refreshed = status.refresh_all(reg)
    if status_filter:
        refreshed = [e for e in refreshed if e.status == status_filter]
    return [_entry_dict(e) for e in refreshed]


@mcp.tool()
def get_agent_output(
    session_id: str,
    offset: int = 0,
    format: str = "parsed",
    limit: int | None = None,
) -> dict[str, Any]:
    """Read parsed (default) or raw JSONL events for an agent.

    Returns: {events, next_offset, eof, status, exit_code}.
    """
    reg = _registry()
    entry = reg.get(session_id)
    if entry is None:
        raise ToolError(f"SESSION_NOT_FOUND: {session_id}")

    entry = status.refresh(entry, reg)
    log_path = Path(entry.log_path)

    try:
        result = reader.read(log_path, offset=offset, fmt=format, limit=limit)
    except ValueError as e:
        raise ToolError(str(e)) from e

    eof = entry.status in TERMINAL_STATUSES and result.next_offset == (
        log_path.stat().st_size if log_path.exists() else 0
    )
    return {
        "events": result.events,
        "next_offset": result.next_offset,
        "eof": eof,
        "status": entry.status,
        "exit_code": entry.exit_code,
    }


@mcp.tool()
def abort_agent(session_id: str, grace_seconds: float = 2.0) -> dict[str, Any]:
    """SIGTERM the agent, escalating to SIGKILL after grace.

    Returns: {session_id, prior_status, status, exit_code?}.
    """
    reg = _registry()
    entry = reg.get(session_id)
    if entry is None:
        raise ToolError(f"SESSION_NOT_FOUND: {session_id}")

    entry = status.refresh(entry, reg)
    prior = entry.status
    if prior in TERMINAL_STATUSES:
        raise ToolError(f"ALREADY_FINISHED: status={prior}")

    pid = entry.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Process already gone, just mark aborted.
        updated = reg.update(
            session_id,
            status="aborted",
            ended_at=int(time.time() * 1000),
        )
        return {
            "session_id": session_id,
            "prior_status": prior,
            "status": updated.status,
            "exit_code": updated.exit_code,
        }

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not _pgid_alive(pid):
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    # Reap the child so it doesn't linger as a zombie.
    spawner.reap(session_id)

    updated = reg.update(
        session_id,
        status="aborted",
        ended_at=int(time.time() * 1000),
    )
    return {
        "session_id": session_id,
        "prior_status": prior,
        "status": updated.status,
        "exit_code": updated.exit_code,
    }


def _pgid_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
