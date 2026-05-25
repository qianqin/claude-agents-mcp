from __future__ import annotations

import time

import pytest

from claude_agents_mcp.registry import AgentEntry, Registry


def _entry(sid: str = "s1", **overrides) -> AgentEntry:
    base = dict(
        session_id=sid,
        pid=1234,
        cwd="/home/qian/projects",
        log_path="/tmp/whatever.jsonl",
        started_at=1779730570913,
        status="running",
    )
    base.update(overrides)
    return AgentEntry(**base)


def test_roundtrip(tmp_path):
    p = tmp_path / "registry.json"
    r1 = Registry(path=p)
    r1.add(_entry("a"))
    r1.add(_entry("b", pid=2222, name="second"))

    r2 = Registry(path=p)
    got = {e.session_id: e for e in r2.all()}
    assert set(got) == {"a", "b"}
    assert got["b"].name == "second"
    assert got["b"].pid == 2222


def test_atomic_write_no_tmp_leftover(tmp_path):
    p = tmp_path / "registry.json"
    r = Registry(path=p)
    r.add(_entry("x"))
    assert p.exists()
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_update_persists(tmp_path):
    p = tmp_path / "registry.json"
    r = Registry(path=p)
    r.add(_entry("a"))
    r.update("a", status="done", ended_at=1779730570999, exit_code=0)

    r2 = Registry(path=p)
    e = r2.get("a")
    assert e.status == "done"
    assert e.exit_code == 0
    assert e.ended_at == 1779730570999


def test_remove(tmp_path):
    p = tmp_path / "registry.json"
    r = Registry(path=p)
    r.add(_entry("a"))
    r.add(_entry("b"))
    r.remove("a")
    assert {e.session_id for e in r.all()} == {"b"}


def test_prune_terminal_entries_past_ttl(tmp_path):
    p = tmp_path / "registry.json"
    r = Registry(path=p, ttl_seconds=10)
    now = 2_000_000_000_000
    r.add(_entry("recent", status="done", ended_at=now - 1_000))
    r.add(_entry("old", status="done", ended_at=now - 100_000))
    r.add(_entry("running", status="running", ended_at=None))

    dropped = r.prune(now_ms=now)
    assert dropped == ["old"]
    assert {e.session_id for e in r.all()} == {"recent", "running"}


def test_load_missing_file_is_empty(tmp_path):
    r = Registry(path=tmp_path / "absent.json")
    assert r.all() == []
