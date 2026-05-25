from __future__ import annotations

from pathlib import Path

import pytest

from claude_agents_mcp import paths


def test_cwd_slug_basic():
    assert paths.cwd_slug("/home/qian/projects") == "-home-qian-projects"


def test_cwd_slug_trailing_slash_kept_as_dash():
    assert paths.cwd_slug("/home/qian/projects/") == "-home-qian-projects-"


def test_cwd_slug_nested_path():
    assert paths.cwd_slug("/home/qian/projects/web") == "-home-qian-projects-web"


def test_session_log_path_uses_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = paths.session_log_path("/home/qian/projects", "abc-123")
    assert p == tmp_path / ".claude" / "projects" / "-home-qian-projects" / "abc-123.jsonl"


def test_state_paths_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.state_dir() == tmp_path / ".claude-agents-mcp"
    assert paths.registry_path() == tmp_path / ".claude-agents-mcp" / "registry.json"
    assert paths.exit_file("sid") == tmp_path / ".claude-agents-mcp" / "exits" / "sid"


def test_ensure_state_dirs_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths.ensure_state_dirs()
    assert (tmp_path / ".claude-agents-mcp" / "exits").is_dir()
