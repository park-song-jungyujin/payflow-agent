"""agent-tools.md, schema-contract.md §9 — 집행자 에이전트 툴.

CLAUDE.md: 매칭 실패 판단, 이상징후 서술이 이 에이전트의 일이다. 자연어 → 정산
필터 변환은 별도 배선(run이 아직 없는 시점이라 entity_id=settlement_run_id를
못 쓴다)이 필요해 이 툴의 범위 밖이다 — 오늘은 이상징후 서술만 다룬다.

미래 거래일 판정은 이제 툴이 아니다 — api가 정산 실행 생성 시점에 미리
계산해 candidate_claims·duplicate_groups와 나란히 프롬프트로 보낸다
(payflow-backend src/matching/future_dated.py). 분석 도중 매번 tool-call
왕복을 만드는 대신 duplicate_groups와 같은 자리에서 읽기만 한다 — 날짜
산술은 여전히 LLM이 스스로 계산하지 않는다는 원칙은 그대로다, "언제"
계산하는지만 바뀌었다.
"""

from google.adk.tools.tool_context import ToolContext

from shared.api_client import reject_claim_items, reject_claims, write_agent_draft


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


def flag_claims(
    settlement_run_id: str,
    task_id: str,
    already_settled_claim_ids: list[str],
    other_rejections: list[dict],
    tool_context: ToolContext,
) -> dict:
    """claim 전체를 통째로 이번 배치에서 반려한다 — flag_personal_use_items가
    물품 한 줄만 빼는 것과 달리, 이건 claim 전체(이 영수증의 청구 전액)를 뺀다.
    이상징후 서술이 끝난 뒤, submit_settlement_analysis를 부르기 전에 **한 번만**
    호출한다 — 신호 강도가 다른 두 유형을 한 번의 호출로 같이 처리한다(내부적으로
    서로 다른 반려 방식을 쓸 뿐, 호출하는 쪽에서는 한 번이면 된다).

    already_settled_claim_ids: exact_duplicate_groups 중 `already_settled_claim_ids`가
        비어있지 않은 그룹의 `claim_ids`(이번 배치 후보)만 넣는다 — 영수증
        고유번호가 과거에 이미 송금 완료된 receipt와 완전일치하는, 가장 확실한
        신호. candidate_claims(tool_context.state)에서 각 claim_id의 items를
        찾아 전부 반려 대상에 넣는다 — item_index를 LLM이 직접 세지 않는다
        (프롬프트에 이미 있는 JSON을 tool call 인자로 재전사하다 배열 원소가
        문자열로 축약되는 사고가 반복됐던 것과 같은 이유).

    other_rejections: 같은 배치 내 중복(duplicate_groups)·미래 거래일
        (future_dated_claims)로 판정된 claim 목록. "애매한 패턴"(당신의 자유
        판단)은 여기 포함하지 않는다. 각 항목은 다음 두 키를 갖는 dict다.
        - claim_id: 반려할 claim의 claim_id.
        - reason: 사람 승인자와(나중에) 청구자 본인이 읽을 한국어 사유. 어느
          유형 때문인지 구체적으로 쓴다(예: "같은 가맹점·같은 금액의 다른
          청구와 중복", "거래일자가 future_dated_claims에 실린 오늘 날짜보다
          미래").

    두 인자 다 해당하는 claim이 없으면 이 툴을 아예 호출하지 않는다 — 둘 다
    빈 리스트로 넘기지 않는다. 한쪽만 있으면 나머지는 빈 리스트로 둔다.

    반환: {"status": "ok", "results": [...]} — already_settled_claim_ids·
        other_rejections 양쪽 결과를 한 리스트에 합쳐 담는다. 한쪽 api 호출이
        실패해도 예외를 던지지 않고 그 부분만 {"status": "error", ...} 항목으로
        담아 나머지 처리(다른 쪽 반려, submit_settlement_analysis)를 막지 않는다.
    """
    if not already_settled_claim_ids and not other_rejections:
        return {
            "status": "error",
            "detail": "already_settled_claim_ids and other_rejections must not both be empty",
        }

    results = []
    made_call = False

    if already_settled_claim_ids:
        candidate_claims = tool_context.state.get("candidate_claims") or []
        claims_by_id = {
            c["claim_id"]: c for c in candidate_claims if isinstance(c, dict) and c.get("claim_id")
        }
        item_rejections = []
        for claim_id in already_settled_claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                continue
            items = claim.get("items") or []
            for item_index in range(len(items)):
                item_rejections.append(
                    {
                        "claim_id": claim_id,
                        "item_index": item_index,
                        "reason": "이미 송금 완료된 영수증의 재청구로 확인되어 자동 반려됨",
                    }
                )
        if item_rejections:
            made_call = True
            try:
                result = reject_claim_items(
                    settlement_run_id=settlement_run_id, task_id=task_id, rejections=item_rejections
                )
                results.extend(result.get("results", []))
            except Exception as e:
                results.append({"status": "error", "detail": str(e)})

    if other_rejections:
        made_call = True
        try:
            result = reject_claims(
                settlement_run_id=settlement_run_id, task_id=task_id, rejections=other_rejections
            )
            results.extend(result.get("results", []))
        except Exception as e:
            results.append({"status": "error", "detail": str(e)})

    if not made_call:
        return {"status": "error", "detail": "no items found for the given already_settled_claim_ids"}

    return {"status": "ok", "results": results}


