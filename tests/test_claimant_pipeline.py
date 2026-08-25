"""schema-contract.md §9 — /agents/claimant/review 배선.

test_main.py의 executor 파이프라인 테스트와 같은 형태다: `_run_once`(ADK Runner →
실제 Gemini)만 스텁으로 갈아끼우고 OIDC → 세션 → INPUT 턴 → before_tool_callback →
submit_receipt_review → write_agent_draft → OUTPUT 턴까지 실제 LLM·Firestore·GCS
없이 체인 전체를 돈다.
"""

import pytest
from fastapi.testclient import TestClient

import main
from claimant.tools import submit_receipt_review
from shared.callbacks import make_before_tool_callback
from shared.memory import AgentType


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def oidc_ok(monkeypatch):
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", lambda *a, **kw: {})


_BODY = {
    "receipt_id": "rct_1",
    "task_id": "CLAIMANT:rct_1",
    "org_id": "org_1",
    "recipient_id": "rcp_1",
    "merchant_name": "스타벅스 강남점",
    "transaction_date": "2026-08-05",
    "parsed_amount_minor": None,
    "currency": "KRW",
    "account_category_code": "UNCLASSIFIED",
    "parse_confidence": 0.41,
    "raw_text_gcs_uri": "gs://payflow-receipts/raw_text/rct_1.txt",
}


class _FakeSession:
    def __init__(self, turns=None):
        self.turns = turns or []
        self.session_id = "CLAIMANT__rct_1"
        self.agent_type = AgentType.CLAIMANT
        self.org_id = "org_1"
        self.status = "ACTIVE"
        self.case_features = {}
        self.summary = None


@pytest.fixture(autouse=True)
def _stub_close_session_by_default(monkeypatch):
    monkeypatch.setattr(main, "close_session", lambda session: session)


