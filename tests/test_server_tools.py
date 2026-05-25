from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from claude_agents_mcp import paths
import claude_agents_mcp.server as srv


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths.ensure_state_dirs()
    monkeypatch.setattr(srv, "_DEFAULT_SERVER_CWD", str(tmp_path))
    return tmp_path


def _fake_claude(home: Path, *, sleep: float = 0.0, exit_code: int = 0, jsonl_lines: list[dict] | None = None) -> Path:
    """Create a stand-in `claude` shell script that:
    - parses --session-id <uuid> from its args
    - writes provided jsonl lines to ~/.claude/projects/<slug>/<sid>.jsonl
    - sleeps, then exits with the requested code
    """
    script = home / "fake-claude.sh"
    lines_json = json.dumps(jsonl_lines or [])
    script.write_text(
        f"""#!/usr/bin/env bash
set -e
SID=""
ARGS=("$@")
for ((i=0; i<${{#ARGS[@]}}; i++)); do
  if [[ "${{ARGS[$i]}}" == "--session-id" ]]; then
    SID="${{ARGS[$((i+1))]}}"
  fi
done
CWD=$(pwd)
SLUG=$(echo "$CWD" | sed 's|/|-|g')
DIR="$HOME/.claude/projects/$SLUG"
mkdir -p "$DIR"
LOG="$DIR/$SID.jsonl"
python3 - <<PY
import json, os, sys
lines = json.loads({lines_json!r})
with open(os.environ['LOG_PATH'], 'w') as f:
    for line in lines:
        f.write(json.dumps(line) + "\\n")
PY
sleep {sleep}
exit {exit_code}
"""
    )
    script.chmod(0o755)
    return script


def test_spawn_returns_entry_dict(home, monkeypatch):
    fake = _fake_claude(home, sleep=0, jsonl_lines=[])
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("LOG_PATH", "/dev/null")  # placeholder, fake will override per-spawn

    # The fake script uses $LOG_PATH; we need it to compute per-spawn. Patch via wrapper.
    wrapper = home / "wrap.sh"
    wrapper.write_text(
        f"""#!/usr/bin/env bash
SID=""
ARGS=("$@")
for ((i=0; i<${{#ARGS[@]}}; i++)); do
  if [[ "${{ARGS[$i]}}" == "--session-id" ]]; then
    SID="${{ARGS[$((i+1))]}}"
  fi
done
CWD=$(pwd)
SLUG=$(echo "$CWD" | sed 's|/|-|g')
DIR="$HOME/.claude/projects/$SLUG"
mkdir -p "$DIR"
export LOG_PATH="$DIR/$SID.jsonl"
exec {fake} "$@"
"""
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(wrapper))

    res = srv.spawn_agent(prompt="hi", cwd=str(home))
    assert "session_id" in res
    assert res["pid"] > 0
    assert res["status"] == "running"
    assert res["permission_mode"] == "bypassPermissions"


def test_full_lifecycle_done(home, monkeypatch):
    lines = [
        {"type": "permission-mode", "permissionMode": "bypassPermissions"},
        {"type": "user", "timestamp": "T1", "message": {"role": "user", "content": "hi"}},
        {
            "type": "assistant",
            "timestamp": "T2",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hello!"}]},
        },
    ]
    wrapper = _make_wrapper(home, sleep=0, exit_code=0, jsonl_lines=lines)
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(wrapper))

    res = srv.spawn_agent(prompt="hi", cwd=str(home))
    sid = res["session_id"]

    # Poll until terminal
    for _ in range(100):
        listed = srv.list_agents()
        match = [e for e in listed if e["session_id"] == sid][0]
        if match["status"] in {"done", "errored", "aborted", "orphaned"}:
            break
        time.sleep(0.02)

    assert match["status"] == "done"
    assert match["exit_code"] == 0

    out = srv.get_agent_output(sid)
    types = [e["type"] for e in out["events"]]
    assert "user" in types
    assert "assistant_text" in types
    assert out["eof"] is True
    assert out["status"] == "done"


def test_get_output_unknown_session_raises(home):
    with pytest.raises(ToolError) as exc:
        srv.get_agent_output("missing-sid")
    assert "SESSION_NOT_FOUND" in str(exc.value)


def test_abort_unknown_session_raises(home):
    with pytest.raises(ToolError) as exc:
        srv.abort_agent("missing-sid")
    assert "SESSION_NOT_FOUND" in str(exc.value)


def test_abort_running_agent(home, monkeypatch):
    wrapper = _make_wrapper(home, sleep=10, exit_code=0, jsonl_lines=[])
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(wrapper))

    res = srv.spawn_agent(prompt="hi", cwd=str(home))
    sid = res["session_id"]

    # Give the wrapper a moment to fork into sleep
    time.sleep(0.1)

    out = srv.abort_agent(sid, grace_seconds=0.5)
    assert out["status"] == "aborted"
    assert out["prior_status"] == "running"

    # Subsequent abort should error
    with pytest.raises(ToolError) as exc:
        srv.abort_agent(sid)
    assert "ALREADY_FINISHED" in str(exc.value)


