"""Spawn a new `claude agents` background session by driving the TUI.

We type the prompt into the overview's new-session box, submit it, then watch
the overview (the source of truth) for a new agent row to appear. Confirming
that row is the only signal we need: the spawn succeeded. We deliberately do
NOT return a reusable handle — the TUI auto-generates the agent's display name
a few seconds after start, and `claude agents --json` session ids are not
navigable in the TUI. Callers reconnect via `list_agents` instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import tui_state
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
    status: str
    cwd: str


def spawn(
    req: SpawnRequest,
    controller: TmuxController | None = None,
    *,
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

    return SpawnResult(
        status=new_row.get("status", "running"),
        cwd=req.cwd or "",
    )
