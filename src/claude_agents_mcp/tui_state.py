"""Parse `tmux capture-pane` output of the `claude agents` TUI.

The TUI has two main views plus a couple of transient states:

- OVERVIEW: header logo + "N awaiting input · N working · N completed" counts,
  collapsible "Working"/"Completed" sections, and a new-session input box at the
  bottom with the placeholder "describe a task for a new session".
- CHAT: an individual agent's conversation. The rule line directly above the
  input box carries the agent name (right-aligned), e.g. "──── my agent ──".
- TRUST: the "Do you trust the files in this folder?" dialog on first launch.
- SHELL: `claude agents` has exited and the pane shows a bash prompt.
- UNKNOWN: still rendering / can't tell yet — caller should re-capture.

Selection in OVERVIEW is rendered as a background color only. It is invisible in
`capture-pane -p` but visible in `capture-pane -e` as SGR code `48;5;255`. Parse
the highlighted row from the escape-coded capture, never by counting keystrokes.
"""

from __future__ import annotations

import re
from enum import Enum

# Placeholder shown in the new-session input box when it is empty.
NEW_SESSION_PLACEHOLDER = "describe a task for a new session"

# SGR background code tmux emits for the selected overview row.
_SELECTED_BG = "48;5;255"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# A line that is "just a rule": box-drawing dashes and spaces only.
_RULE_CHARS = set("─—-= \t")
# Leading status glyphs that prefix an agent row.
_GLYPHS = set("✻✽✢✺✶✷✸✹∙·●○◌*")
_COUNTS_RE = re.compile(
    r"(\d+)\s+awaiting input\s*·\s*(\d+)\s+working\s*·\s*(\d+)\s+completed"
)


class View(str, Enum):
    OVERVIEW = "overview"
    CHAT = "chat"
    TRUST = "trust"
    SHELL = "shell"
    UNKNOWN = "unknown"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _input_line_index(plain_lines: list[str]) -> int | None:
    """Index of the input box line (the LAST line whose first non-space char is
    '❯'). Chat history also uses '❯' for user turns, so the last one wins."""
    found = None
    for i, line in enumerate(plain_lines):
        if line.lstrip().startswith("❯"):
            found = i
    return found


