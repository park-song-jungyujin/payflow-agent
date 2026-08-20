"""agent-tools.md — claimant/tools.py submit_receipt_review 단위 테스트.
LLM 없이 툴 함수 자체(입력 검증 + write_agent_draft 호출 형태)만 검증한다."""

from claimant import tools


def test_empty_classification_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_receipt_review(
        receipt_id="rct_1", task_id="task_1", classification="  ", requery_message=""
    )

    assert result == {"status": "error", "detail": "classification must not be empty"}
    assert calls == []  # 검증 실패는 api를 호출하지 않는다


def test_valid_review_writes_draft_with_expected_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        classification="업무용",
        requery_message="",
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls == [
        {
            "agent": "CLAIMANT",
            "target_type": "RECEIPT",
            "target_id": "rct_1",
            "task_id": "task_1",
            "payload": {"classification": "업무용", "requery_message": ""},
        }
    ]


def test_missing_draft_id_in_response_returns_none_not_crash(monkeypatch):
    """api 응답이 예상과 달라도 툴이 예외로 죽지 않는다 — ADK 루프를 끊지 않는다."""
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: {})

    result = tools.submit_receipt_review(
        receipt_id="rct_1", task_id="task_1", classification="개인용", requery_message="영수증 재요청"
    )

    assert result == {"status": "ok", "draft_id": None}
