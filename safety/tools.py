"""agent-tools.md, schema-contract.md §9 — 안전 확인 에이전트의 유일한 부수효과 툴.

승인 카드 렌더 직전 호출되어 리스크 리포트를 api의 agent_drafts에 기록한다.
architecture.md: 이 리포트는 조언일 뿐이다. 한도 초과·중복 송금·CAS 전이 같은 실제
차단은 이 툴이 하지 않는다 — api/src/guards/의 코드가 독립적으로, 이 리포트가 비어
있어도 그대로 동작한다.
"""

from shared.api_client import write_agent_draft


def submit_risk_report(settlement_run_id: str, task_id: str, risk_report: str) -> dict:
    """작성한 리스크 리포트를 api에 기록한다. 리포트 작성이 끝나면 반드시 한 번 호출한다.

    settlement_run_id: 검토 대상 정산 실행 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    risk_report: 사람 승인자가 읽을 한국어 리스크 서술. 빈 문자열이면 거부된다.
    """
    if not risk_report.strip():
        return {"status": "error", "detail": "risk_report must not be empty"}

    result = write_agent_draft(
        agent="SAFETY",
        target_type="SETTLEMENT_RUN",
        target_id=settlement_run_id,
        task_id=task_id,
        payload={"risk_report": risk_report},
    )
    return {"status": "ok", "draft_id": result.get("draft_id")}
