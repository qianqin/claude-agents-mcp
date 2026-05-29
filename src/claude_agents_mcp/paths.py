from __future__ import annotations

import os
from pathlib import Path


def cwd_slug(cwd: str | os.PathLike) -> str:
    return str(cwd).replace("/", "-")


def claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def session_log_path(cwd: str | os.PathLike, session_id: str) -> Path:
    """On-disk transcript path for a session, when one exists.

    Note: TUI-spawned background agents do not reliably write here; this is used
    only for best-effort enrichment of `claude agents --json` records.
    """
    return claude_projects_dir() / cwd_slug(cwd) / f"{session_id}.jsonl"
