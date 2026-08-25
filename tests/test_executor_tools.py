"""agent-tools.md — executor/tools.py submit_settlement_analysis 단위 테스트."""

from datetime import date, datetime

from executor import tools


class _FakeToolContext:
    def __init__(self):
        self.state = {}


# --- check_future_dated_claims — 날짜 산술은 LLM이 아니라 이 툴이 결정한다 ---


def test_future_dated_pure_function_filters_by_today():
    claims = [
        {"claim_id": "clm_past", "transaction_date": "2026-07-17"},
        {"claim_id": "clm_today", "transaction_date": "2026-08-23"},
        {"claim_id": "clm_future", "transaction_date": "2026-08-24"},
    ]

    result = tools._future_dated(claims, today=date(2026, 8, 23))

    assert result == [{"claim_id": "clm_future", "transaction_date": "2026-08-24"}]


def test_future_dated_skips_missing_or_malformed_dates():
    """근거 없는 필드는 비교하지 않는다 — schema-contract.md §2 verify_passed와 같은 원칙."""
    claims = [
        {"claim_id": "clm_none", "transaction_date": None},
        {"claim_id": "clm_absent"},
        {"claim_id": "clm_bad", "transaction_date": "not-a-date"},
    ]

    result = tools._future_dated(claims, today=date(2026, 8, 23))

    assert result == []


def test_check_future_dated_claims_uses_server_clock(monkeypatch):
    """이 회귀 테스트가 지키는 버그: 2026-07-17(과거)를 LLM이 미래로 오판했던 사례.
    서버 시계를 today로 고정해 그런 오판이 코드 레벨에서 원천 차단되는지 본다."""

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 23, tzinfo=tz)

    monkeypatch.setattr(tools, "datetime", _FixedDatetime)

    result = tools.check_future_dated_claims(
        [{"claim_id": "clm_01MOQBR9F49HMKY6AW61Z14PXG", "transaction_date": "2026-07-17"}]
    )

    assert result == {"today": "2026-08-23", "future_dated": []}


# --- flag_personal_use_items — 청구 반려 자동화 ---


def test_flag_personal_use_items_rejects_without_calling_api_when_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "reject_claim_items", lambda **kw: calls.append(kw) or {})

    result = tools.flag_personal_use_items(
        settlement_run_id="run_1",
        task_id="task_1",
        rejections=[],
        tool_context=_FakeToolContext(),
    )

    assert result == {"status": "error", "detail": "rejections must not be empty"}
    assert calls == []


def test_flag_personal_use_items_forwards_rejections_to_api(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools,
        "reject_claim_items",
        lambda **kw: calls.append(kw)
        or {"results": [{"claim_id": "clm_1", "item_index": 0, "status": "ok", "amount_minor": 5500}]},
    )

    result = tools.flag_personal_use_items(
        settlement_run_id="run_1",
        task_id="task_1",
        rejections=[{"claim_id": "clm_1", "item_index": 0, "reason": "개인 생필품으로 추정"}],
        tool_context=_FakeToolContext(),
    )

    assert calls == [
        {
            "settlement_run_id": "run_1",
            "task_id": "task_1",
            "rejections": [{"claim_id": "clm_1", "item_index": 0, "reason": "개인 생필품으로 추정"}],
        }
    ]
    assert result == {
        "status": "ok",
        "results": [{"claim_id": "clm_1", "item_index": 0, "status": "ok", "amount_minor": 5500}],
    }


def test_flag_personal_use_items_never_raises_when_api_call_fails(monkeypatch):
    """반려가 실패해도(run이 이미 승인됐다거나 네트워크 오류) 예외를 던지지 않는다 —
    던지면 이 뒤에 이어질 submit_settlement_analysis 호출까지 막혀 이상징후
    분석 자체가 조용히 안 끝나는 버그(fix/executor-silent-failure)가 재발한다."""

    def boom(**kw):
        raise RuntimeError("settlement_run status is APPROVED, expected DRAFT")

    monkeypatch.setattr(tools, "reject_claim_items", boom)

    result = tools.flag_personal_use_items(
        settlement_run_id="run_1",
        task_id="task_1",
        rejections=[{"claim_id": "clm_1", "item_index": 0, "reason": "x"}],
        tool_context=_FakeToolContext(),
    )

    assert result == {
        "status": "error",
        "detail": "settlement_run status is APPROVED, expected DRAFT",
    }


def test_empty_summary_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})
    tool_context = _FakeToolContext()

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=[],
        summary_text="   ",
        tool_context=tool_context,
    )

    assert result == {"status": "error", "detail": "summary_text must not be empty"}
    assert calls == []
    # main.py.executor_analyze가 이 값으로 draft가 실제로 안 써졌음을 판단한다.
    assert tool_context.state["executor_submission_status"] == "error"


def test_empty_anomalies_list_is_valid(monkeypatch):
    """이상징후가 없는 것도 정상 결과다 — anomalies가 빈 리스트여도 거부하지 않는다."""
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    tool_context = _FakeToolContext()
    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=[],
        summary_text="이상 없음",
        tool_context=tool_context,
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls[0]["payload"] == {
        "anomalies": [],
        "summary_text": "이상 없음",
    }
    assert tool_context.state["executor_submission_status"] == "ok"


def test_valid_analysis_writes_draft_with_expected_shape(monkeypatch):
    """영어 번역(anomalies_en·summary_text_en)은 이 에이전트가 안 쓴다 — api가
    draft를 받는 시점에 Gemma로 번역해 채운다(api/src/guards/agent_drafts.py)."""
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=["같은 가맹점 · 같은 금액 · 3분 간격 청구 2건"],
        summary_text="중복 의심 1건, 나머지는 이상 없음",
        tool_context=_FakeToolContext(),
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls == [
        {
            "agent": "EXECUTOR",
            "target_type": "SETTLEMENT_RUN",
            "target_id": "run_1",
            "task_id": "task_1",
            "payload": {
                "anomalies": ["같은 가맹점 · 같은 금액 · 3분 간격 청구 2건"],
                "summary_text": "중복 의심 1건, 나머지는 이상 없음",
            },
        }
    ]
