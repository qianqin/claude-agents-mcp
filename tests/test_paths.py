from __future__ import annotations

from claude_agents_mcp import paths


def test_cwd_slug_basic():
    assert paths.cwd_slug("/home/qian/projects") == "-home-qian-projects"


def test_cwd_slug_nested_path():
    assert paths.cwd_slug("/home/qian/projects/web") == "-home-qian-projects-web"


def test_session_log_path_uses_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = paths.session_log_path("/home/qian/projects", "abc-123")
    assert p == tmp_path / ".claude" / "projects" / "-home-qian-projects" / "abc-123.jsonl"
