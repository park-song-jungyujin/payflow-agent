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

from shared.api_client import reject_claim_items, reject_claims, write_agent_draft


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


def check_future_dated_claims(tool_context: ToolContext) -> dict:
    """candidate_claims 중 거래일자가 서버 기준 오늘보다 미래인 건을 찾는다. 인자가
    없다 — candidate_claims는 main.py가 세션 state에 미리 넣어둔 값을 그대로 쓴다.

    이상징후 서술 전에 날짜 관련 판단이 필요하면 반드시 이 툴을 먼저 호출하고,
    그 결과에 있는 claim_id만 "미래 거래일" 이상징후로 서술한다. 이 목록에 없는
    claim을 날짜 이유로 이상징후에 넣지 않는다 — "오늘"의 기준은 이 툴이 반환한
    결과이지 당신의 추정이 아니다.

    LLM이 candidate_claims를 인자로 직접 넘기게 하면, 프롬프트에 이미 있는 JSON을
    tool call 인자로 다시 옮겨적는 과정에서 배열 원소를 객체 대신 문자열로 축약해
    AttributeError로 죽는 사례가 반복됐다(2026-08-25 hotfix). state에서 직접 읽어
    이 재전사 자체를 없앤다.

    반환: {"today": "YYYY-MM-DD", "future_dated": [{"claim_id": str, "transaction_date": str}, ...]}
        transaction_date가 없는 claim은 판정 대상에서 제외한다(근거 없는 필드는
        비교하지 않는다 — schema-contract.md §2 검증 절 verify_passed와 같은 원칙).
        future_dated가 빈 리스트면 미래 거래일 건이 없다는 뜻이다.
    """
    candidate_claims = tool_context.state.get("candidate_claims") or []
    today = datetime.now(UTC).date()
    return {"today": today.isoformat(), "future_dated": _future_dated(candidate_claims, today)}


def flag_personal_use_items(
    settlement_run_id: str,
    task_id: str,
    rejections: list[dict],
    tool_context: ToolContext,
) -> dict:
    """개인적 사용이 의심되는 물품을 청구 반려한다 — 그 물품 가격을 정산 금액에서
    제외한다. 이상징후 서술이 끝난 뒤, submit_settlement_analysis를 부르기 전에
    호출한다(반려 내역을 요약에 포함하려면 먼저 반려부터 끝나야 한다).

    의심 물품을 하나도 못 찾았으면 이 툴을 아예 호출하지 않는다 — 빈 리스트를
    넘기지 않는다. 물품명만으로 업무 관련성이 뚜렷이 없어 보이는 경우로 한정한다
    (개인 미용·개인 생필품·개인 오락 등) — account_category_code와 상충되는
    물품 하나가 섞여 있는 경우가 전형적이다. 클레임 전체가 아니라 그 물품 한 줄만
    반려한다. 사람이 나중에 web에서 언제든 되돌릴 수 있는 상태를 만들 뿐이지만,
    근거 없이 반려하지는 않는다.

    rejections: 반려할 물품 목록. 이번 호출 한 번에 전부 담는다(claim마다
        따로따로 부르지 않는다 — 세션당 툴 호출 횟수 제한에 걸릴 수 있다). 각
        항목은 다음 세 키를 갖는 dict다.
        - claim_id: 그 물품이 속한 claim의 claim_id.
        - item_index: candidate_claims에서 그 claim의 items 배열 안 위치(0부터
          시작).
        - reason: 사람 승인자와(나중에) 청구자 본인이 읽을 한국어 사유. "개인
          사용으로 추정" 같은 모호한 문구 대신 물품명·맥락을 근거로 구체적으로
          쓴다(예: "샴푸는 사무용품 영수증에 섞인 개인 생필품으로 보입니다").

    반환: {"status": "ok", "results": [...]} — results의 각 항목은
        {"claim_id", "item_index", "status", ...}. 일부만 실패해도(잘못된
        item_index 등) 나머지는 정상 반영된다 — status가 "error"인 항목은
        summary_text에 반려 내역으로 넣지 않는다.
        api 호출 자체가 실패해도(네트워크·상태 불일치 등) 예외를 던지지 않고
        {"status": "error", ...}를 돌려준다 — 반려가 실패해도 이상징후 분석
        자체(submit_settlement_analysis)는 계속 진행해야 하기 때문이다.
    """
    if not rejections:
        return {"status": "error", "detail": "rejections must not be empty"}

    try:
        result = reject_claim_items(
            settlement_run_id=settlement_run_id, task_id=task_id, rejections=rejections
        )
    except Exception as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "results": result.get("results", [])}


