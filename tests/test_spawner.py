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


def test_spawn_confirms_via_overview_only():
    ctrl = FakeController(["old agent"], ["old agent", "shiny new task"])
    res = spawner.spawn(
        SpawnRequest(prompt="make it", cwd="/x"),
        ctrl,
        sleep=lambda _: None,
    )
    assert ctrl.prompt == "make it"
    # status derived from the new row's section; cwd set from the request.
    assert res.status == "running"
    assert res.cwd == "/x"


def test_spawn_result_has_no_handle_fields():
    ctrl = FakeController(["old agent"], ["old agent", "shiny new task"])
    res = spawner.spawn(
        SpawnRequest(prompt="make it", cwd="/x"),
        ctrl,
        sleep=lambda _: None,
    )
    assert not hasattr(res, "title")
    assert not hasattr(res, "description")
    assert not hasattr(res, "session_id")


def test_spawn_accepts_no_runner_argument():
    ctrl = FakeController(["old agent"], ["old agent", "shiny new task"])
    with pytest.raises(TypeError):
        spawner.spawn(
            SpawnRequest(prompt="x", cwd="/x"),
            ctrl,
            runner=None,
            sleep=lambda _: None,
        )


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
            sleep=lambda _: None,
            monotonic=mono,
        )
    assert "no new agent row" in str(exc.value)
