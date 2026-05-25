from __future__ import annotations

import os
from pathlib import Path


def cwd_slug(cwd: str | os.PathLike) -> str:
    return str(cwd).replace("/", "-")


def claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def session_log_path(cwd: str | os.PathLike, session_id: str) -> Path:
    return claude_projects_dir() / cwd_slug(cwd) / f"{session_id}.jsonl"


def state_dir() -> Path:
    return Path.home() / ".claude-agents-mcp"


def registry_path() -> Path:
    return state_dir() / "registry.json"


def exits_dir() -> Path:
    return state_dir() / "exits"


def exit_file(session_id: str) -> Path:
    return exits_dir() / session_id


def ensure_state_dirs() -> None:
    exits_dir().mkdir(parents=True, exist_ok=True)
