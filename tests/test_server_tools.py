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
