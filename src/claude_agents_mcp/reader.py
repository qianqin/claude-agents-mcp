from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


META_TYPES = frozenset({"last-prompt", "agent-setting", "permission-mode", "summary"})


@dataclass
class ReadResult:
    events: list[dict]
    next_offset: int
    eof: bool

    def to_dict(self) -> dict:
        return {
            "events": self.events,
            "next_offset": self.next_offset,
            "eof": self.eof,
        }


def _iter_jsonl_from(path: Path, offset: int) -> Iterator[tuple[int, dict]]:
    """Yield (end_byte_offset, parsed) for each complete JSONL line.

    A partial trailing line (no newline yet) is left for the next call —
    `end_byte_offset` returned by the iterator does NOT advance past it.
    """
    with path.open("rb") as f:
        f.seek(offset)
        buf = b""
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                nl = buf.find(b"\n")
                if nl == -1:
                    break
                line = buf[:nl]
                buf = buf[nl + 1 :]
                offset += len(line) + 1
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    # Skip a malformed line but advance past it.
                    continue
                yield offset, parsed


def _parse_event(line: dict) -> dict | None:
    """Translate a raw JSONL line into the friendly event shape.
    Returns None to drop the line (meta) or for unknown types.
    """
    t = line.get("type")
    if t in META_TYPES:
        return None

    ts = line.get("timestamp")

    if t == "user":
        msg = line.get("message", {})
        return {
            "type": "user",
            "role": "user",
            "ts": ts,
            "content": msg.get("content"),
        }

    if t == "assistant":
        msg = line.get("message", {})
        content = msg.get("content", [])
        events: list[dict] = []
        if isinstance(content, list):
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    events.append({
                        "type": "assistant_text",
                        "role": "assistant",
                        "ts": ts,
                        "content": block.get("text", ""),
                    })
                elif btype == "thinking":
                    events.append({
                        "type": "assistant_thinking",
                        "role": "assistant",
                        "ts": ts,
                        "content": block.get("thinking", ""),
                    })
                elif btype == "tool_use":
                    events.append({
                        "type": "tool_use",
                        "role": "assistant",
                        "ts": ts,
                        "name": block.get("name"),
                        "id": block.get("id"),
                        "input": block.get("input"),
                    })
        # Multiple blocks → caller gets a list packaging
        return {"_expanded": events} if events else None

    if t == "tool_result":
        return {
            "type": "tool_result",
            "ts": ts,
            "tool_use_id": line.get("toolUseId") or line.get("tool_use_id"),
            "content": line.get("content"),
            "is_error": line.get("isError") or line.get("is_error", False),
        }

    if t == "system":
        return {
            "type": "system",
            "ts": ts,
            "content": line.get("content") or line.get("message"),
        }

    if t == "needs_clarification":
        return {
            "type": "needs_clarification",
            "ts": ts,
            "question": line.get("question"),
            "context": line.get("context"),
            "urgency": line.get("urgency"),
        }

    return None


def find_latest_clarification(path: Path) -> dict | None:
    """Scan the log file and return the latest needs_clarification event as a
    parsed dict ({question, context, urgency, ts}), or None if not present."""
    if not path.exists():
        return None
    latest: dict | None = None
    for _, line in _iter_jsonl_from(path, 0):
        if line.get("type") == "needs_clarification":
            latest = {
                "type": "needs_clarification",
                "ts": line.get("timestamp"),
                "question": line.get("question"),
                "context": line.get("context"),
                "urgency": line.get("urgency"),
            }
    return latest


def read(
    path: Path,
    offset: int = 0,
    *,
    fmt: str = "parsed",
    limit: int | None = None,
) -> ReadResult:
    if fmt not in ("parsed", "raw"):
        raise ValueError(f"unknown format: {fmt!r}")

    events: list[dict] = []
    last_offset = offset

    if not path.exists():
        return ReadResult(events=[], next_offset=offset, eof=False)

    for end_offset, line in _iter_jsonl_from(path, offset):
        last_offset = end_offset
        if fmt == "raw":
            events.append(line)
        else:
            ev = _parse_event(line)
            if ev is None:
                continue
            if "_expanded" in ev:
                events.extend(ev["_expanded"])
            else:
                events.append(ev)
        if limit is not None and len(events) >= limit:
            break

    return ReadResult(events=events, next_offset=last_offset, eof=False)
