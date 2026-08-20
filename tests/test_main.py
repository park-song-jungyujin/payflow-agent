"""main.py — OIDC 게이트, 라우팅, 아직 없는 청구자/집행자 라우트 응답을 검증한다.

파이프라인 테스트: `_run_once`(ADK Runner → 실제 Gemini 호출)만 스텁으로 갈아끼우고,
OIDC 검증 → 요청 파싱 → (스텁이 대신하는) 에이전트 실행 → 응답까지 전체 경로를
실제 LLM 호출 없이 통과시킨다. 스텁 내부에서 before_tool_callback과 실제 safety
tool(submit_risk_report)을 그대로 호출해 "에이전트가 툴을 부르면 draft가 실제로
써진다"는 체인까지 코드로 검증한다.
"""

import pytest
from fastapi.testclient import TestClient

import main
from shared.callbacks import make_before_tool_callback
from safety.tools import submit_risk_report


@pytest.fixture
def client():
    return TestClient(main.app)


def test_health():
    client = TestClient(main.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_bearer_token_rejected(client):
    resp = client.post("/agents/safety/report", json={})
    assert resp.status_code == 401


def test_invalid_bearer_token_rejected(client, monkeypatch):
    def boom(token, request, audience):
        raise ValueError("invalid token")

    monkeypatch.setattr(main.id_token, "verify_oauth2_token", boom)
    resp = client.post(
        "/agents/safety/report",
        json={"settlement_run_id": "run_1", "task_id": "task_1"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_claimant_route_is_not_implemented(client, monkeypatch):
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})
    resp = client.post(
        "/agents/claimant/review", json={}, headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 501


def test_executor_route_is_not_implemented(client, monkeypatch):
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})
    resp = client.post(
        "/agents/executor/analyze", json={}, headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 501


def test_safety_report_missing_fields_rejected(client, monkeypatch):
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})
    resp = client.post(
        "/agents/safety/report", json={"task_id": "task_1"}, headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 400


def test_safety_report_pipeline_without_real_llm(client, monkeypatch):
    """OIDC 통과 → _run_once(스텁) → before_tool_callback → submit_risk_report →
    write_agent_draft(스텁)까지, 실제 Vertex/LLM 호출 없이 체인 전체를 돈다."""
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})

    drafts_written = []
    audit_log = []
    monkeypatch.setattr(
        "safety.tools.write_agent_draft",
        lambda **kw: drafts_written.append(kw) or {"draft_id": f"drf_{kw['task_id']}"},
    )
    monkeypatch.setattr(
        "shared.callbacks.record_tool_call_audit", lambda **kw: audit_log.append(kw)
    )

    class _FakeToolContext:
        state = {}

    async def fake_run_once(agent, session_id, prompt):
        """실제 LlmAgent/Runner/Gemini 대신, 에이전트가 리스크 리포트를 작성하고
        정해진 프로토콜대로 툴을 한 번 호출했다고 가정한 결과를 재현한다."""
        before_tool_callback = make_before_tool_callback("SAFETY")
        gate_result = before_tool_callback(
            tool=type("T", (), {"name": "submit_risk_report"})(),
            args={},
            tool_context=_FakeToolContext(),
        )
        assert gate_result is None  # 게이트 통과
        submit_risk_report(
            settlement_run_id="run_1", task_id=session_id, risk_report="한도 근접 항목 없음"
        )

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/safety/report",
        json={
            "settlement_run_id": "run_1",
            "task_id": "task_1",
            "settlement_run_snapshot": {"total_amount_minor": 1000},
        },
        headers={"Authorization": "Bearer x"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert drafts_written == [
        {
            "agent": "SAFETY",
            "target_type": "SETTLEMENT_RUN",
            "target_id": "run_1",
            "task_id": "task_1",
            "payload": {"risk_report": "한도 근접 항목 없음"},
        }
    ]
    assert audit_log == [
        {"agent": "SAFETY", "action": "TOOL_CALL_STARTED", "reason": "tool=submit_risk_report"}
    ]
