from __future__ import annotations

import shlex

import pytest

from claude_agents_mcp import paths, spawner
from claude_agents_mcp.registry import Registry
from claude_agents_mcp.spawner import SpawnRequest, build_command


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths.ensure_state_dirs()
    return tmp_path


def test_build_command_new_session(home):
    req = SpawnRequest(prompt="hello world", cwd="/tmp")
    cmd = build_command(req, sid="abc", exit_file=paths.exit_file("abc"))
    assert cmd[0] == "sh"
    assert cmd[1] == "-c"
    inner = cmd[2]
    assert "--session-id abc" in inner
    assert "--permission-mode bypassPermissions" in inner
    assert "'hello world'" in inner  # prompt shell-quoted
    assert inner.endswith(f"; echo $? > {shlex.quote(str(paths.exit_file('abc')))}")


def test_build_command_resume(home):
    req = SpawnRequest(prompt="next turn", cwd="/tmp", session_id="prev-sid")
    cmd = build_command(req, sid="new-sid", exit_file=paths.exit_file("new-sid"))
    inner = cmd[2]
    assert "--resume prev-sid" in inner
    assert "--session-id" not in inner


def test_build_command_extra_flags(home):
    req = SpawnRequest(
        prompt="p",
        cwd="/tmp",
        model="opus",
        agent="reviewer",
        effort="high",
        extra_args=["--add-dir", "/x"],
    )
    cmd = build_command(req, sid="s", exit_file=paths.exit_file("s"))
    inner = cmd[2]
    for token in ["--model opus", "--agent reviewer", "--effort high", "--add-dir /x"]:
        assert token in inner


def test_spawn_uses_fake_claude_records_exit(home, monkeypatch):
    # Use a fake "claude" that just exits 0 after writing a marker.
    fake = home / "fake-claude.sh"
    marker = home / "ran"
    fake.write_text(f"#!/bin/sh\necho ran > {marker}\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(fake))

    reg = Registry(path=paths.registry_path())
    res = spawner.spawn(SpawnRequest(prompt="hi", cwd=str(home)), reg)

    # Wait briefly for subprocess
    import time as _t
    for _ in range(50):
        if paths.exit_file(res.session_id).exists():
            break
        _t.sleep(0.02)

    assert paths.exit_file(res.session_id).read_text().strip() == "0"
    assert marker.exists()
    entry = reg.get(res.session_id)
    assert entry is not None
    assert entry.pid == res.pid
    assert entry.permission_mode == "bypassPermissions"


def test_spawn_missing_binary_raises(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", "/no/such/bin")
    reg = Registry(path=paths.registry_path())
    # sh -c will execute and exit 127; spawn itself succeeds. The exit code
    # ends up in the exit file. Ensure spawn doesn't crash.
    res = spawner.spawn(SpawnRequest(prompt="p", cwd=str(home)), reg)
    import time as _t
    for _ in range(50):
        if paths.exit_file(res.session_id).exists():
            break
        _t.sleep(0.02)
    assert paths.exit_file(res.session_id).read_text().strip() != "0"
