"""Tests for memory_tools.py — ADK tool signatures and tool_context.state binding."""

from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from shared import memory, memory_tools


class _FakeToolContext:
    def __init__(self, state=None):
        self.state = state or {}


def test_fetch_full_session_history_extracts_org_id_from_tool_context(monkeypatch):
    called = {}

    def fake_fetch(session_id, org_id):
        called["args"] = (session_id, org_id)
        return None

    monkeypatch.setattr(memory, "fetch_full_session", fake_fetch)
    monkeypatch.setattr(memory_tools, "fetch_full_session", fake_fetch)

    ctx = _FakeToolContext(state={"org_id": "org_1"})
    result = memory_tools.fetch_full_session_history("CLAIMANT__x", tool_context=ctx)

    assert called["args"] == ("CLAIMANT__x", "org_1")
    assert result == {"status": "error", "detail": "session not found"}


def test_fetch_full_session_history_falls_back_to_empty_org_id_without_tool_context(monkeypatch):
    called = {}

    def fake_fetch(session_id, org_id):
        called["args"] = (session_id, org_id)
        return None

    monkeypatch.setattr(memory, "fetch_full_session", fake_fetch)
    monkeypatch.setattr(memory_tools, "fetch_full_session", fake_fetch)

    result = memory_tools.fetch_full_session_history("CLAIMANT__x")

    assert called["args"] == ("CLAIMANT__x", "")
    assert result == {"status": "error", "detail": "session not found"}


def test_fetch_full_session_history_tool_declaration_omits_tool_context():
    """ADK FunctionTool should automatically omit tool_context from LLM parameter declaration."""
    tool = FunctionTool(memory_tools.fetch_full_session_history)
    declaration = tool._get_declaration()

    assert declaration.name == "fetch_full_session_history"
    # LLM should only see session_id, not tool_context or org_id
    properties = (
        declaration.parameters_json_schema.get("properties", {})
        if declaration.parameters_json_schema
        else declaration.parameters.properties
    )
    assert "session_id" in properties
    assert "tool_context" not in properties
    assert "org_id" not in properties