def _is_rule(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return all(ch in _RULE_CHARS for ch in stripped)


# Minimum run of box-drawing dashes for a line to count as a separator rule.
_RULE_MIN_DASHES = 8


def _is_rule_like(line: str) -> bool:
    """A separator rule, possibly with an embedded label (the chat agent name).

    The overview's input box has a pure-dash rule above it; the chat view's has
    a rule with the agent name embedded. Both have a long dash run."""
    return (line.count("─") + line.count("—")) >= _RULE_MIN_DASHES


def _rule_label(line: str) -> str:
    """Text embedded in a rule line (the chat agent name), or '' if pure rule."""
    # Drop box-drawing dashes/spaces from both ends and middle runs.
    label = line
    for ch in "─—":
        label = label.replace(ch, " ")
    return label.strip()


def classify(plain: str) -> View:
    """Classify the current view from a plain (`-p`) capture."""
    if _looks_like_trust(plain):
        return View.TRUST

    lines = _lines(plain)
    idx = _input_line_index(lines)

    if idx is not None and idx > 0:
        above = lines[idx - 1]
        if _is_rule_like(above):
            label = _rule_label(above)
            if label:
                return View.CHAT
            return View.OVERVIEW

    # No clean input box. Fall back to chrome heuristics.
    if _COUNTS_RE.search(plain) or NEW_SESSION_PLACEHOLDER in plain:
        return View.OVERVIEW
    if "← for agents" in plain or "bypass permissions on" in plain:
        return View.CHAT
    if "? for shortcuts" in plain:
        return View.OVERVIEW

    # Nothing TUI-ish: probably dropped to a shell.
    if _looks_like_shell(plain):
        return View.SHELL
    return View.UNKNOWN


def _looks_like_trust(plain: str) -> bool:
    low = plain.lower()
    return "do you trust" in low or "trust the files" in low


def _looks_like_shell(plain: str) -> bool:
    nonblank = [ln for ln in _lines(plain) if ln.strip()]
    if not nonblank:
        return False
    # A bash prompt line ending in $ or # with no TUI box/rule chrome around.
    tail = nonblank[-1].rstrip()
    return tail.endswith("$") or tail.endswith("#") or "claude agents" in tail


def chat_agent_name(plain: str) -> str | None:
    """Agent name from the rule above the input box, when in CHAT view."""
    lines = _lines(plain)
    idx = _input_line_index(lines)
    if idx is None or idx == 0:
        return None
    above = lines[idx - 1]
    if not _is_rule_like(above):
        return None
    label = _rule_label(above)
    return label or None


def input_text(plain: str) -> str:
    """Current text in the input box. Placeholder counts as empty string."""
    lines = _lines(plain)
    idx = _input_line_index(lines)
    if idx is None:
        return ""
    line = lines[idx].lstrip()
    # Drop the leading '❯' and following space.
    body = line[1:].lstrip(" ")
    if body.strip() == NEW_SESSION_PLACEHOLDER:
        return ""
    return body.rstrip()


def input_is_empty(plain: str) -> bool:
    return input_text(plain) == ""


def counts(plain: str) -> dict[str, int] | None:
    m = _COUNTS_RE.search(plain)
    if not m:
        return None
    return {
        "awaiting_input": int(m.group(1)),
        "working": int(m.group(2)),
        "completed": int(m.group(3)),
    }


def _agent_name_from_row(plain_row: str) -> str:
    """Extract the agent (or section) name from an overview row.

    Rows look like: " ✻ my agent name             description text   4h".
    The name runs from after the status glyph up to the first run of 2+ spaces.
    Section headers are plain words like "Working" / "Completed".
    """
    s = plain_row.strip()
    # Strip a single leading glyph + space.
    if s and s[0] in _GLYPHS:
        s = s[1:].lstrip()
    # Name is everything up to the first 2+ space gap.
    name = re.split(r"\s{2,}", s, maxsplit=1)[0]
    return name.strip()


def highlighted_row(ansi: str) -> str | None:
    """The plain text of the selected (background-highlighted) overview row.

    Reads an escape-coded (`-e`) capture, finds the line bearing the selection
    background SGR, and returns it with ANSI stripped. None if nothing selected.
    """
    for line in _lines(ansi):
        if _SELECTED_BG in line:
            return strip_ansi(line).rstrip()
    return None


def selected_name(ansi: str) -> str | None:
    """Name of the highlighted overview row (agent name or section header)."""
    row = highlighted_row(ansi)
    if row is None:
        return None
    return _agent_name_from_row(row)


_SECTION_HEADERS = ("Awaiting input", "Working", "Completed")
_SECTION_STATUS = {
    "Awaiting input": "awaiting_input",
    "Working": "running",
    "Completed": "done",
}
_AGE_RE = re.compile(r"^\d+[smhdw]$|^now$")


def _is_section_header(line: str) -> str | None:
    """If the line is a section header, return its canonical name, else None."""
    s = strip_ansi(line).strip()
    for h in _SECTION_HEADERS:
        if s == h or s.startswith(h + " "):
            return h
    return None


def _is_agent_row(line: str) -> bool:
    s = strip_ansi(line).lstrip()
    return bool(s) and s[0] in _GLYPHS


def _parse_agent_row(line: str) -> dict:
    s = strip_ansi(line).strip()
    if s and s[0] in _GLYPHS:
        s = s[1:].lstrip()
    parts = [p for p in re.split(r"\s{2,}", s) if p]
    title = parts[0] if parts else ""
    age = None
    if len(parts) >= 2 and _AGE_RE.match(parts[-1]):
        age = parts[-1]
        desc = "  ".join(parts[1:-1])
    else:
        desc = "  ".join(parts[1:])
    return {"title": title.strip(), "description": desc.strip(), "age": age}


def parse_overview_rows(plain: str) -> list[dict]:
    """Parse the overview's Working/Completed/Awaiting sections into rows.

    Each row: {title, description, age, section, status}. `title` is the
    actionable identity (what the TUI shows and selection/navigation match on).
    """
    rows: list[dict] = []
    section: str | None = None
    in_idx = _input_line_index(_lines(plain))
    for i, line in enumerate(_lines(plain)):
        if in_idx is not None and i >= in_idx - 1:
            break  # stop at the input box rule / box
        header = _is_section_header(line)
        if header:
            section = header
            continue
        if section and _is_agent_row(line):
            row = _parse_agent_row(line)
            if row["title"]:
                row["section"] = section
                row["status"] = _SECTION_STATUS.get(section, "unknown")
                rows.append(row)
    return rows


def overview_titles(plain: str) -> list[str]:
    return [r["title"] for r in parse_overview_rows(plain)]


_LOGO_CHARS = set("▐▛███▜▌▝▘")
_SPINNER_RE = re.compile(r"^[✻✽✢✺✶✷✸✹∙·●]\s+(Churned|Baked|Working|Thinking|Cooking)")


def _is_chat_noise(stripped: str) -> bool:
    """Banner/logo and transient spinner lines that aren't conversation."""
    if "Claude Code v" in stripped:
        return True
    if sum(1 for ch in stripped if ch in _LOGO_CHARS) >= 3:
        return True
    if _SPINNER_RE.match(stripped):
        return True
    return False


def parse_chat(plain: str) -> dict:
    """Parse the visible chat transcript into ordered events.

    Events: {type: user|assistant|recap, text}. Only the visible viewport is
    captured (the TUI is a full-screen app, so earlier history is off-screen).
    """
    lines = _lines(plain)
    in_idx = _input_line_index(lines)
    end = in_idx - 1 if in_idx is not None else len(lines)

    events: list[dict] = []
    current: dict | None = None

    def flush():
        nonlocal current
        if current is not None:
            current["text"] = current["text"].rstrip()
            if current["text"]:
                events.append(current)
        current = None

    for line in lines[:end]:
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            if current is not None:
                current["text"] += "\n"
            continue
        if _is_chat_noise(stripped):
            continue
        if stripped.startswith("❯"):
            flush()
            current = {"type": "user", "text": stripped[1:].strip()}
        elif stripped.startswith("●"):
            flush()
            current = {"type": "assistant", "text": stripped[1:].strip()}
        elif stripped.startswith("※"):
            flush()
            current = {"type": "recap", "text": stripped[1:].strip()}
        else:
            # Continuation (tool output ⎿, wrapped text, etc.).
            if current is None:
                current = {"type": "assistant", "text": ""}
            current["text"] += ("\n" if current["text"] else "") + stripped
    flush()
    return {"agent": chat_agent_name(plain), "events": events}


# --- choice menu (AskUserQuestion rendered as an arrow-key selection) --------
#
# When a background agent asks a question, its chat shows a selection MENU
# rather than the free-text input box. The reliable signature is the footer
# carrying both "↑/↓ to navigate" and "Enter to select". The selected option
# row is marked by a leading `❯` glyph (and purple fg SGR 38;5;105m); the
# in-chat menu does NOT use a background SGR, so detection keys off the `❯`
# marker, which always accompanies the highlighted row.

_MENU_NAV = "↑/↓ to navigate"
_MENU_SELECT = "Enter to select"
# A numbered option row: "N. label" (the leading `❯`/spaces are stripped first).
_OPTION_RE = re.compile(r"^(\d+)\.\s+(.*\S)\s*$")


def is_choice_menu(plain: str) -> bool:
    """True when the chat is a selection menu (per the footer signature)."""
    return _MENU_NAV in plain and _MENU_SELECT in plain


def parse_menu_options(plain: str) -> list[dict]:
    """Ordered options for a choice menu.

    Each: `{index, label, description}`. `label` is the option-row text (e.g.
    "A", "Type something."); `description` is the indented detail line(s) below
    it (e.g. "First choice."), joined with spaces — useful for label-substring
    matching. `description` is "" when the option has none.
    """
    options: list[dict] = []
    for line in _lines(plain):
        s = strip_ansi(line).strip()
        if s.startswith("❯"):
            s = s[1:].lstrip()
        m = _OPTION_RE.match(s)
        if m:
            options.append(
                {"index": int(m.group(1)), "label": m.group(2), "description": ""}
            )
            continue
        # A non-option, non-empty, non-rule line right after an option is that
        # option's description detail. _is_rule_like (not _is_rule) so the chat
        # header rule, which embeds the agent name, is excluded — otherwise that
        # name would leak into the last option's description and skew matching.
        if options and s and not _is_rule_like(line) and _MENU_NAV not in s:
            prev = options[-1]
            prev["description"] = (prev["description"] + " " + s).strip()
    return options


def selected_option_index(ansi: str) -> int | None:
    """Index of the menu row marked by `❯` / fg 38;5;105m (from an `-e` capture)."""
    for line in _lines(ansi):
        if "❯" not in line:
            continue
        s = strip_ansi(line).strip()
        if s.startswith("❯"):
            s = s[1:].lstrip()
        m = _OPTION_RE.match(s)
        if m:
            return int(m.group(1))
    return None


def is_custom_text_option(label: str) -> bool:
    """True for the auto-appended free-text escape hatch ("Type something")."""
    return label.strip().lower().startswith("type something")


def name_matches(target: str, visible: str) -> bool:
    """True if a (possibly truncated, ellipsis-suffixed) visible overview name
    refers to the target agent name from `claude agents --json`."""
    if not visible:
        return False
    t = target.strip()
    v = visible.strip()
    if t == v:
        return True
    # Visible names are truncated with a trailing ellipsis ('…' or '...').
    core = v.rstrip("…").rstrip(".").rstrip()
    if core and t.startswith(core):
        return True
    # Defensive: target itself truncated in some display.
    tcore = t.rstrip("…").rstrip(".").rstrip()
    return bool(tcore) and v.startswith(tcore)
