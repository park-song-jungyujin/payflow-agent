"""agent-tools.md — executor/tools.py 단위 테스트."""

from executor import tools


class _FakeToolContext:
    def __init__(self):
        self.state = {}


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
        rejections=[tools.ItemRejection(claim_id="clm_1", item_index=0, reason="개인 생필품으로 추정")],
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
        rejections=[tools.ItemRejection(claim_id="clm_1", item_index=0, reason="x")],
        tool_context=_FakeToolContext(),
    )

    assert result == {
        "status": "error",
        "detail": "settlement_run status is APPROVED, expected DRAFT",
    }


# --- flag_claims — 청구 전체 반려 자동화 (already_settled + 같은 배치 내 중복·미래 거래일) ---


def test_flag_claims_rejects_without_calling_api_when_both_empty(monkeypatch):
    item_calls = []
    claim_calls = []
    monkeypatch.setattr(tools, "reject_claim_items", lambda **kw: item_calls.append(kw) or {})
    monkeypatch.setattr(tools, "reject_claims", lambda **kw: claim_calls.append(kw) or {})

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=[],
        other_rejections=[],
        tool_context=_FakeToolContext(),
    )

    assert result == {
        "status": "error",
        "detail": "already_settled_claim_ids and other_rejections must not both be empty",
    }
    assert item_calls == []
    assert claim_calls == []


def test_flag_claims_already_settled_rejects_every_item_in_the_claim(monkeypatch):
    """물품을 골라 뽑지 않는다 — claim에 딸린 모든 item_index를 반려 대상에 넣는다."""
    calls = []
    monkeypatch.setattr(
        tools,
        "reject_claim_items",
        lambda **kw: calls.append(kw)
        or {
            "results": [
                {"claim_id": "clm_1", "item_index": 0, "status": "ok", "amount_minor": 0},
                {"claim_id": "clm_1", "item_index": 1, "status": "ok", "amount_minor": 0},
            ]
        },
    )
    tool_context = _FakeToolContext()
    tool_context.state["candidate_claims"] = [
        {
            "claim_id": "clm_1",
            "items": [{"name": "택시비", "amount_minor": 18500}, {"name": "부가세", "amount_minor": 0}],
        },
        {"claim_id": "clm_other", "items": [{"name": "무관 물품", "amount_minor": 1000}]},
    ]

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=["clm_1"],
        other_rejections=[],
        tool_context=tool_context,
    )

    assert calls == [
        {
            "settlement_run_id": "run_1",
            "task_id": "task_1",
            "rejections": [
                {
                    "claim_id": "clm_1",
                    "item_index": 0,
                    "reason": "Automatically rejected: confirmed re-submission of a receipt that was already paid out",
                },
                {
                    "claim_id": "clm_1",
                    "item_index": 1,
                    "reason": "Automatically rejected: confirmed re-submission of a receipt that was already paid out",
                },
            ],
        }
    ]
    assert result["status"] == "ok"


def test_flag_claims_already_settled_skips_unknown_claim_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "reject_claim_items", lambda **kw: calls.append(kw) or {})
    tool_context = _FakeToolContext()
    tool_context.state["candidate_claims"] = [{"claim_id": "clm_1", "items": []}]

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=["clm_missing"],
        other_rejections=[],
        tool_context=tool_context,
    )

    assert result == {"status": "error", "detail": "no items found for the given already_settled_claim_ids"}
    assert calls == []


def test_flag_claims_other_rejections_forwards_to_api(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools,
        "reject_claims",
        lambda **kw: calls.append(kw)
        or {"results": [{"claim_id": "clm_1", "status": "ok", "excluded": True}]},
    )

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=[],
        other_rejections=[tools.ClaimRejection(claim_id="clm_1", reason="동일 영수증 재제출 의심")],
        tool_context=_FakeToolContext(),
    )

    assert calls == [
        {
            "settlement_run_id": "run_1",
            "task_id": "task_1",
            "rejections": [{"claim_id": "clm_1", "reason": "동일 영수증 재제출 의심"}],
        }
    ]
    assert result == {
        "status": "ok",
        "results": [{"claim_id": "clm_1", "status": "ok", "excluded": True}],
    }


