"""Authoritative agent listing via `claude agents --json`.

`claude agents --json` prints the live background sessions and exits — no TTY
required — so it is the source of truth for which agents exist and their status.
We enrich each record with the on-disk JSONL log path (computed from cwd +
sessionId) so callers can read full conversation history with `reader`.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from . import paths

CLAUDE_BIN_ENV = "CLAUDE_AGENTS_MCP_CLAUDE_BIN"

# Raw `claude agents` statuses → normalized lifecycle labels.
_STATUS_MAP = {
    "busy": "running",
    "working": "running",
    "idle": "idle",
    "awaiting_input": "awaiting_input",
    "awaiting input": "awaiting_input",
    "needs_input": "awaiting_input",
    "completed": "done",
    "done": "done",
    "error": "errored",
    "errored": "errored",
}


class StatusError(RuntimeError):
    pass


def claude_bin() -> str:
    return os.environ.get(CLAUDE_BIN_ENV, "claude")


def normalize_status(raw: str | None) -> str:
    if not raw:
        return "unknown"
    return _STATUS_MAP.get(raw, raw)


def list_live(*, runner=subprocess.run) -> list[dict[str, Any]]:
    """Run `claude agents --json` and return enriched agent records.

    Each record: {sessionId, name, status (normalized), raw_status, pid, cwd,
    kind, started_at, log_path}.
    """
    proc = runner(
        [claude_bin(), "agents", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise StatusError(
            f"`claude agents --json` failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as e:
        raise StatusError(f"could not parse `claude agents --json`: {e}") from e

    out: list[dict[str, Any]] = []
    for item in data:
        sid = item.get("sessionId")
        cwd = item.get("cwd", "")
        raw_status = item.get("status")
        log_path = (
            str(paths.session_log_path(cwd, sid)) if sid and cwd else None
        )
        out.append(
            {
                "session_id": sid,
                "name": item.get("name"),
                "status": normalize_status(raw_status),
                "raw_status": raw_status,
                "pid": item.get("pid"),
                "cwd": cwd,
                "kind": item.get("kind"),
                "started_at": item.get("startedAt"),
                "log_path": log_path,
            }
        )
    return out


def resolve(identifier: str, *, runner=subprocess.run) -> dict[str, Any] | None:
    """Find a live agent by sessionId (exact) or name (exact, then prefix)."""
    agents = list_live(runner=runner)
    for a in agents:
        if a["session_id"] == identifier:
            return a
    for a in agents:
        if a["name"] == identifier:
            return a
    matches = [a for a in agents if a["name"] and a["name"].startswith(identifier)]
    if len(matches) == 1:
        return matches[0]
    return None
