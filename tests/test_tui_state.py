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


# --- choice menu (AskUserQuestion rendered as an arrow-key selection) --------

# Plain (`-p`) capture of a choice menu, per the verified live layout.
MENU = "\n".join(
    [
        "Which option you pick?",
        "❯ 1. A",
        "     First choice.",
        "  2. B",
        "     Second choice.",
        "  3. C",
        "     Third choice.",
        "  4. Type something.",
        "─" * 5,
        "  5. Chat about this",
        "Enter to select · ↑/↓ to navigate · Esc to cancel",
    ]
)


def _menu_ansi(selected: int) -> str:
    """Escape-coded menu capture with `❯` + fg 38;5;105m on `selected` row."""
    rows = [
        (1, "A"),
        (2, "B"),
        (3, "C"),
        (4, "Type something."),
        (5, "Chat about this"),
    ]
    lines = ["Which option you pick?"]
    for idx, label in rows:
        if idx == selected:
            lines.append(
                f"\x1b[38;5;105m❯\x1b[39m \x1b[38;5;241m{idx}.\x1b[39m "
                f"\x1b[38;5;105m{label}\x1b[39m"
            )
        else:
            lines.append(f"\x1b[38;5;241m  {idx}. {label}\x1b[39m")
    lines.append("Enter to select · ↑/↓ to navigate · Esc to cancel")
    return "\n".join(lines)


def test_is_choice_menu_true():
    assert tui_state.is_choice_menu(MENU) is True


def test_is_choice_menu_false_for_free_text_chat():
    assert tui_state.is_choice_menu(CHAT) is False
    assert tui_state.is_choice_menu(OVERVIEW) is False


def test_parse_menu_options():
    opts = tui_state.parse_menu_options(MENU)
    indexed = [(o["index"], o["label"]) for o in opts]
    assert indexed == [
        (1, "A"),
        (2, "B"),
        (3, "C"),
        (4, "Type something."),
        (5, "Chat about this"),
    ]
    by_index = {o["index"]: o for o in opts}
    assert by_index[1]["description"] == "First choice."
    assert by_index[2]["description"] == "Second choice."


def test_parse_menu_options_ignores_chat_header_rule():
    # The chat-header rule (agent name embedded in dashes) sits below the last
    # option; it must NOT leak into that option's description, otherwise label
    # substring matching would falsely resolve a word from the agent title.
    menu_with_header = "\n".join(
        [
            "Which option you pick?",
            "❯ 1. A",
            "     First choice.",
            "  2. B",
            "     Second choice.",
            "─" * 10 + " my agent name " + "─" * 10,
            "Enter to select · ↑/↓ to navigate · Esc to cancel",
        ]
    )
    opts = tui_state.parse_menu_options(menu_with_header)
    last = opts[-1]
    assert last["index"] == 2
    assert last["description"] == "Second choice."
    assert "my agent name" not in last["description"]


def test_selected_option_index_from_ansi():
    assert tui_state.selected_option_index(_menu_ansi(2)) == 2
    assert tui_state.selected_option_index(_menu_ansi(1)) == 1
    assert tui_state.selected_option_index(_menu_ansi(4)) == 4


def test_selected_option_index_none_when_no_marker():
    assert tui_state.selected_option_index("no menu here") is None


def test_is_custom_text_option():
    assert tui_state.is_custom_text_option("Type something.") is True
    assert tui_state.is_custom_text_option("type SOMETHING") is True
    assert tui_state.is_custom_text_option("A") is False
