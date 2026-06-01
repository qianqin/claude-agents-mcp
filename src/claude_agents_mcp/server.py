from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import status, tui_state
from .spawner import SpawnError, SpawnRequest, spawn as spawn_impl
from .tmux_controller import TmuxController, TmuxError

_DEFAULT_SERVER_CWD = os.getcwd()

# One persistent tmux session / controller for the whole server process.
_controller: TmuxController | None = None


def _ctrl() -> TmuxController:
    global _controller
    if _controller is None:
        _controller = TmuxController(cwd=_DEFAULT_SERVER_CWD)
    return _controller


mcp = FastMCP(
    name="claude-agents-mcp",
    instructions=(
        "Spawn, observe, message, and abort Claude Code background agents by "
        "driving the `claude agents` TUI inside a persistent tmux session.\n\n"
        "Agents are addressed by their TUI title (the `name`/`title` shown in "
        "list_agents) — a unique prefix is enough. The TUI title is the only "
        "reliable identity: `claude agents --json` session names are sid "
        "prefixes that do NOT match the titles and may list stale/ghost "
        "sessions, so list_sessions is supplementary only.\n\n"
        "Some agent questions render as an arrow-key choice menu in the chat "
        "(not a free-text box). Use select_option to pick an option (by number "
        "or label substring); reply_to_agent is menu-aware and routes free text "
        "through the menu's \"Type something\" escape hatch automatically.\n\n"
        "Tools: spawn_agent (start), list_agents (enumerate from the overview), "
        "get_agent_output (read the chat transcript), send_to_agent (raw text to "
        "the input box) / reply_to_agent (menu-aware reply) / select_option "
        "(answer a choice menu), abort_agent (delete + kill), open_agent / "
        "return_to_overview (navigate), get_tui_state (debug)."
    ),
)


_SPAWN_NOTE = (
    "Agent started. It has no reusable handle: the TUI auto-generates the "
    "agent's display name a few seconds after start (derived from its work), "
    "so the name is not knowable in advance, and no navigable session id is "
    "exposed. To find or address this agent, call list_agents and pick it by "
    "its (now-stable) name/description."
)


@mcp.tool()
def spawn_agent(prompt: str, cwd: str | None = None) -> dict[str, Any]:
    """Spawn a new background agent by typing a prompt into the agents overview.

    Confirms the agent started (a new row appears in the overview) but returns
    NO reusable handle: the TUI auto-generates the display name a few seconds
    after start, so the name is not knowable at spawn time, and no navigable
    session id is exposed. `cwd` selects the working directory of the
    tmux-hosted agents view (effective on first spawn).

    Returns: {spawned, status, note}. To find/address this agent afterward,
    call list_agents and choose it by its (now-stable) name/description.
    """
    use_default = cwd in (None, _DEFAULT_SERVER_CWD)
    controller = _ctrl() if use_default else TmuxController(cwd=cwd)
    req = SpawnRequest(prompt=prompt, cwd=cwd or _DEFAULT_SERVER_CWD)
    try:
        result = spawn_impl(req, controller)
    except SpawnError as e:
        raise ToolError(f"SPAWN_FAILED: {e}") from e
    return {
        "spawned": True,
        "status": result.status,
        "note": _SPAWN_NOTE,
    }


@mcp.tool()
def list_agents(status_filter: str | None = None) -> list[dict[str, Any]]:
    """List agents from the `claude agents` overview (the source of truth).

    Each: {title, status, description, age, section}. `title` is the identity
    used by other tools. Optional status_filter:
    running|done|awaiting_input.
    """
    try:
        rows = _ctrl().list_agents()
    except TmuxError as e:
        raise ToolError(f"LIST_FAILED: {e}") from e
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    return rows


@mcp.tool()
def list_sessions() -> list[dict[str, Any]]:
    """Supplementary raw listing from `claude agents --json`.

    Provides session_id / pid / started_at, but names are sid prefixes (not the
    TUI titles) and the list may include stale/ghost sessions. Prefer
    list_agents for anything actionable.
    """
    try:
        return status.list_live()
    except status.StatusError as e:
        raise ToolError(f"LIST_FAILED: {e}") from e


@mcp.tool()
def get_agent_output(agent: str) -> dict[str, Any]:
    """Read the visible chat transcript of an agent (by TUI title or prefix).

    Opens the agent's chat in the TUI and parses the visible viewport into
    ordered events. Only the on-screen portion is captured (the TUI is a
    full-screen app), so this is the most recent conversation, not full history.
    Returns: {title, agent, events:[{type, text}]}.
    """
    try:
        chat = _ctrl().read_agent(agent)
    except TmuxError as e:
        raise ToolError(f"READ_FAILED: {e}") from e
    if chat is None:
        raise ToolError(f"AGENT_NOT_FOUND: {agent}")
    return {"title": agent, "agent": chat.get("agent"), "events": chat["events"]}


