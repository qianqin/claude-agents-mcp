"""Spawn a new `claude agents` background session by driving the TUI.

We type the prompt into the overview's new-session box, submit it, then watch
the overview (the source of truth) for a new agent row to appear and return its
title — the actionable identity used by every other tool.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import status, tui_state
from .tmux_controller import TmuxController

SPAWN_POLL_TIMEOUT = 25.0
SPAWN_POLL_INTERVAL = 0.5


class SpawnError(RuntimeError):
    pass


@dataclass
class SpawnRequest:
    prompt: str
    cwd: str | None = None
    name: str | None = None  # advisory only; the TUI auto-titles sessions


@dataclass
class SpawnResult:
    title: str
    status: str
    description: str | None
    session_id: str | None  # best-effort from `claude agents --json`
    cwd: str


def _best_effort_session_ids(runner) -> set[str]:
    try:
        kwargs = {"runner": runner} if runner is not None else {}
        return {r["session_id"] for r in status.list_live(**kwargs) if r.get("session_id")}
    except status.StatusError:
        return set()


def spawn(
    req: SpawnRequest,
    controller: TmuxController | None = None,
    *,
    runner=None,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> SpawnResult:
    if controller is None:
        controller = TmuxController(cwd=req.cwd)

    try:
        controller.ensure_overview()
        titles_before = set(tui_state.overview_titles(controller.capture()))
    except Exception as e:
        raise SpawnError(f"could not read overview before spawn: {e}") from e

    sids_before = _best_effort_session_ids(runner)

    try:
        controller.spawn(req.prompt)
    except Exception as e:
        raise SpawnError(f"failed to drive TUI: {e}") from e

    deadline = monotonic() + SPAWN_POLL_TIMEOUT
    new_row = None
    while monotonic() < deadline:
        rows = tui_state.parse_overview_rows(controller.capture())
        new_titles = [r for r in rows if r["title"] not in titles_before]
        if new_titles:
            # Prefer a freshly Working row; otherwise take the first new one.
            working = [r for r in new_titles if r.get("section") == "Working"]
            new_row = working[0] if working else new_titles[0]
            break
        sleep(SPAWN_POLL_INTERVAL)

    if new_row is None:
        raise SpawnError(
            "spawned prompt but no new agent row appeared in the overview "
            f"within {SPAWN_POLL_TIMEOUT}s"
        )

    new_sids = _best_effort_session_ids(runner) - sids_before
    session_id = next(iter(new_sids)) if new_sids else None

    return SpawnResult(
        title=new_row["title"],
        status=new_row.get("status", "running"),
        description=new_row.get("description"),
        session_id=session_id,
        cwd=req.cwd or "",
    )
