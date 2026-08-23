"""agent-tools.md — executor/tools.py submit_settlement_analysis 단위 테스트."""

from datetime import date, datetime

from executor import tools


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


def test_empty_summary_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=[],
        summary_text="   ",
        anomalies_en=[],
        summary_text_en="ok",
    )

    assert result == {"status": "error", "detail": "summary_text/summary_text_en must not be empty"}
    assert calls == []


def test_empty_summary_en_rejected_without_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=[],
        summary_text="이상 없음",
        anomalies_en=[],
        summary_text_en="  ",
    )

    assert result == {"status": "error", "detail": "summary_text/summary_text_en must not be empty"}
    assert calls == []


def test_mismatched_anomalies_length_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "write_agent_draft", lambda **kw: calls.append(kw) or {})

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=["중복 의심 1건"],
        summary_text="중복 의심 1건",
        anomalies_en=[],
        summary_text_en="1 suspected duplicate",
    )

    assert result == {
        "status": "error",
        "detail": "anomalies and anomalies_en must have the same length",
    }
    assert calls == []


def test_empty_anomalies_list_is_valid(monkeypatch):
    """이상징후가 없는 것도 정상 결과다 — anomalies가 빈 리스트여도 거부하지 않는다."""
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=[],
        summary_text="이상 없음",
        anomalies_en=[],
        summary_text_en="No anomalies found",
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls[0]["payload"] == {
        "anomalies": [],
        "summary_text": "이상 없음",
        "anomalies_en": [],
        "summary_text_en": "No anomalies found",
    }


def test_valid_analysis_writes_draft_with_expected_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=["같은 가맹점 · 같은 금액 · 3분 간격 청구 2건"],
        summary_text="중복 의심 1건, 나머지는 이상 없음",
        anomalies_en=["Same merchant, same amount, two claims 3 minutes apart"],
        summary_text_en="1 suspected duplicate, no issues otherwise",
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
                "anomalies_en": ["Same merchant, same amount, two claims 3 minutes apart"],
                "summary_text_en": "1 suspected duplicate, no issues otherwise",
            },
        }
    ]