def test_flag_claims_combines_both_kinds_in_one_call(monkeypatch):
    """already_settled_claim_ids와 other_rejections가 둘 다 있으면 한 번의 호출로
    두 api를 다 부르고 결과를 합쳐 돌려준다 — LLM은 한 번만 부르면 된다."""
    item_calls = []
    claim_calls = []
    monkeypatch.setattr(
        tools,
        "reject_claim_items",
        lambda **kw: item_calls.append(kw)
        or {"results": [{"claim_id": "clm_1", "item_index": 0, "status": "ok", "amount_minor": 0}]},
    )
    monkeypatch.setattr(
        tools,
        "reject_claims",
        lambda **kw: claim_calls.append(kw)
        or {"results": [{"claim_id": "clm_2", "status": "ok", "excluded": True}]},
    )
    tool_context = _FakeToolContext()
    tool_context.state["candidate_claims"] = [{"claim_id": "clm_1", "items": [{"name": "a"}]}]

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=["clm_1"],
        other_rejections=[tools.ClaimRejection(claim_id="clm_2", reason="미래 거래일")],
        tool_context=tool_context,
    )

    assert len(item_calls) == 1
    assert len(claim_calls) == 1
    assert result == {
        "status": "ok",
        "results": [
            {"claim_id": "clm_1", "item_index": 0, "status": "ok", "amount_minor": 0},
            {"claim_id": "clm_2", "status": "ok", "excluded": True},
        ],
    }


def test_flag_claims_never_raises_when_already_settled_api_call_fails(monkeypatch):
    def boom(**kw):
        raise RuntimeError("settlement_run status is APPROVED, expected DRAFT")

    monkeypatch.setattr(tools, "reject_claim_items", boom)
    tool_context = _FakeToolContext()
    tool_context.state["candidate_claims"] = [{"claim_id": "clm_1", "items": [{"name": "a"}]}]

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=["clm_1"],
        other_rejections=[],
        tool_context=tool_context,
    )

    assert result == {
        "status": "ok",
        "results": [{"status": "error", "detail": "settlement_run status is APPROVED, expected DRAFT"}],
    }


def test_flag_claims_never_raises_when_other_rejections_api_call_fails(monkeypatch):
    """flag_personal_use_items와 같은 이유 — 반려 실패로 submit_settlement_analysis
    호출까지 막히면 안 된다."""

    def boom(**kw):
        raise RuntimeError("settlement_run status is APPROVED, expected DRAFT")

    monkeypatch.setattr(tools, "reject_claims", boom)

    result = tools.flag_claims(
        settlement_run_id="run_1",
        task_id="task_1",
        already_settled_claim_ids=[],
        other_rejections=[tools.ClaimRejection(claim_id="clm_1", reason="x")],
        tool_context=_FakeToolContext(),
    )

    assert result == {
        "status": "ok",
        "results": [{"status": "error", "detail": "settlement_run status is APPROVED, expected DRAFT"}],
    }


# --- submit_settlement_analysis ---


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
        summary_text="No anomalies found",
        tool_context=tool_context,
    )

    assert result == {"status": "ok", "draft_id": "drf_task_1"}
    assert calls[0]["payload"] == {
        "anomalies": [],
        "summary_text": "No anomalies found",
    }
    assert tool_context.state["executor_submission_status"] == "ok"


def test_valid_analysis_writes_english_only_draft(monkeypatch):
    """anomalies·summary_text는 영어가 기본이다(schema-contract.md §9) — api가
    이 payload를 영어 그대로 커밋하고, 한국어(anomalies_ko·summary_text_ko)는
    별도 Cloud Task가 Gemma로 번역해 나중에 채운다. 이 에이전트는 한국어를
    쓰지도, _en 병행 필드를 보내지도 않는다."""
    calls = []
    monkeypatch.setattr(
        tools, "write_agent_draft", lambda **kw: calls.append(kw) or {"draft_id": "drf_task_1"}
    )

    result = tools.submit_settlement_analysis(
        settlement_run_id="run_1",
        task_id="task_1",
        anomalies=["Same merchant, same amount, 2 claims 3 minutes apart"],
        summary_text="1 suspected duplicate, no other anomalies",
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
                "anomalies": ["Same merchant, same amount, 2 claims 3 minutes apart"],
                "summary_text": "1 suspected duplicate, no other anomalies",
            },
        }
    ]