def flag_duplicate_claims(
    settlement_run_id: str,
    task_id: str,
    claim_ids: list[str],
    tool_context: ToolContext,
) -> dict:
    """이미 송금 완료된 영수증의 재청구로 확인된 claim을 통째로 반려한다 —
    flag_personal_use_items와 달리 물품을 골라 뽑지 않고, claim에 딸린 모든
    물품을 한 번에 반려해 정산 금액을 0으로 만든다(claim 자체가 문제이지
    특정 물품이 문제가 아니므로). 이상징후 서술이 끝난 뒤,
    submit_settlement_analysis를 부르기 전에 호출한다.

    claim_ids: exact_duplicate_groups 중 `already_settled_claim_ids`가
        비어있지 않은 그룹의 `claim_ids`(이번 배치 후보)만 넘긴다 — 영수증
        고유번호가 과거에 이미 송금 완료된 receipt와 완전일치하는, 가장
        확실한 신호일 때만 자동 반려한다. 같은 배치 안에서만 중복인 경우나
        미래 거래일은 여기 넣지 않는다 — flag_suspicious_claims의 몫이다.

    candidate_claims(tool_context.state)에서 각 claim_id의 items를 찾아
    전부 반려 대상에 넣는다 — item_index를 LLM이 직접 세지 않는다
    (check_future_dated_claims와 같은 이유, 재전사 오류 방지).

    반환: flag_personal_use_items와 같은 형태({"status", "results"}).
        candidate_claims에 없는 claim_id는 조용히 건너뛴다.
    """
    if not claim_ids:
        return {"status": "error", "detail": "claim_ids must not be empty"}

    candidate_claims = tool_context.state.get("candidate_claims") or []
    claims_by_id = {
        c["claim_id"]: c for c in candidate_claims if isinstance(c, dict) and c.get("claim_id")
    }

    rejections = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        items = claim.get("items") or []
        for item_index in range(len(items)):
            rejections.append(
                {
                    "claim_id": claim_id,
                    "item_index": item_index,
                    "reason": "이미 송금 완료된 영수증의 재청구로 확인되어 자동 반려됨",
                }
            )

    if not rejections:
        return {"status": "error", "detail": "no items found for the given claim_ids"}

    try:
        result = reject_claim_items(
            settlement_run_id=settlement_run_id, task_id=task_id, rejections=rejections
        )
    except Exception as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "results": result.get("results", [])}


def flag_suspicious_claims(
    settlement_run_id: str,
    task_id: str,
    rejections: list[dict],
    tool_context: ToolContext,
) -> dict:
    """중복 청구(같은 배치 내)·미래 거래일로 판정된 claim을 통째로 이번 배치에서
    제외한다 — flag_personal_use_items가 물품 한 줄만 빼는 것과 달리, 이건 claim
    전체(이 영수증의 청구 전액)를 뺀다. 이상징후 서술이 끝난 뒤,
    submit_settlement_analysis를 부르기 전에 호출한다.

    대상은 duplicate_groups(같은 배치 내 중복)·check_future_dated_claims 결과에
    있는 claim_id만이다 — "애매한 패턴"(4번 유형, 당신의 자유 판단)은 여기 포함하지
    않는다. exact_duplicate_groups 중 already_settled_claim_ids가 있는 그룹은
    flag_duplicate_claims의 몫이다(이미 송금 완료된 영수증 재청구 — 더 강한 신호라
    별도 처리).

    해당하는 claim이 하나도 없으면 이 툴을 아예 호출하지 않는다 — 빈 리스트를
    넘기지 않는다.

    rejections: 반려할 claim 목록. 이번 호출 한 번에 전부 담는다(flag_personal_use_items와
        같은 이유 — 세션당 툴 호출 횟수 제한). 각 항목은 다음 두 키를 갖는 dict다.
        - claim_id: 반려할 claim의 claim_id.
        - reason: 사람 승인자와(나중에) 청구자 본인이 읽을 한국어 사유. 어느 유형
          때문인지 구체적으로 쓴다(예: "같은 가맹점·같은 금액의 다른 청구와 중복",
          "거래일자가 check_future_dated_claims가 알려준 오늘 날짜보다 미래").

    반환: {"status": "ok", "results": [...]} — results의 각 항목은
        {"claim_id", "status", ...}. flag_personal_use_items와 같은 이유로 이
        함수 자체는 예외를 던지지 않는다 — api 호출이 실패해도
        {"status": "error", ...}를 돌려주고 이상징후 분석은 계속 진행된다.
    """
    if not rejections:
        return {"status": "error", "detail": "rejections must not be empty"}

    try:
        result = reject_claims(
            settlement_run_id=settlement_run_id, task_id=task_id, rejections=rejections
        )
    except Exception as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "results": result.get("results", [])}


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
