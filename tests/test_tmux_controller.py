from __future__ import annotations

import subprocess

from claude_agents_mcp.tmux_controller import TmuxController


class FakeRunner:
    """Records tmux invocations and returns scripted capture-pane output.

    plain/ansi are lists consumed front-to-back; the last element repeats once
    exhausted. exists controls `has-session` return code.
    """

    def __init__(self, plain, ansi=None, exists=True):
        self.calls: list[list[str]] = []
        self._plain = list(plain)
        self._ansi = list(ansi or plain)
        self.exists = exists

    def _pop(self, seq):
        if not seq:
            return ""
        if len(seq) == 1:
            return seq[0]
        return seq.pop(0)

    def __call__(self, args, capture_output=True, text=True, check=True):
        self.calls.append(list(args))
        cmd = args[1] if len(args) > 1 else ""
        if cmd == "has-session":
            return subprocess.CompletedProcess(args, 0 if self.exists else 1, "", "")
        if cmd == "capture-pane":
            out = self._pop(self._ansi if "-e" in args else self._plain)
            return subprocess.CompletedProcess(args, 0, out, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def sent_keys(self):
        return [c for c in self.calls if len(c) > 1 and c[1] == "send-keys"]


OVERVIEW = "\n".join(
    [
        "0 awaiting input · 1 working · 1 completed",
        "",
        "Working",
        " ✻ my agent                 working on it   3s",
        "",
        "Completed",
        " ∙ other agent              all done   2h",
        "",
        "─" * 40,
        "❯ describe a task for a new session",
        "─" * 40,
        "? for shortcuts",
    ]
)
CHAT = "\n".join(["● hi", "─" * 10 + " my agent " + "─" * 10, "❯ ", "─" * 40, "← for agents"])
CHAT_TYPED = CHAT.replace("❯ \n", "❯ leftover\n")


def _noop_sleep(_):
    pass


def test_clear_input_sends_ca_ck():
    r = FakeRunner(plain=[OVERVIEW])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    c.clear_input()
    keys = ["".join(k[3:]) for k in r.sent_keys()]
    assert any("C-a" in k for k in keys)
    assert any("C-k" in k for k in keys)


def test_send_text_uses_paste_buffer():
    r = FakeRunner(plain=[OVERVIEW])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    c.send_text("hello world\nsecond line")
    set_buffer = [c2 for c2 in r.calls if len(c2) > 1 and c2[1] == "set-buffer"]
    paste = [c2 for c2 in r.calls if len(c2) > 1 and c2[1] == "paste-buffer"]
    assert set_buffer and set_buffer[0][-1] == "hello world\nsecond line"
    assert paste and "-p" in paste[0]


def test_ensure_overview_from_chat_clears_and_lefts():
    # ensure_session/ensure_tui consumes the first capture; the ensure_overview
    # loop then sees typed chat (needs clearing), then overview after Left.
    r = FakeRunner(plain=[CHAT_TYPED, CHAT_TYPED, OVERVIEW])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    c.ensure_overview()
    keys = ["".join(k[3:]) for k in r.sent_keys()]
    assert any("C-a" in k for k in keys)  # cleared input
    assert any("Left" in k for k in keys)  # then Left to overview


def test_select_agent_matches_via_highlight():
    sel_header = "\x1b[48;5;255mCompleted\x1b[0m"
    sel_agent = "\x1b[48;5;255m ✻ my agent\x1b[0m   desc   1h"
    r = FakeRunner(plain=[OVERVIEW], ansi=[sel_header, sel_agent])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    assert c.select_agent("my agent") is True
    keys = ["".join(k[3:]) for k in r.sent_keys()]
    assert any("Down" in k for k in keys)


def test_spawn_types_prompt_and_enters():
    r = FakeRunner(plain=[OVERVIEW])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    c.spawn("do a thing")
    paste = [c2 for c2 in r.calls if len(c2) > 1 and c2[1] == "set-buffer"]
    assert paste and paste[0][-1] == "do a thing"
    keys = ["".join(k[3:]) for k in r.sent_keys()]
    assert any("Enter" in k for k in keys)


def test_open_agent_verifies_chat_header():
    sel_agent = "\x1b[48;5;255m ✻ my agent\x1b[0m   desc   1h"
    # Sequence of plain captures: overview (ensure), overview (select pre-check),
    # then chat after Enter.
    r = FakeRunner(plain=[OVERVIEW, OVERVIEW, OVERVIEW, CHAT], ansi=[sel_agent])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    assert c.open_agent("my agent") is True


CONFIRM = OVERVIEW.replace("? for shortcuts", "ctrl+x to confirm")


def test_abort_sends_double_ctrl_x():
    sel_agent = "\x1b[48;5;255m ✻ my agent\x1b[0m   desc   1h"
    # After the first C-x the footer shows the confirm prompt.
    r = FakeRunner(plain=[OVERVIEW, OVERVIEW, CONFIRM], ansi=[sel_agent])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    assert c.abort_agent("my agent") is True
    ctrl_x = [k for k in r.sent_keys() if "C-x" in "".join(k[3:])]
    assert len(ctrl_x) == 2  # arm + confirm


def test_list_agents_parses_overview():
    r = FakeRunner(plain=[OVERVIEW])
    c = TmuxController(runner=r, sleep=_noop_sleep)
    rows = c.list_agents()
    titles = {row["title"] for row in rows}
    assert "my agent" in titles
    assert "other agent" in titles