def submit_settlement_analysis(
    settlement_run_id: str,
    task_id: str,
    anomalies: list[str],
    anomalies_en: list[str],
    summary_text: str,
    summary_text_en: str,
    tool_context: ToolContext,
) -> dict:
    """분석 결과를 api에 기록한다. 분석이 끝나면 반드시 한 번 호출한다.

    settlement_run_id: 분석 대상 정산 실행 ID. 프롬프트에 주어진 값을 그대로 쓴다.
    task_id: 프롬프트에 주어진 멱등키. 같은 task_id로 다시 호출하면 이전 기록을 덮어쓴다.
    anomalies: 이상징후 서술 목록(한국어). 하나도 없으면 빈 리스트 — 그 자체로 정상 결과다.
    anomalies_en: anomalies와 정확히 같은 개수·같은 순서의 영어 버전. 요약이나
        의역이 아니라 같은 내용의 번역이다.
    summary_text: 사람 승인자가 읽을 한국어 종합 요약. 빈 문자열이면 거부된다.
    summary_text_en: summary_text의 영어 번역. summary_text가 비어있지 않은 한
        같이 채운다 — 비어있으면 거부된다.

    web 대시보드의 영어 표시는 예전엔 api가 draft를 받는 시점에 Gemma로 별도
    번역해 채웠지만, 그 순차 호출(최대 15초)이 분석 전체 지연의 큰 부분이었다
    — 이제 이 툴을 호출하는 같은 턴에서 당신이 직접 두 언어를 함께 써서
    보낸다(api/src/guards/agent_drafts.py는 이제 그대로 통과시키기만 한다).

    tool_context는 ADK가 자동 주입한다(LLM에는 안 보이는 파라미터) — main.py가
    이 값을 tool_context.state에 남겨 세션 종료 후 확인한다. main.py.executor_analyze는
    이 함수의 반환값을 볼 수 없다(ADK가 LLM에게 넘길 뿐 라우트로 되돌려주지 않는다) —
    이 상태 없이는 LLM이 툴을 아예 안 부르거나 before_tool_callback에 거부돼도
    라우트가 무조건 200을 반환해, PROCESSING이 영원히 안 풀리는 조용한 실패가 된다.
    """
    if not summary_text.strip():
        tool_context.state["executor_submission_status"] = "error"
        return {"status": "error", "detail": "summary_text must not be empty"}
    if not summary_text_en.strip():
        tool_context.state["executor_submission_status"] = "error"
        return {"status": "error", "detail": "summary_text_en must not be empty"}
    if len(anomalies_en) != len(anomalies):
        tool_context.state["executor_submission_status"] = "error"
        return {"status": "error", "detail": "anomalies_en must have the same length as anomalies"}

    result = write_agent_draft(
        agent="EXECUTOR",
        target_type="SETTLEMENT_RUN",
        target_id=settlement_run_id,
        task_id=task_id,
        payload={
            "anomalies": anomalies,
            "anomalies_en": anomalies_en,
            "summary_text": summary_text,
            "summary_text_en": summary_text_en,
        },
    )
    tool_context.state["executor_submission_status"] = "ok"
    return {"status": "ok", "draft_id": result.get("draft_id")}
