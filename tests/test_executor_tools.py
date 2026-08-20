"""agent-tools.md — executor/tools.py submit_settlement_analysis 단위 테스트."""

from executor import tools


def test_empty_analysis_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1", task_id="task_1", analysis="   "
    )

    assert result == {"status": "error", "detail": "analysis must not be empty"}
    assert calls == []


def test_valid_analysis_writes_draft_with_expected_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1", task_id="task_1", analysis="매칭 실패 2건, 통화 혼재 있음"
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls == [
        {
            "agent": "EXECUTOR",
            "target_type": "SETTLEMENT_RUN",
            "target_id": "run_1",
            "task_id": "task_1",
            "payload": {"analysis": "매칭 실패 2건, 통화 혼재 있음"},
        }
    ]
