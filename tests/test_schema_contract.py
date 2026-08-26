"""docs/rules/schema-contract.md — agent가 pyproject.toml에 pin한 payflow-backend
패키지의 스키마와 agent 코드가 실제로 만드는 값이 어긋나지 않는지 검증한다.

과거 이력: 스키마 v0.3.0 발행 후 agent의 pin이 늦게 따라가 로컬-원격이 어긋난 적이
있다(journal 2026-08-18). pin은 uv.lock이 고정하지만, "고정된 버전과 agent 코드가
실제로 호환되는가"는 아무 것도 검증하지 않았다 — 이 테스트가 그 공백을 채운다.
"""

from schemas.enums import AgentDraftTargetType, AgentName
from schemas.models import AgentDraft

from claimant.tools import submit_receipt_review
from executor.tools import submit_settlement_analysis
from safety.tools import submit_risk_report


def test_agent_name_enum_covers_all_three_agents():
    """agent-tools.md — claimant/executor/safety 셋. before_tool_callback에 하드코딩된
    이름이 백엔드 AgentName enum과 정확히 일치해야 /agents/drafts가 400을 안 낸다."""
    assert {m.value for m in AgentName} == {"CLAIMANT", "EXECUTOR", "SAFETY"}


def test_target_type_enum_covers_receipt_and_settlement_run():
    assert {m.value for m in AgentDraftTargetType} == {"RECEIPT", "SETTLEMENT_RUN"}


def _drafted_payload(monkeypatch, module, submit_fn, **kwargs):
    captured = {}
    monkeypatch.setattr(module, "write_agent_draft", lambda **kw: captured.update(kw) or {"draft_id": "x"})
    submit_fn(**kwargs)
    return captured


def test_claimant_draft_call_validates_against_agent_draft_schema(monkeypatch):
    import claimant.tools as mod

    call = _drafted_payload(
        monkeypatch,
        mod,
        submit_receipt_review,
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=False,
        is_business=True,
        requery_message="",
        reason="금액·날짜 모두 읽혔다",
    )
    # §9 payload 계약 — 백엔드 parse_claimant_payload(api/src/ingest/drafts.py)가
    # needs_requery를 bool로 요구한다. enum만 대조하던 이 스위트가 스캐폴딩의
    # {classification, requery_message} 불일치를 못 잡았다. 여기서 막는다.
    assert set(call["payload"]) == {
        "needs_requery",
        "is_business",
        "requery_message",
        "reason",
    }
    assert isinstance(call["payload"]["needs_requery"], bool)
    assert isinstance(call["payload"]["is_business"], bool)
    AgentDraft.model_validate(
        {
            "draft_id": f"drf_{call['task_id']}",
            "agent": call["agent"],
            "target_type": call["target_type"],
            "target_id": call["target_id"],
            "task_id": call["task_id"],
            "payload": call["payload"],
            "created_at": "2026-08-20T00:00:00Z",
        }
    )


def test_executor_draft_call_validates_against_agent_draft_schema(monkeypatch):
    import executor.tools as mod

    class _FakeToolContext:
        state = {}

    call = _drafted_payload(
        monkeypatch,
        mod,
        submit_settlement_analysis,
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=[],
        anomalies_en=[],
        summary_text="매칭 실패 없음",
        summary_text_en="No matching failures",
        tool_context=_FakeToolContext(),
    )
    AgentDraft.model_validate(
        {
            "draft_id": f"drf_{call['task_id']}",
            "agent": call["agent"],
            "target_type": call["target_type"],
            "target_id": call["target_id"],
            "task_id": call["task_id"],
            "payload": call["payload"],
            "created_at": "2026-08-20T00:00:00Z",
        }
    )


def test_safety_draft_call_validates_against_agent_draft_schema(monkeypatch):
    import safety.tools as mod

    call = _drafted_payload(
        monkeypatch,
        mod,
        submit_risk_report,
        settlement_run_id="run_1",
        task_id="task_1",
        risk_report="이상 없음",
    )
    AgentDraft.model_validate(
        {
            "draft_id": f"drf_{call['task_id']}",
            "agent": call["agent"],
            "target_type": call["target_type"],
            "target_id": call["target_id"],
            "task_id": call["task_id"],
            "payload": call["payload"],
            "created_at": "2026-08-20T00:00:00Z",
        }
    )
