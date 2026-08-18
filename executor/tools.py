"""agent-tools.md, schema-contract.md §9 — 집행자 에이전트의 유일한 부수효과 툴.

CLAUDE.md: 매칭 실패 판단, 이상징후 서술, 자연어 → 정산 필터가 이 에이전트의 일이다.
Track B 담당 — 아래는 safety/tools.py와 같은 모양을 맞춘 baseline이다. 실제 판단
로직, 프롬프트, 인자 구성은 Track B가 채운다.
"""

from shared.api_client import write_agent_draft


def submit_settlement_analysis(
    settlement_run_id: str, task_id: str, analysis: str
) -> dict:
    """분석 결과를 api에 기록한다. 분석이 끝나면 반드시 한 번 호출한다.

    settlement_run_id: 분석 대상 정산 실행 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    analysis: 매칭 실패·이상징후에 대한 한국어 서술.
    """
    if not analysis.strip():
        return {"status": "error", "detail": "analysis must not be empty"}

    result = write_agent_draft(
        agent="EXECUTOR",
        target_type="SETTLEMENT_RUN",
        target_id=settlement_run_id,
        task_id=task_id,
        payload={"analysis": analysis},
    )
    return {"status": "ok", "draft_id": result.get("draft_id")}