def test_reply_to_agent_unknown_session_raises(home):
    with pytest.raises(ToolError) as exc:
        srv.reply_to_agent("missing-sid", "answer")
    assert "SESSION_NOT_FOUND" in str(exc.value)


def test_reply_to_agent_not_awaiting_raises(home, monkeypatch):
    # Spawn an agent that exits cleanly with no clarification event.
    wrapper = _make_wrapper(home, sleep=0, exit_code=0, jsonl_lines=[])
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(wrapper))
    res = srv.spawn_agent(prompt="hi", cwd=str(home))
    sid = res["session_id"]
    for _ in range(100):
        listed = srv.list_agents()
        match = [e for e in listed if e["session_id"] == sid][0]
        if match["status"] != "running":
            break
        time.sleep(0.02)
    assert match["status"] == "done"

    with pytest.raises(ToolError) as exc:
        srv.reply_to_agent(sid, "answer")
    assert "NOT_AWAITING_CLARIFICATION" in str(exc.value)


def test_clarification_full_flow(home, monkeypatch):
    lines = [
        {"type": "user", "timestamp": "T1", "message": {"role": "user", "content": "go"}},
        {
            "type": "needs_clarification",
            "timestamp": "T2",
            "question": "MySQL or Postgres?",
            "context": "two DBs configured",
            "urgency": "block",
        },
    ]
    wrapper = _make_wrapper(home, sleep=0, exit_code=0, jsonl_lines=lines)
    monkeypatch.setenv("CLAUDE_AGENTS_MCP_CLAUDE_BIN", str(wrapper))

    res = srv.spawn_agent(prompt="pick db", cwd=str(home))
    sid = res["session_id"]

    for _ in range(100):
        listed = srv.list_agents()
        match = [e for e in listed if e["session_id"] == sid][0]
        if match["status"] != "running":
            break
        time.sleep(0.02)
    assert match["status"] == "awaiting_clarification"

    out = srv.get_agent_output(sid)
    assert out["status"] == "awaiting_clarification"
    assert out["eof"] is True
    types = [e["type"] for e in out["events"]]
    assert "needs_clarification" in types

    # Abort must be blocked while awaiting reply.
    with pytest.raises(ToolError) as exc:
        srv.abort_agent(sid)
    assert "AWAITING_REPLY" in str(exc.value)

    # Reply writes sidecar and flips status to pending_reply.
    rep = srv.reply_to_agent(sid, "Postgres")
    assert rep["status"] == "pending_reply"
    assert rep["replied_at"] > 0
    pending_path = paths.pending_file(sid)
    assert pending_path.exists()
    payload = json.loads(pending_path.read_text())
    assert payload["question"] == "MySQL or Postgres?"
    assert payload["answer"] == "Postgres"

    # Refresh keeps pending_reply sticky.
    listed = srv.list_agents()
    match = [e for e in listed if e["session_id"] == sid][0]
    assert match["status"] == "pending_reply"

    # reply_to_agent again must fail (not awaiting anymore).
    with pytest.raises(ToolError) as exc:
        srv.reply_to_agent(sid, "again")
    assert "NOT_AWAITING_CLARIFICATION" in str(exc.value)

    # Resume spawn consumes the sidecar and starts a new run.
    spy = {}
    orig_spawn = srv.spawn_impl

    def capture(req, registry):
        spy["prompt"] = req.prompt
        spy["session_id"] = req.session_id
        return orig_spawn(req, registry)

    monkeypatch.setattr(srv, "spawn_impl", capture)

    res2 = srv.spawn_agent(prompt="follow-up", cwd=str(home), session_id=sid)
    assert res2["session_id"] == sid
    assert "Previous question: MySQL or Postgres?" in spy["prompt"]
    assert "Your answer: Postgres" in spy["prompt"]
    assert spy["prompt"].endswith("follow-up")
    # Sidecar consumed.
    assert not pending_path.exists()


def _make_wrapper(home: Path, *, sleep: float, exit_code: int, jsonl_lines: list[dict]) -> Path:
    """Build a wrapper script that, given claude-style args, writes jsonl and exits."""
    import shlex
    lines_text = "".join(json.dumps(l) + "\n" for l in jsonl_lines)
    lines_file = home / "lines.jsonl"
    lines_file.write_text(lines_text)
    wrapper = home / "wrap.sh"
    wrapper.write_text(
        f"""#!/usr/bin/env bash
SID=""
ARGS=("$@")
for ((i=0; i<${{#ARGS[@]}}; i++)); do
  if [[ "${{ARGS[$i]}}" == "--session-id" ]]; then
    SID="${{ARGS[$((i+1))]}}"
  fi
done
CWD=$(pwd)
SLUG=$(echo "$CWD" | sed 's|/|-|g')
DIR="$HOME/.claude/projects/$SLUG"
mkdir -p "$DIR"
cp {shlex.quote(str(lines_file))} "$DIR/$SID.jsonl"
sleep {sleep}
exit {exit_code}
"""
    )
    wrapper.chmod(0o755)
    return wrapper
