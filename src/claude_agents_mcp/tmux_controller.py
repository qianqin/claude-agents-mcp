"""Drive the `claude agents` TUI inside a persistent tmux session.

All control flow follows the spec's golden rule: after every keypress, settle
briefly then re-capture the pane and VERIFY the resulting state before sending
the next keys. We never fire blind key sequences.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from . import tui_state
from .tui_state import View

DEFAULT_SESSION = "claude-agents-mcp"
SESSION_ENV = "CLAUDE_AGENTS_MCP_TMUX_SESSION"
CLAUDE_BIN_ENV = "CLAUDE_AGENTS_MCP_CLAUDE_BIN"
CWD_ENV = "CLAUDE_AGENTS_MCP_CWD"

SETTLE_SECONDS = 0.5
PANE_WIDTH = 220
PANE_HEIGHT = 50
# Bound on navigation / state-wait loops so a misbehaving TUI can't hang us.
MAX_NAV_STEPS = 60
MAX_STATE_WAIT = 40


class TmuxError(RuntimeError):
    pass


def claude_bin() -> str:
    return os.environ.get(CLAUDE_BIN_ENV, "claude")


@dataclass
class AgentName:
    """Result of locating an agent in the overview."""

    name: str
    found: bool


class TmuxController:
    def __init__(
        self,
        session: str | None = None,
        *,
        cwd: str | None = None,
        runner=subprocess.run,
        sleep=time.sleep,
        settle: float = SETTLE_SECONDS,
    ) -> None:
        self.session = session or os.environ.get(SESSION_ENV, DEFAULT_SESSION)
        self.cwd = cwd or os.environ.get(CWD_ENV) or os.getcwd()
        self._run = runner
        self._sleep = sleep
        self._settle_s = settle

    # --- low-level tmux ----------------------------------------------------

    def _tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def session_exists(self) -> bool:
        proc = self._run(
            ["tmux", "has-session", "-t", self.session],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def capture(self, *, ansi: bool = False) -> str:
        args = ["capture-pane", "-t", self.session, "-p"]
        if ansi:
            args.append("-e")
        proc = self._tmux(*args)
        return proc.stdout

    def _settle(self) -> None:
        self._sleep(self._settle_s)

    def send_key(self, *keys: str, settle: bool = True) -> None:
        self._tmux("send-keys", "-t", self.session, *keys)
        if settle:
            self._settle()

    def send_text(self, text: str, settle: bool = True) -> None:
        """Insert literal text (incl. newlines) without triggering key handling,
        via a tmux paste buffer with bracketed paste."""
        self._tmux("set-buffer", "--", text)
        self._tmux("paste-buffer", "-t", self.session, "-d", "-p")
        if settle:
            self._settle()

    def clear_input(self) -> None:
        # readline-style: jump to start, kill to end of line.
        self.send_key("C-a", settle=False)
        self.send_key("C-k")

    # --- state -------------------------------------------------------------

    def view(self) -> View:
        return tui_state.classify(self.capture())

    def _wait_for(self, predicate, *, what: str):
        for _ in range(MAX_STATE_WAIT):
            plain = self.capture()
            if predicate(plain):
                return plain
            self._sleep(self._settle_s)
        raise TmuxError(f"timed out waiting for {what}")

    # --- session lifecycle -------------------------------------------------

    def ensure_session(self) -> None:
        """Create the tmux session running `claude agents` if needed, then make
        sure it is showing a usable TUI (handling the trust dialog)."""
        if not self.session_exists():
            self._tmux(
                "new-session",
                "-d",
                "-s",
                self.session,
                "-x",
                str(PANE_WIDTH),
                "-y",
                str(PANE_HEIGHT),
                "-c",
                self.cwd,
            )
            self._launch_claude()
        self.ensure_tui()

    def _launch_claude(self) -> None:
        self.send_key("-l", f"{claude_bin()} agents", settle=False)
        self.send_key("Enter")

    def ensure_tui(self) -> None:
        """Ensure the pane shows the agents TUI (overview or chat). Restart
        `claude agents` if it has exited to a shell; clear a trust dialog."""
        for _ in range(MAX_STATE_WAIT):
            view = self.view()
            if view in (View.OVERVIEW, View.CHAT):
                return
            if view == View.TRUST:
                self.send_key("Enter")
                continue
            if view == View.SHELL:
                self._launch_claude()
                self._settle()
                continue
            # UNKNOWN: still rendering.
            self._sleep(self._settle_s)
        raise TmuxError("could not reach a usable claude agents TUI")

    # --- navigation --------------------------------------------------------

    def ensure_overview(self) -> None:
        self.ensure_session()
        for _ in range(MAX_STATE_WAIT):
            plain = self.capture()
            view = tui_state.classify(plain)
            if view == View.OVERVIEW:
                return
            if view == View.CHAT:
                # Left only exits when the input is empty.
                if not tui_state.input_is_empty(plain):
                    self.clear_input()
                self.send_key("Left")
                continue
            if view == View.TRUST:
                self.send_key("Enter")
                continue
            if view == View.SHELL:
                self._launch_claude()
                self._settle()
                continue
            self._sleep(self._settle_s)
        raise TmuxError("could not return to overview")

    def select_agent(self, name: str) -> bool:
        """Move the overview selection onto the named agent. Returns True if it
        landed on it. Verifies via the ANSI highlight, never by step-counting."""
        self.ensure_overview()
        seen: list[str] = []
        for _ in range(MAX_NAV_STEPS):
            selected = tui_state.selected_name(self.capture(ansi=True))
            if selected is not None and tui_state.name_matches(name, selected):
                return True
            # Detect a full wrap: if we return to a previously seen selection.
            if selected is not None:
                if selected in seen and len(seen) > 1:
                    return False
                seen.append(selected)
            self.send_key("Down")
        return False

    def open_agent(self, name: str) -> bool:
        """Open the named agent's chat. Verifies the chat header matches."""
        plain = self.capture()
        if tui_state.classify(plain) == View.CHAT:
            current = tui_state.chat_agent_name(plain)
            if current and tui_state.name_matches(name, current):
                return True
        if not self.select_agent(name):
            return False
        self.send_key("Enter")
        plain = self.capture()
        if tui_state.classify(plain) != View.CHAT:
            return False
        current = tui_state.chat_agent_name(plain)
        return bool(current and tui_state.name_matches(name, current))

    def return_to_overview(self) -> None:
        self.ensure_overview()

    # --- reads -------------------------------------------------------------

    def list_agents(self) -> list[dict]:
        """Parsed overview rows (the source of truth for actionable agents)."""
        self.ensure_overview()
        return tui_state.parse_overview_rows(self.capture())

    def read_agent(self, title: str) -> dict | None:
        """Open the agent's chat and return its parsed visible transcript."""
        if not self.open_agent(title):
            return None
        return tui_state.parse_chat(self.capture())

    # --- actions -----------------------------------------------------------

    def spawn(self, prompt: str) -> None:
        """Type a new-session prompt in the overview and submit it."""
        self.ensure_overview()
        self.clear_input()
        self.send_text(prompt)
        self.send_key("Enter")

    def send_message(self, name: str, message: str) -> bool:
        """Open the agent and send a message into its chat input."""
        if not self.open_agent(name):
            return False
        self.clear_input()
        self.send_text(message)
        self.send_key("Enter")
        return True

    def chat_is_menu(self, name: str) -> bool:
        """Open the agent and report whether its chat is a choice menu."""
        if not self.open_agent(name):
            return False
        return tui_state.is_choice_menu(self.capture())

    def _resolve_option_index(self, plain: str, option: str) -> int | None:
        """Map `option` (a number or case-insensitive label substring) to a
        menu option index, or None if it matches no option."""
        options = tui_state.parse_menu_options(plain)
        if option.isdigit():
            idx = int(option)
            return idx if any(o["index"] == idx for o in options) else None
        needle = option.strip().lower()
        for o in options:
            haystack = (o["label"] + " " + o.get("description", "")).lower()
            if needle in haystack:
                return o["index"]
        return None

    def _navigate_to_option(self, target: int) -> bool:
        """Move the `❯` marker onto option `target`, verifying after each key."""
        for _ in range(MAX_NAV_STEPS):
            current = tui_state.selected_option_index(self.capture(ansi=True))
            if current is None:
                return False
            if current == target:
                return True
            self.send_key("Down" if target > current else "Up")
        return False

    def select_option(self, name: str, option: str) -> bool:
        """Answer a choice menu by selecting `option` (number or label substring).

        Opens the agent, confirms a menu is showing (else False), navigates the
        `❯` marker to the target via arrow keys (verifying after each press),
        presses Enter, and confirms the menu is gone. Returns True on success.
        """
        if not self.open_agent(name):
            return False
        plain = self.capture()
        if not tui_state.is_choice_menu(plain):
            return False
        target = self._resolve_option_index(plain, option)
        if target is None:
            return False
        if not self._navigate_to_option(target):
            return False
        self.send_key("Enter")
        return not tui_state.is_choice_menu(self.capture())

    def answer_custom(self, name: str, text: str) -> bool:
        """Answer a choice menu with free text via the "Type something" option.

        Selects the custom-text option, presses Enter, types `text`, and submits.
        Returns True if the menu was present and the text was sent.
        """
        if not self.open_agent(name):
            return False
        plain = self.capture()
        if not tui_state.is_choice_menu(plain):
            return False
        target = next(
            (
                o["index"]
                for o in tui_state.parse_menu_options(plain)
                if tui_state.is_custom_text_option(o["label"])
            ),
            None,
        )
        if target is None:
            return False
        if not self._navigate_to_option(target):
            return False
        self.send_key("Enter")
        self.send_text(text)
        self.send_key("Enter")
        return True

    def abort_agent(self, name: str) -> bool:
        """Select the agent in the overview and delete it. Ctrl+X arms the
        delete; a second Ctrl+X within the confirm window commits it (footer
        shows "ctrl+x to confirm"). Deleting kills the agent process."""
        self.ensure_overview()
        if not self.select_agent(name):
            return False
        self.send_key("C-x", settle=False)
        self._settle()
        if "ctrl+x to confirm" in self.capture().lower():
            self.send_key("C-x")
        return True
