from __future__ import annotations

import time
from pathlib import Path

from . import paths, spawner
from .registry import TERMINAL_STATUSES, AgentEntry, Registry


def _read_cmdline(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def _pid_alive_with_sid(pid: int, session_id: str) -> bool:
    cmdline = _read_cmdline(pid)
    if cmdline is None:
        return False
    return session_id in cmdline


def _read_exit_code(session_id: str) -> int | None:
    p = paths.exit_file(session_id)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
        return int(text)
    except (ValueError, OSError):
        return None


ORPHAN_GRACE_SECONDS = 0.5


def refresh(entry: AgentEntry, registry: Registry, *, now_ms: int | None = None) -> AgentEntry:
    """Recompute status for one entry, persist if changed. Returns the entry."""
    if entry.status in TERMINAL_STATUSES:
        return entry

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    # Reap any finished child so it doesn't linger as a zombie.
    spawner.reap(entry.session_id)

    exit_code = _read_exit_code(entry.session_id)
    if exit_code is not None:
        new_status = "done" if exit_code == 0 else "errored"
        return registry.update(
            entry.session_id,
            status=new_status,
            exit_code=exit_code,
            ended_at=now_ms,
        )

    if _pid_alive_with_sid(entry.pid, entry.session_id):
        return entry

    # PID gone but no exit file: race between shell child exit and the wrapper's
    # `echo $? > exit_file` flush. Briefly retry before declaring orphaned.
    deadline = time.time() + ORPHAN_GRACE_SECONDS
    while time.time() < deadline:
        time.sleep(0.05)
        exit_code = _read_exit_code(entry.session_id)
        if exit_code is not None:
            new_status = "done" if exit_code == 0 else "errored"
            return registry.update(
                entry.session_id,
                status=new_status,
                exit_code=exit_code,
                ended_at=int(time.time() * 1000),
            )

    return registry.update(
        entry.session_id,
        status="orphaned",
        ended_at=int(time.time() * 1000),
    )


def refresh_all(registry: Registry) -> list[AgentEntry]:
    now_ms = int(time.time() * 1000)
    return [refresh(e, registry, now_ms=now_ms) for e in registry.all()]
