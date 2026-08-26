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


def reject_claim_items(
    *, settlement_run_id: str, task_id: str, rejections: list[dict]
) -> dict:
    """청구 반려 자동화 — api/src/settlements/routes.py의
    POST /agents/executor/reject-items를 부른다. 사람이 web 체크박스로 직접 하는
    것과 최종 효과(_apply_item_exclusion)는 같다 — 금액 재계산은 api가 한다,
    여기서는 "어떤 물품을 반려할지"만 실어 보낸다(절대 규칙 3)."""
    return _post(
        "/agents/executor/reject-items",
        {"settlement_run_id": settlement_run_id, "task_id": task_id, "rejections": rejections},
    )


def reject_claims(*, settlement_run_id: str, task_id: str, rejections: list[dict]) -> dict:
    """청구 전체 반려 자동화 — reject_claim_items와 같은 자리, 대상이 물품 한 줄이
    아니라 claim 전체(중복 청구·동일 영수증 재제출·미래 거래일)다. api의
    POST /agents/executor/reject-claims를 부른다 — 이번에도 "어떤 claim을 반려할지"만
    실어 보내고, 실제로 배치 합계에서 빼는 계산은 api가 한다."""
    return _post(
        "/agents/executor/reject-claims",
        {"settlement_run_id": settlement_run_id, "task_id": task_id, "rejections": rejections},
    )
