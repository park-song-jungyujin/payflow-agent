"""agent-tools.md, schema-contract.md §9 — 청구자 에이전트의 유일한 부수효과 툴.

CLAUDE.md: 파싱 결과 검토, 업무용·개인용 분류, 재요청 문안 작성이 이 에이전트의 일이다.
Track A 담당 — 아래는 safety/tools.py와 같은 모양을 맞춘 baseline이다. 실제 분류 로직,
프롬프트, 인자 구성은 Track A가 채운다.
"""

from shared.api_client import write_agent_draft


def submit_receipt_review(
    receipt_id: str, task_id: str, classification: str, requery_message: str
) -> dict:
    """검토 결과를 api에 기록한다. 검토가 끝나면 반드시 한 번 호출한다.

    receipt_id: 검토 대상 영수증 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    classification: 업무용/개인용 분류 결과.
    requery_message: 재요청이 필요하면 보낼 문안. 필요 없으면 빈 문자열.
    """
    if not classification.strip():
        return {"status": "error", "detail": "classification must not be empty"}

    result = write_agent_draft(
        agent="CLAIMANT",
        target_type="RECEIPT",
        target_id=receipt_id,
        task_id=task_id,
        payload={"classification": classification, "requery_message": requery_message},
    )
    return {"status": "ok", "draft_id": result.get("draft_id")}
