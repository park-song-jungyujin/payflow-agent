"""main.py — OIDC 게이트, 라우팅, 청구자(아직 501)/집행자/안전확인 라우트를 검증한다.

파이프라인 테스트: `_run_once`(ADK Runner → 실제 Gemini 호출)만 스텁으로 갈아끼우고,
OIDC 검증 → 요청 파싱 → (스텁이 대신하는) 에이전트 실행 → 응답까지 전체 경로를
실제 LLM 호출 없이 통과시킨다. 스텁 내부에서 before_tool_callback과 실제
safety/executor tool을 그대로 호출해 "에이전트가 툴을 부르면 draft가 실제로 써진다"는
체인까지 코드로 검증한다. executor는 agent_sessions(get_or_create_session·append_turn)
도 스텁으로 갈아끼운다 — 그쪽 로직 자체는 tests/test_memory.py가 이미 덮는다.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from shared.callbacks import make_before_tool_callback
from shared.memory import AgentType, Turn
from safety.tools import submit_risk_report
from executor.tools import submit_settlement_analysis


def test_render_prior_turns_wraps_only_untrusted_turns():
    """tiered-memory-review.html §3·§8 Phase 1 — 과거 턴 중 untrusted=True인 것만
    <untrusted_receipt_text>로 개별 래핑된다."""
    turns = [
        Turn(
            turn_id="t1",
            ts=datetime.now(timezone.utc),
            role="INPUT",
            content="영수증 원문에 심어진 인젝션 시도",
            untrusted=True,
        ),
        Turn(
            turn_id="t2",
            ts=datetime.now(timezone.utc),
            role="OUTPUT",
            content="판정: 정상",
            untrusted=False,
        ),
    ]

    rendered = main._render_prior_turns(turns, empty_message="(없음)")

    assert rendered.count("<untrusted_receipt_text>") == 1
    assert "영수증 원문에 심어진 인젝션 시도" in rendered
    assert "<untrusted_receipt_text>\n판정: 정상" not in rendered


def test_render_prior_turns_empty_uses_provided_message():
    assert main._render_prior_turns([], empty_message="(이전 턴 없음)") == "(이전 턴 없음)"


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


def test_claimant_route_requires_receipt_id(client, monkeypatch):
    """501 스텁이 사라졌다 — 배선 전체는 tests/test_claimant_pipeline.py가 덮는다."""
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})
    resp = client.post(
        "/agents/claimant/review", json={}, headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 400


def test_executor_analyze_missing_fields_rejected(client, monkeypatch):
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})
    resp = client.post(
        "/agents/executor/analyze", json={}, headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 400


def test_executor_analyze_empty_candidate_claims_is_valid_request(client, monkeypatch):
    """candidate_claims가 빈 리스트인 것과 아예 없는 것(None)은 다르다 — 빈 배치도
    유효한 분석 대상이다."""
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})

    class _FakeSession:
        turns = []

    async def fake_run_once(agent, session_id, prompt):
        return ""

    monkeypatch.setattr(main, "get_or_create_session", lambda *a, **kw: _FakeSession())
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)
    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/executor/analyze",
        json={"settlement_run_id": "run_1", "task_id": "task_1", "candidate_claims": []},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200


def test_executor_analyze_passes_org_id_to_session(client, monkeypatch):
    """tiered-memory-review.html §8 Phase 2 — body의 org_id가 get_or_create_session에
    그대로 전달돼야 새 세션이 조직으로 스코핑된다."""
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})

    class _FakeSession:
        turns = []

    org_ids_seen = []

    def fake_get_or_create_session(agent_type, entity_id, actor_ref=None, org_id=""):
        org_ids_seen.append(org_id)
        return _FakeSession()

    async def fake_run_once(agent, session_id, prompt):
        return ""

    monkeypatch.setattr(main, "get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)
    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/executor/analyze",
        json={
            "settlement_run_id": "run_1",
            "task_id": "task_1",
            "candidate_claims": [],
            "org_id": "org_9",
        },
        headers={"Authorization": "Bearer x"},
    )

    assert resp.status_code == 200
    assert org_ids_seen == ["org_9"]


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


def test_executor_analyze_pipeline_without_real_llm(client, monkeypatch):
    """OIDC 통과 → 세션 조회/INPUT 기록 → _run_once(스텁) → before_tool_callback →
    submit_settlement_analysis → write_agent_draft(스텁) → OUTPUT 기록까지, 실제
    Vertex/LLM 호출과 실제 Firestore 없이 체인 전체를 돈다."""
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})

    drafts_written = []
    audit_log = []
    monkeypatch.setattr(
        "executor.tools.write_agent_draft",
        lambda **kw: drafts_written.append(kw) or {"draft_id": f"drf_{kw['task_id']}"},
    )
    monkeypatch.setattr(
        "shared.callbacks.record_tool_call_audit", lambda **kw: audit_log.append(kw)
    )

    class _FakeSession:
        turns = []

    sessions_fetched = []
    turns_appended = []

    def fake_get_or_create_session(agent_type, entity_id, actor_ref=None, org_id=""):
        sessions_fetched.append((agent_type, entity_id))
        return _FakeSession()

    def fake_append_turn(session, **kw):
        turns_appended.append(kw)
        return session

    monkeypatch.setattr(main, "get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(main, "append_turn", fake_append_turn)

    prompts = []
    similar_calls = []
    monkeypatch.setattr(
        main,
        "find_similar_sessions",
        lambda *a, **kw: similar_calls.append((a, kw)) or ["과거 유사 사례: 3턴, 상태 CLOSED"],
    )

    class _FakeToolContext:
        state = {}

    async def fake_run_once(agent, session_id, prompt):
        """실제 LlmAgent/Runner/Gemini 대신, 에이전트가 이상징후를 서술하고
        정해진 프로토콜대로 툴을 한 번 호출했다고 가정한 결과를 재현한다."""
        prompts.append(prompt)
        before_tool_callback = make_before_tool_callback("EXECUTOR")
        gate_result = before_tool_callback(
            tool=type("T", (), {"name": "submit_settlement_analysis"})(),
            args={},
            tool_context=_FakeToolContext(),
        )
        assert gate_result is None  # 게이트 통과
        submit_settlement_analysis(
            settlement_run_id="run_1",
            task_id=session_id,
            anomalies=["중복 의심 1건"],
            summary_text="중복 의심 1건, 나머지는 이상 없음",
            anomalies_en=["1 suspected duplicate"],
            summary_text_en="1 suspected duplicate, no issues otherwise",
        )
        return "중복 의심 1건, 나머지는 이상 없음"

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/executor/analyze",
        json={
            "settlement_run_id": "run_1",
            "task_id": "task_1",
            "candidate_claims": [{"claim_id": "clm_1"}, {"claim_id": "clm_2"}],
            "duplicate_groups": [{"claim_ids": ["clm_1", "clm_2"]}],
            "exact_duplicate_groups": [
                {"claim_ids": ["clm_1", "clm_2"], "receipt_serial_number": "A1234"}
            ],
        },
        headers={"Authorization": "Bearer x"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert sessions_fetched == [(AgentType.EXECUTOR, "run_1")]
    assert "과거 유사 사례: 3턴, 상태 CLOSED" in prompts[0]
    assert "참고용" in prompts[0]
    assert "org_id: ''" in prompts[0]
    # exact_duplicate_groups가 비신뢰 블록(→ 프롬프트)에 실제로 실렸는지 —
    # body를 조용히 무시하는 경로가 생기면 이 단언이 잡는다.
    assert "A1234" in turns_appended[0]["content"]
    assert drafts_written == [
        {
            "agent": "EXECUTOR",
            "target_type": "SETTLEMENT_RUN",
            "target_id": "run_1",
            "task_id": "task_1",
            "payload": {
                "anomalies": ["중복 의심 1건"],
                "summary_text": "중복 의심 1건, 나머지는 이상 없음",
                "anomalies_en": ["1 suspected duplicate"],
                "summary_text_en": "1 suspected duplicate, no issues otherwise",
            },
        }
    ]
    assert audit_log == [
        {
            "agent": "EXECUTOR",
            "action": "TOOL_CALL_STARTED",
            "reason": "tool=submit_settlement_analysis",
        }
    ]
    # INPUT 턴에는 비신뢰 표시가, OUTPUT 턴에는 없어야 한다.
    assert [t["role"] for t in turns_appended] == ["INPUT", "OUTPUT"]
    assert turns_appended[0]["untrusted"] is True
    assert turns_appended[0]["doc_refs"] == ["clm_1", "clm_2"]
    assert turns_appended[1]["untrusted"] is False
    assert turns_appended[1]["content"] == "중복 의심 1건, 나머지는 이상 없음"
