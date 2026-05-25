from __future__ import annotations

import os
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .registry import AgentEntry, Registry


CLAUDE_BIN_ENV = "CLAUDE_AGENTS_MCP_CLAUDE_BIN"


def claude_bin() -> str:
    return os.environ.get(CLAUDE_BIN_ENV, "claude")


# Module-level map so status.refresh can reap finished children non-blockingly.
# Across server restarts these handles are lost — kernel reparents to init, which reaps.
_active_procs: dict[str, "subprocess.Popen"] = {}


def reap(session_id: str) -> int | None:
    """Non-blocking poll on the tracked Popen. Returns exit code if reaped,
    None if still running or unknown."""
    proc = _active_procs.get(session_id)
    if proc is None:
        return None
    rc = proc.poll()
    if rc is not None:
        _active_procs.pop(session_id, None)
    return rc


@dataclass
class SpawnRequest:
    prompt: str
    cwd: str
    session_id: str | None = None  # resume target
    name: str | None = None
    model: str | None = None
    agent: str | None = None
    permission_mode: str = "bypassPermissions"
    effort: str | None = None
    extra_args: list[str] | None = None


@dataclass
class SpawnResult:
    session_id: str
    pid: int
    log_path: Path
    started_at: int


def build_command(req: SpawnRequest, sid: str, *, exit_file: Path) -> list[str]:
    parts: list[str] = [claude_bin(), "-p"]

    if req.session_id is not None:
        parts += ["--resume", req.session_id]
    else:
        parts += ["--session-id", sid]

    parts += ["--permission-mode", req.permission_mode]

    if req.model:
        parts += ["--model", req.model]
    if req.agent:
        parts += ["--agent", req.agent]
    if req.effort:
        parts += ["--effort", req.effort]
    if req.extra_args:
        parts += list(req.extra_args)

    parts += ["--", req.prompt]

    quoted = " ".join(shlex.quote(p) for p in parts)
    return ["sh", "-c", f"{quoted}; echo $? > {shlex.quote(str(exit_file))}"]


def spawn(req: SpawnRequest, registry: Registry) -> SpawnResult:
    paths.ensure_state_dirs()

    sid = req.session_id or str(uuid.uuid4())
    exit_file = paths.exit_file(sid)
    # Clear any stale exit file for resume case
    if exit_file.exists():
        exit_file.unlink()

    cmd = build_command(req, sid, exit_file=exit_file)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=req.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        raise SpawnError(f"failed to spawn: {e}") from e

    _active_procs[sid] = proc
    started_at = int(time.time() * 1000)
    log_path = paths.session_log_path(req.cwd, sid)

    entry = AgentEntry(
        session_id=sid,
        pid=proc.pid,
        cwd=req.cwd,
        log_path=str(log_path),
        started_at=started_at,
        status="running",
        name=req.name,
        model=req.model,
        permission_mode=req.permission_mode,
        resumed_from=req.session_id,
    )
    registry.add(entry)

    return SpawnResult(
        session_id=sid,
        pid=proc.pid,
        log_path=log_path,
        started_at=started_at,
    )


class SpawnError(RuntimeError):
    pass