def test_missing_fields_rejected(client, oidc_ok):
    resp = client.post(
        "/agents/claimant/review", json={}, headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 400



def test_claimant_pipeline_without_real_llm(client, oidc_ok, monkeypatch):
    drafts_written = []
    audit_log = []
    monkeypatch.setattr(
        "claimant.tools.write_agent_draft",
        lambda **kw: drafts_written.append(kw) or {"draft_id": f"drf_{kw['task_id']}"},
    )
    monkeypatch.setattr(
        "shared.callbacks.record_tool_call_audit", lambda **kw: audit_log.append(kw)
    )
    monkeypatch.setattr(main, "fetch_raw_text", lambda uri, **kw: "합계 45,000원")

    sessions_fetched = []
    turns_appended = []
    closed_sessions = []
    monkeypatch.setattr(
        main,
        "get_or_create_session",
        lambda agent_type, entity_id, actor_ref=None, org_id="": sessions_fetched.append(
            (agent_type, entity_id, actor_ref, org_id)
        )
        or _FakeSession(),
    )
    monkeypatch.setattr(
        main, "append_turn", lambda session, **kw: turns_appended.append(kw) or session
    )
    monkeypatch.setattr(
        main, "close_session", lambda session: closed_sessions.append(session) or session
    )
    monkeypatch.setattr(main, "find_prior_session_summary", lambda *a, **kw: None)
    similar_calls = []
    monkeypatch.setattr(
        main,
        "find_similar_sessions",
        lambda *a, **kw: similar_calls.append((a, kw)) or ["과거 유사 사례: 3턴, 상태 CLOSED"],
    )

    prompts = []
    states = []

    class _FakeToolContext:
        state = {}

    async def fake_run_once(agent, session_id, prompt, state=None):
        prompts.append(prompt)
        states.append(state)
        gate = make_before_tool_callback("CLAIMANT")(
            tool=type("T", (), {"name": "submit_receipt_review"})(),
            args={},
            tool_context=_FakeToolContext(),
        )
        assert gate is None
        submit_receipt_review(
            receipt_id="rct_1",
            task_id=session_id,
            needs_requery=True,
            is_business=True,
            requery_message="총액이 나오게 다시 찍어 보내주세요",
            reason="parsed_amount_minor가 null이다",
        )
        return "금액이 읽히지 않아 재요청으로 판단했습니다"

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/claimant/review", json=_BODY, headers={"Authorization": "Bearer x"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # entity_id는 receipt_id다 — 같은 영수증으로 재호출되면 세션이 이어진다.
    # org_id는 body에서 그대로 전달돼야 한다 — tiered-memory-review.html §8 Phase 2.
    # actor_ref(recipient_id)도 함께 전달돼야 이전 세션 요약 조회의 연결 키가 된다.
    assert sessions_fetched == [(AgentType.CLAIMANT, "rct_1", "rcp_1", "org_1")]
    assert drafts_written == [
        {
            "agent": "CLAIMANT",
            "target_type": "RECEIPT",
            "target_id": "rct_1",
            "task_id": "CLAIMANT:rct_1",
            "payload": {
                "needs_requery": True,
                "is_business": True,
                "requery_message": "총액이 나오게 다시 찍어 보내주세요",
                "reason": "parsed_amount_minor가 null이다",
            },
        }
    ]
    assert audit_log == [
        {
            "agent": "CLAIMANT",
            "action": "TOOL_CALL_STARTED",
            "reason": "tool=submit_receipt_review",
        }
    ]
    assert [t["role"] for t in turns_appended] == ["INPUT", "OUTPUT"]
    assert turns_appended[0]["untrusted"] is True
    assert turns_appended[0]["doc_refs"] == ["rct_1"]
    assert turns_appended[1]["untrusted"] is False
    # 원문은 비신뢰 블록 안에서만 프롬프트에 들어간다.
    assert "<untrusted_receipt_text>" in prompts[0]
    assert "합계 45,000원" in prompts[0]
    assert "과거 유사 사례: 3턴, 상태 CLOSED" in prompts[0]
    assert "참고용" in prompts[0]


def test_unreadable_raw_text_does_not_block_the_review(client, oidc_ok, monkeypatch):
    """GCS를 못 읽어도 500을 내지 않는다 — 구조화 필드만으로 금액·날짜 부재 판정은
    가능하고, 여기서 죽으면 이 영수증은 CLAIMANT draft 없이 남아 재촉 루프가
    문안을 영영 못 받는다."""
    from claimant.receipt_text import ReceiptTextUnavailable

    def boom(uri, **kw):
        raise ReceiptTextUnavailable("403 Forbidden")

    monkeypatch.setattr(main, "fetch_raw_text", boom)
    monkeypatch.setattr(main, "get_or_create_session", lambda *a, **kw: _FakeSession())
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)
    monkeypatch.setattr(main, "find_prior_session_summary", lambda *a, **kw: None)

    prompts = []

    async def fake_run_once(agent, session_id, prompt, *args, **kwargs):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/claimant/review", json=_BODY, headers={"Authorization": "Bearer x"}
    )

    assert resp.status_code == 200
    assert "원문을 읽지 못했습니다" in prompts[0]


def test_no_raw_text_uri_is_not_an_error(client, oidc_ok, monkeypatch):
    """스냅샷 필드는 부재 시 None으로 실려 온다 — URI가 없다고 400을 내지 않는다."""
    called = []
    monkeypatch.setattr(main, "fetch_raw_text", lambda uri, **kw: called.append(uri) or "")
    monkeypatch.setattr(main, "get_or_create_session", lambda *a, **kw: _FakeSession())
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)
    monkeypatch.setattr(main, "find_prior_session_summary", lambda *a, **kw: None)

    async def fake_run_once(agent, session_id, prompt, *args, **kwargs):
        return ""

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/claimant/review",
        json={**_BODY, "raw_text_gcs_uri": None},
        headers={"Authorization": "Bearer x"},
    )

    assert resp.status_code == 200
    assert called == []  # URI가 없으면 GCS를 부르지도 않는다


