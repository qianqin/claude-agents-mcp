from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

import claude_agents_mcp.server as srv
from claude_agents_mcp.spawner import SpawnResult


class FakeController:
    def __init__(
        self,
        *,
        send=True,
        open_=True,
        abort=True,
        agents=None,
        chat=None,
        is_menu=False,
        select=True,
        custom=True,
    ):
        self.calls = []
        self._send = send
        self._open = open_
        self._abort = abort
        self._agents = agents or []
        self._chat = chat
        self._is_menu = is_menu
        self._select = select
        self._custom = custom

    def list_agents(self):
        return self._agents

    def read_agent(self, title):
        self.calls.append(("read", title))
        return self._chat

    def send_message(self, title, message):
        self.calls.append(("send", title, message))
        return self._send

    def open_agent(self, title):
        self.calls.append(("open", title))
        return self._open

    def abort_agent(self, title):
        self.calls.append(("abort", title))
        return self._abort

    def return_to_overview(self):
        self.calls.append(("overview",))

    def chat_is_menu(self, title):
        self.calls.append(("is_menu", title))
        return self._is_menu

    def select_option(self, title, option):
        self.calls.append(("select", title, option))
        return self._select

    def answer_custom(self, title, text):
        self.calls.append(("custom", title, text))
        return self._custom


@pytest.fixture(autouse=True)
def reset_controller(monkeypatch):
    monkeypatch.setattr(srv, "_controller", None)
    yield


def _install(monkeypatch, fake):
    monkeypatch.setattr(srv, "_controller", fake)
    return fake


def test_spawn_agent_returns_confirmation_only(monkeypatch):
    def fake_spawn(req, controller):
        return SpawnResult(status="running", cwd=req.cwd)

    monkeypatch.setattr(srv, "spawn_impl", fake_spawn)
    out = srv.spawn_agent(prompt="do it")
    assert out["spawned"] is True
    assert out["status"] == "running"
    assert "list_agents" in out["note"]
    assert "title" not in out
    assert "session_id" not in out
    assert "description" not in out


def test_list_agents_filters(monkeypatch):
    agents = [
        {"title": "a", "status": "running"},
        {"title": "b", "status": "done"},
    ]
    _install(monkeypatch, FakeController(agents=agents))
    assert len(srv.list_agents()) == 2
    only = srv.list_agents(status_filter="done")
    assert [a["title"] for a in only] == ["b"]


def test_get_agent_output(monkeypatch):
    chat = {"agent": "my agent", "events": [{"type": "user", "text": "hi"}, {"type": "assistant", "text": "yo"}]}
    _install(monkeypatch, FakeController(chat=chat))
    out = srv.get_agent_output("my agent")
    assert out["agent"] == "my agent"
    assert [e["type"] for e in out["events"]] == ["user", "assistant"]


def test_get_agent_output_not_found(monkeypatch):
    _install(monkeypatch, FakeController(chat=None))
    with pytest.raises(ToolError) as exc:
        srv.get_agent_output("ghost")
    assert "AGENT_NOT_FOUND" in str(exc.value)


def test_send_to_agent(monkeypatch):
    fake = _install(monkeypatch, FakeController(send=True))
    out = srv.send_to_agent("target", "hello there")
    assert out["sent"] is True
    assert ("send", "target", "hello there") in fake.calls


def test_send_to_agent_failure_raises(monkeypatch):
    _install(monkeypatch, FakeController(send=False))
    with pytest.raises(ToolError) as exc:
        srv.send_to_agent("target", "hi")
    assert "SEND_FAILED" in str(exc.value)


def test_reply_delegates_to_send(monkeypatch):
    fake = _install(monkeypatch, FakeController(send=True))
    out = srv.reply_to_agent("target", "the answer")
    assert out["sent"] is True
    assert ("send", "target", "the answer") in fake.calls


def test_abort_agent(monkeypatch):
    fake = _install(monkeypatch, FakeController(abort=True))
    out = srv.abort_agent("target")
    assert out["aborted"] is True
    assert ("abort", "target") in fake.calls


def test_abort_agent_not_found(monkeypatch):
    _install(monkeypatch, FakeController(abort=False))
    with pytest.raises(ToolError) as exc:
        srv.abort_agent("ghost")
    assert "ABORT_FAILED" in str(exc.value)


def test_select_option_success(monkeypatch):
    fake = _install(monkeypatch, FakeController(is_menu=True, select=True))
    out = srv.select_option("target", "3")
    assert out == {"agent": "target", "selected": True, "option": "3"}
    assert ("select", "target", "3") in fake.calls


def test_select_option_not_a_menu_raises(monkeypatch):
    _install(monkeypatch, FakeController(is_menu=False))
    with pytest.raises(ToolError) as exc:
        srv.select_option("target", "1")
    assert "NOT_A_MENU" in str(exc.value)


def test_select_option_failure_raises(monkeypatch):
    _install(monkeypatch, FakeController(is_menu=True, select=False))
    with pytest.raises(ToolError) as exc:
        srv.select_option("target", "9")
    assert "SELECT_FAILED" in str(exc.value)


def test_reply_to_menu_routes_custom_text(monkeypatch):
    fake = _install(monkeypatch, FakeController(is_menu=True, custom=True))
    out = srv.reply_to_agent("target", "my custom answer")
    assert out["sent"] is True
    assert ("custom", "target", "my custom answer") in fake.calls
    assert not any(c[0] == "send" for c in fake.calls)


def test_reply_to_non_menu_uses_send(monkeypatch):
    fake = _install(monkeypatch, FakeController(is_menu=False, send=True))
    out = srv.reply_to_agent("target", "the answer")
    assert out["sent"] is True
    assert ("send", "target", "the answer") in fake.calls
    assert not any(c[0] == "custom" for c in fake.calls)
