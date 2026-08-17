"""agent-tools.md — 부수효과가 있는 툴(Firestore를 쓰는 툴)은 전부
before_tool_callback을 통과한다. 셋이 각자 만들지 않도록 여기 하나로 모은다.

1. 한도 검사 — 같은 세션에서 툴이 너무 여러 번 불리면 거부한다(폭주 방지). 실제
   송금 한도는 여기서 다루지 않는다 — 그건 api/src/guards/의 코드 소관이다
   (money-safety.md, 절대 규칙 3).
2. 중복 실행 검사 — 별도로 하지 않는다. write_agent_draft가 task_id를 Firestore
   문서 ID로 써서 같은 Cloud Tasks 재시도는 자동으로 같은 draft를 덮어쓴다
   (schema-contract.md §2) — 멱등성이 쓰기 자체에 내장돼 있다.
3. 감사 로그 — 호출 시도를 항상 api에 남긴다. 거부됐어도 남긴다.
"""

import os

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from .api_client import record_tool_call_audit

_CALL_COUNTS_KEY = "before_tool_callback:call_counts"


def make_before_tool_callback(agent_name: str):
    """agent_name: schema-contract.md AgentName — CLAIMANT/EXECUTOR/SAFETY."""

    def before_tool_callback(tool: BaseTool, args: dict, tool_context: ToolContext):
        counts = tool_context.state.get(_CALL_COUNTS_KEY, {})
        count = counts.get(tool.name, 0) + 1
        counts[tool.name] = count
        tool_context.state[_CALL_COUNTS_KEY] = counts

        max_calls = int(os.environ.get("AGENT_TOOL_MAX_CALLS_PER_SESSION", "3"))
        if count > max_calls:
            reason = f"{tool.name} exceeded AGENT_TOOL_MAX_CALLS_PER_SESSION={max_calls}"
            record_tool_call_audit(agent=agent_name, action="TOOL_CALL_REJECTED", reason=reason)
            return {"status": "rejected", "reason": reason}

        record_tool_call_audit(
            agent=agent_name, action="TOOL_CALL_STARTED", reason=f"tool={tool.name}"
        )
        return None

    return before_tool_callback