def test_new_session_injects_prior_session_summary(client, oidc_ok, monkeypatch):
    """agent-session-memory.html 결정 3 — "새 세션엔 이전 세션 요약이 들어간다".
    이 receipt_id로는 첫 호출(session.turns == [])이면 같은 recipient_id의
    이전 닫힌 세션 요약을 찾아 프롬프트에 얹는다."""
    monkeypatch.setattr(main, "fetch_raw_text", lambda uri, **kw: "")
    monkeypatch.setattr(main, "get_or_create_session", lambda *a, **kw: _FakeSession())
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)

    summary_calls = []

    def fake_find_prior_session_summary(agent_type, *, actor_ref, exclude_entity_id, org_id):
        summary_calls.append((agent_type, actor_ref, exclude_entity_id, org_id))
        return "2턴, 관련 문서 ['rct_old'], 상태 CLOSED"

    monkeypatch.setattr(main, "find_prior_session_summary", fake_find_prior_session_summary)
    monkeypatch.setattr(main, "find_similar_sessions", lambda *a, **kw: [])

    prompts = []

    async def fake_run_once(agent, session_id, prompt, *args, **kwargs):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/claimant/review", json=_BODY, headers={"Authorization": "Bearer x"}
    )

    assert resp.status_code == 200
    assert summary_calls == [(AgentType.CLAIMANT, "rcp_1", "rct_1", "org_1")]
    assert "이 청구자의 이전 영수증 세션 요약" in prompts[0]
    assert "2턴, 관련 문서 ['rct_old'], 상태 CLOSED" in prompts[0]


def test_continuing_session_skips_prior_session_summary_lookup(client, oidc_ok, monkeypatch):
    """session.turns가 이미 있으면(같은 receipt_id 재시도) 자기 자신의 턴 기록이
    이미 prior_turns로 들어가므로 find_prior_session_summary를 또 부르지 않는다."""
    from datetime import datetime, timezone

    from shared.memory import Turn

    class _ContinuingSession:
        turns = [
            Turn(
                turn_id="t1",
                ts=datetime.now(timezone.utc),
                role="INPUT",
                content="이전 시도",
                untrusted=True,
            )
        ]

    monkeypatch.setattr(main, "fetch_raw_text", lambda uri, **kw: "")
    monkeypatch.setattr(main, "get_or_create_session", lambda *a, **kw: _ContinuingSession())
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)

    summary_calls = []
    monkeypatch.setattr(
        main, "find_prior_session_summary", lambda *a, **kw: summary_calls.append(1)
    )

    async def fake_run_once(agent, session_id, prompt, *args, **kwargs):
        return ""

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/claimant/review", json=_BODY, headers={"Authorization": "Bearer x"}
    )

    assert resp.status_code == 200
    assert summary_calls == []


def test_continuing_session_skips_similar_sessions_lookup(client, oidc_ok, monkeypatch):
    """session.turns가 이미 있으면(같은 receipt_id 재시도) find_similar_sessions도
    또 부르지 않는다 — find_prior_session_summary와 같은 'not session.turns' 조건."""
    from datetime import datetime, timezone

    from shared.memory import Turn

    class _ContinuingSession:
        turns = [
            Turn(
                turn_id="t1",
                ts=datetime.now(timezone.utc),
                role="INPUT",
                content="이전 시도",
                untrusted=True,
            )
        ]

    monkeypatch.setattr(main, "fetch_raw_text", lambda uri, **kw: "")
    monkeypatch.setattr(main, "get_or_create_session", lambda *a, **kw: _ContinuingSession())
    monkeypatch.setattr(main, "append_turn", lambda session, **kw: session)
    monkeypatch.setattr(main, "find_prior_session_summary", lambda *a, **kw: None)

    similar_calls = []
    monkeypatch.setattr(
        main, "find_similar_sessions", lambda *a, **kw: similar_calls.append(1)
    )

    async def fake_run_once(agent, session_id, prompt, *args, **kwargs):
        return ""

    monkeypatch.setattr(main, "_run_once", fake_run_once)

    resp = client.post(
        "/agents/claimant/review", json=_BODY, headers={"Authorization": "Bearer x"}
    )

    assert resp.status_code == 200
    assert similar_calls == []