@mcp.tool()
def send_to_agent(agent: str, message: str) -> dict[str, Any]:
    """Send a message into an agent's chat input (by TUI title or prefix).

    Opens the agent's chat, types the message, and submits it.
    Returns: {agent, sent}.
    """
    try:
        ok = _ctrl().send_message(agent, message)
    except TmuxError as e:
        raise ToolError(f"SEND_FAILED: {e}") from e
    if not ok:
        raise ToolError(f"SEND_FAILED: could not open/verify agent {agent!r}")
    return {"agent": agent, "sent": True}


@mcp.tool()
def select_option(agent: str, option: str) -> dict[str, Any]:
    """Answer an agent's choice-menu question by picking an option.

    When a background agent asks a question (AskUserQuestion), its chat renders
    an arrow-key selection menu. `option` may be the option number ("2") or a
    case-insensitive substring of an option's label/description ("Second"). The
    controller navigates the highlight to the option, verifies, and confirms.
    Returns: {agent, selected, option}. Raises NOT_A_MENU if the agent isn't
    showing a choice menu, or SELECT_FAILED if navigation/verification failed.
    """
    ctrl = _ctrl()
    try:
        if not ctrl.chat_is_menu(agent):
            raise ToolError(f"NOT_A_MENU: {agent!r} is not showing a choice menu")
        ok = ctrl.select_option(agent, option)
    except TmuxError as e:
        raise ToolError(f"SELECT_FAILED: {e}") from e
    if not ok:
        raise ToolError(f"SELECT_FAILED: could not select {option!r} for {agent!r}")
    return {"agent": agent, "selected": True, "option": option}


@mcp.tool()
def reply_to_agent(agent: str, answer: str) -> dict[str, Any]:
    """Reply to an agent awaiting input (a clarification question).

    Menu-aware: if the agent's chat is currently a choice menu, `answer` is
    routed through the free-text "Type something" option (select it, then type
    the text). Otherwise the answer is typed into the normal chat input box
    (same mechanism as send_to_agent). Returns: {agent, sent}.
    """
    ctrl = _ctrl()
    try:
        if ctrl.chat_is_menu(agent):
            ok = ctrl.answer_custom(agent, answer)
            if not ok:
                raise ToolError(
                    f"SEND_FAILED: could not answer menu for {agent!r}"
                )
            return {"agent": agent, "sent": True}
    except TmuxError as e:
        raise ToolError(f"SEND_FAILED: {e}") from e
    return send_to_agent(agent, answer)


@mcp.tool()
def open_agent(agent: str) -> dict[str, Any]:
    """Navigate the TUI to an agent's chat view and verify it opened.

    Returns: {agent, opened}.
    """
    try:
        ok = _ctrl().open_agent(agent)
    except TmuxError as e:
        raise ToolError(f"OPEN_FAILED: {e}") from e
    return {"agent": agent, "opened": ok}


@mcp.tool()
def return_to_overview() -> dict[str, Any]:
    """Return the TUI to the agents overview list. Returns: {view}."""
    try:
        _ctrl().return_to_overview()
    except TmuxError as e:
        raise ToolError(f"NAV_FAILED: {e}") from e
    return {"view": _ctrl().view().value}


@mcp.tool()
def abort_agent(agent: str) -> dict[str, Any]:
    """Delete (abort) an agent via the TUI (select + Ctrl+X + confirm Ctrl+X).

    Deleting from the agents view kills the agent process. Addressed by TUI
    title or prefix. Returns: {agent, aborted}.
    """
    try:
        ok = _ctrl().abort_agent(agent)
    except TmuxError as e:
        raise ToolError(f"ABORT_FAILED: {e}") from e
    if not ok:
        raise ToolError(f"ABORT_FAILED: could not locate agent {agent!r}")
    return {"agent": agent, "aborted": True}


@mcp.tool()
def get_tui_state() -> dict[str, Any]:
    """Inspect the current TUI state (for debugging the controller).

    Returns: {view, chat_agent, input_text, counts, selected}.
    """
    ctrl = _ctrl()
    try:
        ctrl.ensure_session()
    except TmuxError as e:
        raise ToolError(f"TUI_UNAVAILABLE: {e}") from e
    plain = ctrl.capture()
    return {
        "view": tui_state.classify(plain).value,
        "chat_agent": tui_state.chat_agent_name(plain),
        "input_text": tui_state.input_text(plain),
        "counts": tui_state.counts(plain),
        "selected": tui_state.selected_name(ctrl.capture(ansi=True)),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
