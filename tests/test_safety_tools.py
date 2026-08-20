"""agent-tools.md — safety/tools.py submit_risk_report 단위 테스트.
CLAUDE.md: 이 에이전트는 게이트가 아니라 조언자다 — 리포트가 비어있으면 거부만
하고, 그 이상의 판단(승인 차단 등)은 하지 않는다는 것을 반환값으로 확인한다."""

from safety import tools


def test_empty_risk_report_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_risk_report(settlement_run_id="run_1", task_id="task_1", risk_report="")

    assert result == {"status": "error", "detail": "risk_report must not be empty"}
    assert calls == []


def test_whitespace_only_risk_report_rejected(monkeypatch):
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: {})
    result = tools.submit_risk_report(settlement_run_id="run_1", task_id="task_1", risk_report="   \n")
    assert result["status"] == "error"


def test_valid_risk_report_writes_draft_with_expected_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_risk_report(
        settlement_run_id="run_1", task_id="task_1", risk_report="한도 근접 항목 1건"
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls == [
        {
            "agent": "SAFETY",
            "target_type": "SETTLEMENT_RUN",
            "target_id": "run_1",
            "task_id": "task_1",
            "payload": {"risk_report": "한도 근접 항목 1건"},
        }
    ]
