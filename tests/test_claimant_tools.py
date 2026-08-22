"""agent-tools.md — claimant/tools.py submit_receipt_review 단위 테스트.
LLM 없이 툴 함수 자체(입력 검증 + write_agent_draft 호출 형태)만 검증한다.

**payload 모양이 이 스위트의 핵심이다.** 백엔드 parse_claimant_payload가
`needs_requery: bool`을 필수로 요구하는데, 스캐폴딩은 `{classification,
requery_message}`를 보내고 있었다 — LLM만 붙였으면 draft가 전부 조용히 버려졌다.
"""

from claimant import tools


def _capture(monkeypatch, draft_id="drf_task_1"):
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": draft_id}
    )
    return calls


def test_empty_reason_rejected_without_calling_api(monkeypatch):
    """근거 없는 판정은 감사 로그에 남길 게 없다."""
    calls = _capture(monkeypatch)

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=False,
        is_business=True,
        requery_message="",
        reason="  ",
    )

    assert result == {"status": "error", "detail": "reason must not be empty"}
    assert calls == []  # 검증 실패는 api를 호출하지 않는다


def test_requery_without_message_rejected(monkeypatch):
    """문안 없는 재요청은 재촉 루프가 발송할 게 없어 claim_request가 PENDING인 채
    아무도 못 받는다. 툴에서 막는 게 가장 이른 지점이다."""
    calls = _capture(monkeypatch)

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=True,
        is_business=True,
        requery_message="   ",
        reason="금액이 읽히지 않는다",
    )

    assert result["status"] == "error"
    assert "requery_message" in result["detail"]
    assert calls == []


def test_message_without_requery_rejected(monkeypatch):
    """재요청이 아닌데 문안이 있으면 나중에 누가 그걸 보낼지 모른다."""
    calls = _capture(monkeypatch)

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=False,
        is_business=True,
        requery_message="다시 보내주세요",
        reason="정상 파싱",
    )

    assert result["status"] == "error"
    assert calls == []


def test_valid_review_writes_draft_with_contract_payload(monkeypatch):
    calls = _capture(monkeypatch)

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=False,
        is_business=True,
        requery_message="",
        reason="금액·날짜 모두 읽혔고 원문과 모순 없음",
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls == [
        {
            "agent": "CLAIMANT",
            "target_type": "RECEIPT",
            "target_id": "rct_1",
            "task_id": "task_1",
            "payload": {
                "needs_requery": False,
                "is_business": True,
                "requery_message": "",
                "reason": "금액·날짜 모두 읽혔고 원문과 모순 없음",
            },
        }
    ]


def test_requery_with_message_writes_draft(monkeypatch):
    calls = _capture(monkeypatch)

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=True,
        is_business=True,
        requery_message="총액이 나오게 다시 찍어 보내주세요",
        reason="parsed_amount_minor가 null이다",
    )

    assert result["status"] == "ok"
    assert calls[0]["payload"]["needs_requery"] is True
    assert calls[0]["payload"]["requery_message"] == "총액이 나오게 다시 찍어 보내주세요"


def test_missing_draft_id_in_response_returns_none_not_crash(monkeypatch):
    """api 응답이 예상과 달라도 툴이 예외로 죽지 않는다 — ADK 루프를 끊지 않는다."""
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: {})

    result = tools.submit_receipt_review(
        receipt_id="rct_1",
        task_id="task_1",
        needs_requery=False,
        is_business=True,
        requery_message="",
        reason="정상",
    )

    assert result == {"status": "ok", "draft_id": None}
