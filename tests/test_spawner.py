from __future__ import annotations

import pytest

from claude_agents_mcp import spawner
from claude_agents_mcp.spawner import SpawnError, SpawnRequest


def _overview(titles):
    rows = [
        "0 awaiting input · 1 working · 1 completed",
        "",
        "Working",
    ]
    for t in titles:
        rows.append(f" ✻ {t}                 some description here   3s")
    rows += ["", "─" * 40, "❯ describe a task for a new session", "─" * 40, "? for shortcuts"]
    return "\n".join(rows)


class FakeController:
    """Switches its overview from `before` to `after` once spawn() is called."""

    def __init__(self, before_titles, after_titles):
        self._before = _overview(before_titles)
        self._after = _overview(after_titles)
        self._spawned = False
        self.prompt = None

    def ensure_overview(self):
        pass

    def capture(self, ansi=False):
        return self._after if self._spawned else self._before

    def spawn(self, prompt):
        self.prompt = prompt
        self._spawned = True


def test_spawn_detects_new_title():
    ctrl = FakeController(["old agent"], ["old agent", "shiny new task"])
    res = spawner.spawn(
        SpawnRequest(prompt="make it", cwd="/x"),
        ctrl,
        runner=None,
        sleep=lambda _: None,
    )
    assert ctrl.prompt == "make it"
    assert res.title == "shiny new task"
    assert res.status == "running"


def test_spawn_times_out_if_no_new_title():
    ctrl = FakeController(["old agent"], ["old agent"])  # nothing new appears
    clock = {"t": 0.0}

    def mono():
        clock["t"] += 10.0
        return clock["t"]

    with pytest.raises(SpawnError) as exc:
        spawner.spawn(
            SpawnRequest(prompt="x", cwd="/x"),
            ctrl,
            runner=None,
            sleep=lambda _: None,
            monotonic=mono,
        )
    assert "no new agent row" in str(exc.value)
