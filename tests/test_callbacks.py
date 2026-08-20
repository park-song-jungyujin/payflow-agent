"""agent-tools.md before_tool_callback — 세션당 호출 횟수 제한과 감사 로그.
money-safety.md 절대 규칙 3과 무관한 폭주 방지 게이트라, 실제 한도(금액)는
다루지 않는다는 걸 전제로 카운팅/거부/감사 순서만 검증한다."""

import pytest

from shared import callbacks


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeToolContext:
    def __init__(self):
        self.state = {}


@pytest.fixture
def audit_log(monkeypatch):
    log = []
    monkeypatch.setattr(
        callbacks,
        "record_tool_call_audit",
        lambda **kw: log.append(kw),
    )
    return log


def test_calls_within_limit_are_allowed_and_audited(audit_log):
    before = callbacks.make_before_tool_callback("SAFETY")
    tool, ctx = FakeTool("submit_risk_report"), FakeToolContext()

    result = before(tool, {}, ctx)

    assert result is None  # None이면 ADK가 실제 툴을 호출한다
    assert audit_log == [
        {"agent": "SAFETY", "action": "TOOL_CALL_STARTED", "reason": "tool=submit_risk_report"}
    ]


def test_call_count_persists_across_invocations_in_same_session(audit_log):
    before = callbacks.make_before_tool_callback("SAFETY")
    tool, ctx = FakeTool("submit_risk_report"), FakeToolContext()

    before(tool, {}, ctx)
    before(tool, {}, ctx)

    counts = ctx.state[callbacks._CALL_COUNTS_KEY]
    assert counts["submit_risk_report"] == 2


def test_call_over_max_is_rejected_not_raised(monkeypatch, audit_log):
    monkeypatch.setenv("AGENT_TOOL_MAX_CALLS_PER_SESSION", "2")
    before = callbacks.make_before_tool_callback("SAFETY")
    tool, ctx = FakeTool("submit_risk_report"), FakeToolContext()

    before(tool, {}, ctx)
    before(tool, {}, ctx)
    result = before(tool, {}, ctx)  # 3번째 — 한도 초과

    assert result["status"] == "rejected"
    assert "AGENT_TOOL_MAX_CALLS_PER_SESSION=2" in result["reason"]


def test_rejected_call_is_audited_as_rejected_not_started(monkeypatch, audit_log):
    monkeypatch.setenv("AGENT_TOOL_MAX_CALLS_PER_SESSION", "1")
    before = callbacks.make_before_tool_callback("SAFETY")
    tool, ctx = FakeTool("submit_risk_report"), FakeToolContext()

    before(tool, {}, ctx)  # 1회차 — 허용
    before(tool, {}, ctx)  # 2회차 — 거부

    actions = [entry["action"] for entry in audit_log]
    assert actions == ["TOOL_CALL_STARTED", "TOOL_CALL_REJECTED"]


def test_different_tools_have_independent_counters(audit_log):
    before = callbacks.make_before_tool_callback("CLAIMANT")
    ctx = FakeToolContext()

    before(FakeTool("tool_a"), {}, ctx)
    before(FakeTool("tool_b"), {}, ctx)

    counts = ctx.state[callbacks._CALL_COUNTS_KEY]
    assert counts == {"tool_a": 1, "tool_b": 1}
