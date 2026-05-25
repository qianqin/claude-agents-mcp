from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_agents_mcp import reader


def write_jsonl(p: Path, lines: list[dict], *, trailing_partial: str | None = None) -> None:
    body = "".join(json.dumps(l) + "\n" for l in lines)
    if trailing_partial is not None:
        body += trailing_partial
    p.write_text(body, encoding="utf-8")


def test_missing_file_returns_empty(tmp_path):
    out = reader.read(tmp_path / "nope.jsonl")
    assert out.events == []
    assert out.next_offset == 0
    assert out.eof is False


def test_raw_mode_returns_each_line(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "permission-mode", "permissionMode": "bypassPermissions"},
    ])
    out = reader.read(p, fmt="raw")
    assert len(out.events) == 2
    assert out.events[0]["type"] == "user"
    assert out.events[1]["type"] == "permission-mode"
    assert out.next_offset == len(p.read_bytes())


def test_parsed_drops_meta(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {"type": "last-prompt", "leafUuid": "x"},
        {"type": "agent-setting", "agentSetting": "claude"},
        {"type": "permission-mode", "permissionMode": "bypassPermissions"},
        {"type": "user", "timestamp": "T", "message": {"role": "user", "content": "hello"}},
    ])
    out = reader.read(p)
    assert len(out.events) == 1
    assert out.events[0]["type"] == "user"
    assert out.events[0]["content"] == "hello"


def test_parsed_assistant_with_thinking_text_tool(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {
            "type": "assistant",
            "timestamp": "T2",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "secret"},
                    {"type": "text", "text": "hi user"},
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"cmd": "ls"}},
                ],
            },
        }
    ])
    out = reader.read(p)
    types = [e["type"] for e in out.events]
    assert types == ["assistant_thinking", "assistant_text", "tool_use"]
    assert out.events[0]["content"] == "secret"
    assert out.events[1]["content"] == "hi user"
    assert out.events[2]["name"] == "Bash"
    assert out.events[2]["input"] == {"cmd": "ls"}


def test_tool_result(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {"type": "tool_result", "timestamp": "T3", "toolUseId": "tu1", "content": "ok", "isError": False},
    ])
    out = reader.read(p)
    assert out.events == [{
        "type": "tool_result",
        "ts": "T3",
        "tool_use_id": "tu1",
        "content": "ok",
        "is_error": False,
    }]


def test_offset_resume(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {"type": "user", "message": {"content": "a"}},
        {"type": "user", "message": {"content": "b"}},
    ])
    first = reader.read(p, limit=1)
    assert len(first.events) == 1
    assert first.events[0]["content"] == "a"

    second = reader.read(p, offset=first.next_offset)
    assert len(second.events) == 1
    assert second.events[0]["content"] == "b"


def test_partial_trailing_line_left_for_next_call(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {"type": "user", "message": {"content": "first"}},
    ], trailing_partial='{"type":"user","message":{"content":"part')
    out = reader.read(p)
    assert len(out.events) == 1
    assert out.events[0]["content"] == "first"
    # next_offset must point just after the complete line, not into the partial
    expected = len(json.dumps({"type": "user", "message": {"content": "first"}})) + 1
    assert out.next_offset == expected


def test_malformed_line_skipped(tmp_path):
    p = tmp_path / "x.jsonl"
    body = '{"type":"user","message":{"content":"ok"}}\n{not json}\n{"type":"user","message":{"content":"after"}}\n'
    p.write_text(body, encoding="utf-8")
    out = reader.read(p)
    contents = [e["content"] for e in out.events]
    assert contents == ["ok", "after"]


def test_unknown_format_raises(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("")
    with pytest.raises(ValueError):
        reader.read(p, fmt="weird")


def test_parsed_needs_clarification_event(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {
            "type": "needs_clarification",
            "timestamp": "T9",
            "question": "Which DB?",
            "context": "two options",
            "urgency": "block",
        },
    ])
    out = reader.read(p)
    assert out.events == [{
        "type": "needs_clarification",
        "ts": "T9",
        "question": "Which DB?",
        "context": "two options",
        "urgency": "block",
    }]


def test_find_latest_clarification_returns_last(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        {"type": "needs_clarification", "timestamp": "T1", "question": "first?"},
        {"type": "user", "message": {"content": "intermission"}},
        {"type": "needs_clarification", "timestamp": "T2", "question": "second?", "urgency": "optional"},
    ])
    got = reader.find_latest_clarification(p)
    assert got["question"] == "second?"
    assert got["urgency"] == "optional"


def test_find_latest_clarification_none(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [{"type": "user", "message": {"content": "hi"}}])
    assert reader.find_latest_clarification(p) is None
    assert reader.find_latest_clarification(tmp_path / "missing.jsonl") is None
