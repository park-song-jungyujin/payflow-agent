"""agent-tools.md, schema-contract.md §9 — 집행자 에이전트 툴.

CLAUDE.md: 매칭 실패 판단, 이상징후 서술이 이 에이전트의 일이다. 자연어 → 정산
필터 변환은 별도 배선(run이 아직 없는 시점이라 entity_id=settlement_run_id를
못 쓴다)이 필요해 이 툴의 범위 밖이다 — 오늘은 이상징후 서술만 다룬다.

`check_future_dated_claims`는 부수효과가 없는 읽기 전용 판정 툴이다 — 날짜 비교처럼
정답이 하나인 판정을 LLM의 자연어 추론에 맡기면 "오늘"을 잘못 알거나 상대적 날짜
계산을 틀릴 수 있다(예: 실제로는 과거인 거래일을 미래로 오판). 이런 유형의 이상징후는
LLM이 스스로 계산하지 않고 이 툴을 호출해 서버 시계 기준 판정만 받는다.
"""

from datetime import UTC, date, datetime

from google.adk.tools.tool_context import ToolContext

from shared.api_client import write_agent_draft


def _future_dated(candidate_claims: list[dict], today: date) -> list[dict]:
    """순수 함수 — today를 인자로 받아 테스트 가능하게 한다(reminders.decide와 같은
    패턴, docs/journal 2026-08-22 §7 참조: 서버 시계를 함수 내부에서 직접 읽으면
    경계 시각을 테스트하기 어렵다)."""
    future_dated = []
    for c in candidate_claims:
        if not isinstance(c, dict):
            continue
        txn_date_raw = c.get("transaction_date")
        if not txn_date_raw:
            continue
        try:
            txn_date = date.fromisoformat(txn_date_raw)
        except (TypeError, ValueError):
            continue
        if txn_date > today:
            future_dated.append({"claim_id": c["claim_id"], "transaction_date": txn_date_raw})
    return future_dated


def check_future_dated_claims(candidate_claims: list[dict]) -> dict:
    """candidate_claims 중 거래일자가 서버 기준 오늘보다 미래인 건을 찾는다.

    이상징후 서술 전에 날짜 관련 판단이 필요하면 반드시 이 툴을 먼저 호출하고,
    그 결과에 있는 claim_id만 "미래 거래일" 이상징후로 서술한다. 이 목록에 없는
    claim을 날짜 이유로 이상징후에 넣지 않는다 — "오늘"의 기준은 이 툴이 반환한
    결과이지 당신의 추정이 아니다.

    candidate_claims: 프롬프트에 주어진 후보 claim 목록을 그대로 넘긴다. 각 항목은
        최소 claim_id, transaction_date("YYYY-MM-DD" 또는 null)를 갖는다.
    반환: {"today": "YYYY-MM-DD", "future_dated": [{"claim_id": str, "transaction_date": str}, ...]}
        transaction_date가 없는 claim은 판정 대상에서 제외한다(근거 없는 필드는
        비교하지 않는다 — schema-contract.md §2 검증 절 verify_passed와 같은 원칙).
        future_dated가 빈 리스트면 미래 거래일 건이 없다는 뜻이다.
    """
    today = datetime.now(UTC).date()
    return {"today": today.isoformat(), "future_dated": _future_dated(candidate_claims, today)}


def submit_settlement_analysis(
    settlement_run_id: str,
    task_id: str,
    anomalies: list[str],
    summary_text: str,
    tool_context: ToolContext,
) -> dict:
    """분석 결과를 api에 기록한다. 분석이 끝나면 반드시 한 번 호출한다.

    settlement_run_id: 분석 대상 정산 실행 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    anomalies: 이상징후 서술 목록(한국어). 하나도 없으면 빈 리스트 — 그 자체로 정상 결과다.
    summary_text: 사람 승인자가 읽을 한국어 종합 요약. 빈 문자열이면 거부된다.

    영어 번역(anomalies_en·summary_text_en)은 여기서 안 만든다 — api가 draft를
    받는 시점에 Gemma로 번역해 채운다(api/src/guards/agent_drafts.py).

    tool_context는 ADK가 자동 주입한다(LLM에는 안 보이는 파라미터) — main.py가
    이 값을 tool_context.state에 남겨 세션 종료 후 확인한다. main.py.executor_analyze는
    이 함수의 반환값을 볼 수 없다(ADK가 LLM에게 넘길 뿐 라우트로 되돌려주지 않는다) —
    이 상태 없이는 LLM이 툴을 아예 안 부르거나 before_tool_callback에 거부돼도
    라우트가 무조건 200을 반환해, PROCESSING이 영원히 안 풀리는 조용한 실패가 된다.
    """
    if not summary_text.strip():
        tool_context.state["executor_submission_status"] = "error"
        return {"status": "error", "detail": "summary_text must not be empty"}

    result = write_agent_draft(
        agent="EXECUTOR",
        target_type="SETTLEMENT_RUN",
        target_id=settlement_run_id,
        task_id=task_id,
        payload={
            "anomalies": anomalies,
            "summary_text": summary_text,
        },
    )
    tool_context.state["executor_submission_status"] = "ok"
    return {"status": "ok", "draft_id": result.get("draft_id")}
