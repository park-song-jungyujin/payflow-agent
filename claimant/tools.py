"""agent-tools.md, schema-contract.md §9 — 청구자 에이전트의 유일한 부수효과 툴.

CLAUDE.md: 파싱 결과 검토, 업무용·개인용 분류, 재요청 문안 작성이 이 에이전트의 일이다.

**payload는 백엔드 `parse_claimant_payload`(api/src/ingest/drafts.py)가 요구하는
모양이어야 한다** — `needs_requery: bool`이 필수다. 없으면 `InvalidDraftPayload`로
`CLAIMANT_DRAFT_APPLY_INVALID_PAYLOAD` 감사 로그만 남고 200으로 끝나 draft가 조용히
버려진다. 스캐폴딩이 쓰던 `{classification, requery_message}`가 정확히 그 모양이었다.
"""

from shared.api_client import write_agent_draft


def submit_receipt_review(
    receipt_id: str,
    task_id: str,
    needs_requery: bool,
    is_business: bool,
    requery_message: str,
    reason: str,
) -> dict:
    """검토 결과를 api에 기록한다. 검토가 끝나면 반드시 한 번 호출한다.

    receipt_id: 검토 대상 영수증 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    needs_requery: 사람에게 영수증을 다시 요청해야 하면 True. 애매하면 False다 —
        True는 청구를 정산 후보에서 빼기 때문에 잘못 걸면 정당한 지출이 지급되지 않는다.
    is_business: 업무 경비로 보이면 True. 판단이 안 서면 True다.
    requery_message: needs_requery=True일 때 사람에게 보낼 한국어 문안.
        False면 반드시 빈 문자열.
    reason: 이 판정의 근거. 항상 채운다 — 감사 로그에 남는 유일한 서술이다.
    """
    # 예외를 던지지 않는다 — ADK 툴은 반환값으로 LLM에게 말한다. 던지면 루프가
    # 끊겨 draft가 아예 안 써진다.
    if not reason.strip():
        return {"status": "error", "detail": "reason must not be empty"}
    # 문안 없는 재요청은 여기서 막는 게 가장 이른 지점이다 — 재촉 루프는 코드가
    # 문장을 짓지 않으므로, 문안이 없으면 claim_request가 PENDING인 채 아무도
    # 못 받고 claim은 DRAFT로 강등된 채 정산 후보에서 영구 제외된다.
    if needs_requery and not requery_message.strip():
        return {
            "status": "error",
            "detail": "needs_requery=True requires a non-empty requery_message",
        }
    # 반대 방향도 막는다 — 재요청이 아닌데 문안이 있으면 나중에 누가 그걸 보낼지 모른다.
    if not needs_requery and requery_message.strip():
        return {
            "status": "error",
            "detail": "requery_message must be empty when needs_requery=False",
        }

    result = write_agent_draft(
        agent="CLAIMANT",
        target_type="RECEIPT",
        target_id=receipt_id,
        task_id=task_id,
        payload={
            "needs_requery": needs_requery,
            "is_business": is_business,
            "requery_message": requery_message,
            "reason": reason,
        },
    )
    return {"status": "ok", "draft_id": result.get("draft_id")}
