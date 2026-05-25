from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_agents_mcp import paths, status
from claude_agents_mcp.registry import AgentEntry, Registry


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths.ensure_state_dirs()
    return tmp_path


def _add(reg: Registry, **overrides) -> AgentEntry:
    base = dict(
        session_id="sid-1",
        pid=os.getpid(),  # always alive while test runs
        cwd="/home/qian/projects",
        log_path="/tmp/x.jsonl",
        started_at=1_700_000_000_000,
        status="running",
    )
    base.update(overrides)
    e = AgentEntry(**base)
    reg.add(e)
    return e


def test_running_when_pid_alive_with_sid(home, monkeypatch):
    reg = Registry(path=paths.registry_path())
    e = _add(reg)
    monkeypatch.setattr(status, "_pid_alive_with_sid", lambda pid, sid: True)
    out = status.refresh(e, reg)
    assert out.status == "running"
    assert out.ended_at is None


def test_done_when_exit_zero(home):
    reg = Registry(path=paths.registry_path())
    e = _add(reg)
    paths.exit_file(e.session_id).write_text("0\n")
    out = status.refresh(e, reg, now_ms=1_700_000_001_000)
    assert out.status == "done"
    assert out.exit_code == 0
    assert out.ended_at == 1_700_000_001_000


def test_errored_when_exit_nonzero(home):
    reg = Registry(path=paths.registry_path())
    e = _add(reg)
    paths.exit_file(e.session_id).write_text("2\n")
    out = status.refresh(e, reg)
    assert out.status == "errored"
    assert out.exit_code == 2


def test_orphaned_when_pid_dead_no_exit(home, monkeypatch):
    reg = Registry(path=paths.registry_path())
    e = _add(reg, pid=1)  # init, but cmdline won't match sid
    monkeypatch.setattr(status, "_pid_alive_with_sid", lambda pid, sid: False)
    out = status.refresh(e, reg)
    assert out.status == "orphaned"
    assert out.ended_at is not None


def test_terminal_states_unchanged(home, monkeypatch):
    reg = Registry(path=paths.registry_path())
    e = _add(reg, status="aborted", ended_at=123)
    # Even with exit file present, aborted stays aborted
    paths.exit_file(e.session_id).write_text("0\n")
    out = status.refresh(e, reg)
    assert out.status == "aborted"
    assert out.ended_at == 123


def test_pid_alive_with_sid_checks_cmdline_contains_sid(home, monkeypatch, tmp_path):
    # Self-pid is alive; ensure check uses cmdline contents.
    # Our cmdline won't contain 'fake-sid', so should be False.
    assert status._pid_alive_with_sid(os.getpid(), "fake-sid-xyz") is False


def test_pid_alive_dead_pid_returns_false(home):
    # Pick a pid extremely unlikely to exist
    assert status._pid_alive_with_sid(2_147_483_640, "anything") is False


def _write_log(home: Path, log_path: Path, lines: list[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")


def test_awaiting_clarification_when_exit_zero_with_event(home):
    reg = Registry(path=paths.registry_path())
    log_path = home / "log.jsonl"
    e = _add(reg, log_path=str(log_path))
    _write_log(home, log_path, [
        {"type": "needs_clarification", "question": "which?", "urgency": "block"},
    ])
    paths.exit_file(e.session_id).write_text("0\n")
    out = status.refresh(e, reg)
    assert out.status == "awaiting_clarification"
    assert out.exit_code == 0
    assert out.ended_at is not None


def test_pending_reply_when_sidecar_exists(home):
    reg = Registry(path=paths.registry_path())
    log_path = home / "log.jsonl"
    e = _add(reg, log_path=str(log_path))
    _write_log(home, log_path, [
        {"type": "needs_clarification", "question": "which?"},
    ])
    paths.exit_file(e.session_id).write_text("0\n")
    paths.pending_file(e.session_id).write_text(json.dumps({"answer": "left"}))
    out = status.refresh(e, reg)
    assert out.status == "pending_reply"


def test_done_when_exit_zero_no_event(home):
    reg = Registry(path=paths.registry_path())
    log_path = home / "log.jsonl"
    e = _add(reg, log_path=str(log_path))
    _write_log(home, log_path, [{"type": "user", "message": {"content": "x"}}])
    paths.exit_file(e.session_id).write_text("0\n")
    out = status.refresh(e, reg)
    assert out.status == "done"


def test_clarify_statuses_unchanged_by_refresh(home, monkeypatch):
    reg = Registry(path=paths.registry_path())
    e = _add(reg, status="awaiting_clarification", ended_at=42)
    # Even with an exit-file flip it should remain awaiting_clarification.
    paths.exit_file(e.session_id).write_text("0\n")
    out = status.refresh(e, reg)
    assert out.status == "awaiting_clarification"
    assert out.ended_at == 42
