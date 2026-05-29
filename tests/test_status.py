from __future__ import annotations

import json
import subprocess

import pytest

from claude_agents_mcp import status


def _runner(payload, rc=0, stderr=""):
    def run(args, capture_output=True, text=True, check=False):
        return subprocess.CompletedProcess(args, rc, json.dumps(payload), stderr)

    return run


SAMPLE = [
    {
        "pid": 100,
        "cwd": "/home/qian/projects",
        "kind": "background",
        "startedAt": 123,
        "sessionId": "sid-busy",
        "name": "busy one",
        "status": "busy",
    },
    {
        "pid": 200,
        "cwd": "/home/qian/projects",
        "kind": "background",
        "startedAt": 456,
        "sessionId": "sid-idle",
        "name": "idle one",
        "status": "idle",
    },
]


def test_list_live_maps_status_and_log_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    agents = status.list_live(runner=_runner(SAMPLE))
    by_id = {a["session_id"]: a for a in agents}
    assert by_id["sid-busy"]["status"] == "running"
    assert by_id["sid-idle"]["status"] == "idle"
    assert by_id["sid-busy"]["raw_status"] == "busy"
    assert by_id["sid-busy"]["log_path"].endswith("sid-busy.jsonl")
    assert "-home-qian-projects" in by_id["sid-busy"]["log_path"]


def test_list_live_raises_on_failure():
    with pytest.raises(status.StatusError):
        status.list_live(runner=_runner([], rc=1, stderr="boom"))


def test_normalize_status():
    assert status.normalize_status("busy") == "running"
    assert status.normalize_status("completed") == "done"
    assert status.normalize_status("idle") == "idle"
    assert status.normalize_status(None) == "unknown"
    assert status.normalize_status("weird") == "weird"


def test_resolve_by_session_id():
    rec = status.resolve("sid-idle", runner=_runner(SAMPLE))
    assert rec is not None and rec["name"] == "idle one"


def test_resolve_by_name_exact():
    rec = status.resolve("busy one", runner=_runner(SAMPLE))
    assert rec["session_id"] == "sid-busy"


def test_resolve_by_name_prefix():
    rec = status.resolve("idle", runner=_runner(SAMPLE))
    assert rec["session_id"] == "sid-idle"


def test_resolve_unknown_returns_none():
    assert status.resolve("nope", runner=_runner(SAMPLE)) is None
