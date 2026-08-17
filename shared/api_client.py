"""architecture.md — Firestore는 api가 제공하는 툴을 통해서만 접근한다. SDK 직접
쓰기 금지. 이 모듈이 그 유일한 창구다: api/src/guards/agent_drafts.py의
POST /agents/drafts, POST /agents/audit을 호출한다.

인증은 agent 서비스 계정이 자기 신원으로 만든 ID 토큰이다(Cloud Run 메타데이터
서버, 별도 IAM 바인딩 불필요 — api가 allUsers invoker라 OIDC audience만 맞으면
통과한다. infra/iam.tf api_public 참조).
"""

import os

import requests
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token


def _fetch_id_token() -> str:
    audience = os.environ["API_OIDC_AUDIENCE"]
    return google_id_token.fetch_id_token(Request(), audience)


def _api_base_url() -> str:
    return os.environ["API_BASE_URL"].rstrip("/")


def _post(path: str, body: dict) -> dict:
    resp = requests.post(
        f"{_api_base_url()}{path}",
        json=body,
        headers={"Authorization": f"Bearer {_fetch_id_token()}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def write_agent_draft(
    *, agent: str, target_type: str, target_id: str, task_id: str, payload: dict
) -> dict:
    """schema-contract.md §2/§9 agent_drafts에 쓴다. api만 읽는다 — 에이전트끼리
    이 컬렉션으로 간접 통신하지 않는다."""
    return _post(
        "/agents/drafts",
        {
            "agent": agent,
            "target_type": target_type,
            "target_id": target_id,
            "task_id": task_id,
            "payload": payload,
        },
    )


def record_tool_call_audit(
    *, agent: str, action: str, run_id: str | None = None, reason: str | None = None
) -> None:
    """money-safety.md — 모든 툴 호출을 audit_logs에 남긴다. 거부된 시도도 포함."""
    _post(
        "/agents/audit",
        {"agent": agent, "action": action, "run_id": run_id, "reason": reason},
    )
