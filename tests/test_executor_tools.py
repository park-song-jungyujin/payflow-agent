"""agent-tools.md — executor/tools.py submit_settlement_analysis 단위 테스트."""

from executor import tools


def test_empty_summary_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1", task_id="task_1", anomalies=[], summary_text="   "
    )

    assert result == {"status": "error", "detail": "summary_text must not be empty"}
    assert calls == []


def test_empty_anomalies_list_is_valid(monkeypatch):
    """이상징후가 없는 것도 정상 결과다 — anomalies가 빈 리스트여도 거부하지 않는다."""
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1", task_id="task_1", anomalies=[], summary_text="이상 없음"
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls[0]["payload"] == {"anomalies": [], "summary_text": "이상 없음"}


def test_valid_analysis_writes_draft_with_expected_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=["같은 가맹점 · 같은 금액 · 3분 간격 청구 2건"],
        summary_text="중복 의심 1건, 나머지는 이상 없음",
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls == [
        {
            "agent": "EXECUTOR",
            "target_type": "SETTLEMENT_RUN",
            "target_id": "run_1",
            "task_id": "task_1",
            "payload": {
                "anomalies": ["같은 가맹점 · 같은 금액 · 3분 간격 청구 2건"],
                "summary_text": "중복 의심 1건, 나머지는 이상 없음",
            },
        }
    ]
