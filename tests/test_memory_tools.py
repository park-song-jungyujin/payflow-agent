"""Tests for memory_tools.py — ADK tool signatures."""

from shared import memory, memory_tools


def test_fetch_full_session_history_requires_org_id(monkeypatch):
    called = {}

    def fake_fetch(session_id, org_id):
        called["args"] = (session_id, org_id)
        return None

    monkeypatch.setattr(memory, "fetch_full_session", fake_fetch)
    monkeypatch.setattr(memory_tools, "fetch_full_session", fake_fetch)

    result = memory_tools.fetch_full_session_history("CLAIMANT__x", org_id="org_1")

    assert called["args"] == ("CLAIMANT__x", "org_1")
    assert result == {"status": "error", "detail": "session not found"}
