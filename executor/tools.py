"""agent-tools.md, schema-contract.md §9 — 집행자 에이전트의 유일한 부수효과 툴.

CLAUDE.md: 매칭 실패 판단, 이상징후 서술이 이 에이전트의 일이다. 자연어 → 정산
필터 변환은 별도 배선(run이 아직 없는 시점이라 entity_id=settlement_run_id를
못 쓴다)이 필요해 이 툴의 범위 밖이다 — 오늘은 이상징후 서술만 다룬다.
"""

from shared.api_client import write_agent_draft


def submit_settlement_analysis(
    settlement_run_id: str, task_id: str, anomalies: list[str], summary_text: str
) -> dict:
    """분석 결과를 api에 기록한다. 분석이 끝나면 반드시 한 번 호출한다.

    settlement_run_id: 분석 대상 정산 실행 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    anomalies: 이상징후 서술 목록. 하나도 없으면 빈 리스트 — 그 자체로 정상 결과다.
    summary_text: 사람 승인자가 읽을 한국어 종합 요약. 빈 문자열이면 거부된다.
    """
    if not summary_text.strip():
        return {"status": "error", "detail": "summary_text must not be empty"}

    result = write_agent_draft(
        agent="EXECUTOR",
        target_type="SETTLEMENT_RUN",
        target_id=settlement_run_id,
        task_id=task_id,
        payload={"anomalies": anomalies, "summary_text": summary_text},
    )
    return {"status": "ok", "draft_id": result.get("draft_id")}
