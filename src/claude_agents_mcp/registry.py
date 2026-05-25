from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from . import paths

TERMINAL_STATUSES = frozenset({"done", "errored", "aborted", "orphaned"})

DEFAULT_TTL_SECONDS = 24 * 60 * 60


@dataclass
class AgentEntry:
    session_id: str
    pid: int
    cwd: str
    log_path: str
    started_at: int
    status: str = "running"
    name: str | None = None
    model: str | None = None
    permission_mode: str = "bypassPermissions"
    ended_at: int | None = None
    resumed_from: str | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentEntry":
        return cls(**data)


class Registry:
    def __init__(self, path: Path | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._path = path or paths.registry_path()
        self._ttl = ttl_seconds
        self._entries: dict[str, AgentEntry] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        if not self._path.exists():
            self._entries = {}
            return
        with self._path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        self._entries = {sid: AgentEntry.from_dict(d) for sid, d in raw.items()}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(
                {sid: e.to_dict() for sid, e in self._entries.items()},
                f,
                indent=2,
                sort_keys=True,
            )
        os.replace(tmp, self._path)

    def add(self, entry: AgentEntry) -> None:
        self._entries[entry.session_id] = entry
        self.save()

    def get(self, session_id: str) -> AgentEntry | None:
        return self._entries.get(session_id)

    def update(self, session_id: str, **fields) -> AgentEntry:
        entry = self._entries[session_id]
        for k, v in fields.items():
            setattr(entry, k, v)
        self.save()
        return entry

    def remove(self, session_id: str) -> None:
        if session_id in self._entries:
            del self._entries[session_id]
            self.save()

    def all(self) -> list[AgentEntry]:
        return list(self._entries.values())

    def prune(self, now_ms: int | None = None) -> list[str]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now_ms - self._ttl * 1000
        to_drop = [
            sid
            for sid, e in self._entries.items()
            if e.status in TERMINAL_STATUSES
            and (e.ended_at is not None and e.ended_at < cutoff)
        ]
        for sid in to_drop:
            del self._entries[sid]
        if to_drop:
            self.save()
        return to_drop
