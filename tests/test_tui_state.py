from __future__ import annotations

from claude_agents_mcp import tui_state
from claude_agents_mcp.tui_state import NEW_SESSION_PLACEHOLDER, View

OVERVIEW = "\n".join(
    [
        " ▐▛███▜▌   Claude Code v2.1.156",
        "▝▜█████▛▘  Opus 4.8 (1M context) · ~/projects",
        "  ▘▘ ▝▝    0 awaiting input · 1 working · 9 completed",
        "",
        "Working",
        " ✻ read /tmp/rebuild-spec.m…               Bash cd ...   42s",
        "",
        "Completed",
        " ✻ cloudflare pages deployment             Spec for ...   4h",
        " ∙ mcp server claude agents                claude-... 3d",
        "",
        "─" * 80,
        "❯ describe a task for a new session",
        "─" * 80,
        "  enter to open · space to reply · ctrl+x to delete · ? for shortcuts",
    ]
)

CHAT = "\n".join(
    [
        "※ recap: setting up www repo ...",
        "",
        "❯ looks good",
        "",
        "● Approved. Clone repo, write spec.",
        "",
        "─" * 30 + " cloudflare pages deployment " + "─" * 20,
        "❯ ",
        "─" * 80,
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    ]
)

CHAT_TYPING = CHAT.replace("❯ \n", "❯ hello world\n")

TRUST = "\n".join(
    [
        "Do you trust the files in this folder?",
        "/home/qian/projects",
        "❯ 1. Yes, proceed",
        "  2. No, exit",
    ]
)

SHELL = "\n".join(["qian@host:~/projects$ ", ""])


def test_classify_overview():
    assert tui_state.classify(OVERVIEW) == View.OVERVIEW


def test_classify_chat():
    assert tui_state.classify(CHAT) == View.CHAT


def test_classify_trust():
    assert tui_state.classify(TRUST) == View.TRUST


def test_classify_shell():
    assert tui_state.classify(SHELL) == View.SHELL


def test_chat_agent_name():
    assert tui_state.chat_agent_name(CHAT) == "cloudflare pages deployment"
    assert tui_state.chat_agent_name(OVERVIEW) is None


def test_input_empty_vs_placeholder():
    # Overview placeholder reads as empty.
    assert tui_state.input_is_empty(OVERVIEW) is True
    # Empty chat input.
    assert tui_state.input_is_empty(CHAT) is True
    # Typed chat input.
    assert tui_state.input_text(CHAT_TYPING) == "hello world"
    assert tui_state.input_is_empty(CHAT_TYPING) is False


def test_counts():
    c = tui_state.counts(OVERVIEW)
    assert c == {"awaiting_input": 0, "working": 1, "completed": 9}
    assert tui_state.counts(CHAT) is None


def test_highlighted_row_and_selected_name():
    ansi = (
        " \x1b[48;5;255m \x1b[38;5;65m✻\x1b[38;5;16m cloudflare pages deployment"
        "\x1b[39m             \x1b[38;5;241mSpec for ...\x1b[39m   4h"
    )
    row = tui_state.highlighted_row(ansi)
    assert "cloudflare pages deployment" in row
    assert tui_state.selected_name(ansi) == "cloudflare pages deployment"


def test_selected_name_section_header():
    ansi = "\x1b[1m\x1b[38;5;16m\x1b[48;5;255mCompleted\x1b[0m"
    assert tui_state.selected_name(ansi) == "Completed"


def test_no_selection():
    assert tui_state.highlighted_row("no highlight here") is None
    assert tui_state.selected_name("no highlight here") is None


def test_parse_overview_rows():
    rows = tui_state.parse_overview_rows(OVERVIEW)
    titles = [r["title"] for r in rows]
    assert "read /tmp/rebuild-spec.m…" in titles
    assert "cloudflare pages deployment" in titles
    by_title = {r["title"]: r for r in rows}
    assert by_title["read /tmp/rebuild-spec.m…"]["section"] == "Working"
    assert by_title["read /tmp/rebuild-spec.m…"]["status"] == "running"
    assert by_title["cloudflare pages deployment"]["section"] == "Completed"
    assert by_title["cloudflare pages deployment"]["status"] == "done"
    assert by_title["cloudflare pages deployment"]["age"] == "4h"
    # The new-session input box is not an agent row.
    assert all(NEW_SESSION_PLACEHOLDER not in t for t in titles)


def test_overview_titles():
    titles = tui_state.overview_titles(OVERVIEW)
    assert "mcp server claude agents" in titles


def test_parse_chat_events():
    chat = tui_state.parse_chat(CHAT)
    assert chat["agent"] == "cloudflare pages deployment"
    types = [e["type"] for e in chat["events"]]
    assert types[0] == "recap"
    assert "user" in types and "assistant" in types
    user = next(e for e in chat["events"] if e["type"] == "user")
    assert user["text"] == "looks good"
    assert any("Approved" in e["text"] for e in chat["events"] if e["type"] == "assistant")


def test_name_matches_truncated():
    assert tui_state.name_matches(
        "read /tmp/rebuild-spec.md and rebuild claude-agents-mcp",
        "read /tmp/rebuild-spec.m…",
    )
    assert tui_state.name_matches("exactname", "exactname")
    assert not tui_state.name_matches("alpha", "beta")
    assert not tui_state.name_matches("alpha", "")
