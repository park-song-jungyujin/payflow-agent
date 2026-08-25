"""schema-contract.md §2 `agent_sessions`, architecture.md "agent" 예외 — 청구자·
집행자 세션(대화) 이어가기의 유일한 창구. `agent` 서비스가 Firestore에 직접 쓰는
유일한 지점이다. 이 모듈이 `agent_sessions` 외 다른 컬렉션을 건드리면 안 된다 —
그 경계는 IAM이 아니라 이 파일 하나로 지킨다(§2 "IAM 한계").

안전 확인 에이전트는 쓰지 않는다 — 1회성 호출이라 이어갈 세션이 없다.
"""

import os
import uuid
from datetime import datetime, timezone
from enum import Enum

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel

_client: firestore.Client | None = None
_COLLECTION = "agent_sessions"


def get_client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(
            project=os.environ.get("GCP_PROJECT"),
            database=os.environ.get("FIRESTORE_DATABASE", "development"),
        )
    return _client


class AgentType(str, Enum):
    CLAIMANT = "CLAIMANT"
    EXECUTOR = "EXECUTOR"


class Turn(BaseModel):
    turn_id: str
    ts: datetime
    role: str  # "INPUT" | "OUTPUT"
    content: str
    untrusted: bool = False
    doc_refs: list[str] = []


class AgentSession(BaseModel):
    session_id: str
    agent_type: AgentType
    entity_id: str
    org_id: str = ""  # 기존(멀티테넌시 이전) 문서엔 없는 필드라 빈 문자열로 흡수한다
    actor_ref: str | None = None
    status: str = "ACTIVE"  # "ACTIVE" | "CLOSED"
    turns: list[Turn] = []
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


def session_id_for(agent_type: AgentType, entity_id: str) -> str:
    return f"{agent_type.value}__{entity_id}"


def get_or_create_session(
    agent_type: AgentType, entity_id: str, actor_ref: str | None = None, org_id: str = ""
) -> AgentSession:
    """entity_id(청구자는 claim_request_id, 집행자는 settlement_run_id)의 진행 중
    세션을 읽어온다. 없으면 메모리상으로만 새로 만든다 — 실제 Firestore 쓰기는
    첫 append_turn 호출 때 일어난다. 같은 entity_id로 반복 호출되는 것 자체가
    "대화를 이어가는 상황"의 정의다 (schema-contract.md §9).

    org_id는 신규 세션 생성 시에만 반영한다 — 기존 세션은 조회로 이어받으므로
    재기입할 필요가 없다."""
    doc_id = session_id_for(agent_type, entity_id)
    doc = get_client().collection(_COLLECTION).document(doc_id).get()
    if doc.exists:
        return AgentSession.model_validate(doc.to_dict())
    now = datetime.now(timezone.utc)
    return AgentSession(
        session_id=doc_id,
        agent_type=agent_type,
        entity_id=entity_id,
        org_id=org_id,
        actor_ref=actor_ref,
        created_at=now,
        updated_at=now,
    )


def append_turn(
    session: AgentSession,
    *,
    role: str,
    content: str,
    untrusted: bool = False,
    doc_refs: list[str] | None = None,
) -> AgentSession:
    """세션에 턴을 추가하고 즉시 Firestore에 반영한다. content는 <untrusted_*> 같은
    래핑을 벗기지 않고 원문 그대로 저장한다 — 압축·요약은 이 함수가 아니라
    close_session이 닫힌 세션에 한해 코드로 만든다."""
    turn = Turn(
        turn_id=str(uuid.uuid4()),
        ts=datetime.now(timezone.utc),
        role=role,
        content=content,
        untrusted=untrusted,
        doc_refs=doc_refs or [],
    )
    session.turns.append(turn)
    session.updated_at = turn.ts
    get_client().collection(_COLLECTION).document(session.session_id).set(
        session.model_dump(mode="json")
    )
    return session


def close_session(session: AgentSession) -> AgentSession:
    """세션을 CLOSED로 전환하고 결정론적 요약을 생성한다. 요약은 LLM이 아니라
    코드가 만든다(§2 "요약은 코드가 만든다") — 금액은 절대 넣지 않고 턴 수와
    관련 문서 ID만 남긴다."""
    doc_refs = sorted({ref for turn in session.turns for ref in turn.doc_refs})
    session.summary = (
        f"{len(session.turns)}턴, 관련 문서 {doc_refs}, 상태 CLOSED"
        if doc_refs
        else f"{len(session.turns)}턴, 상태 CLOSED"
    )
    session.status = "CLOSED"
    session.updated_at = datetime.now(timezone.utc)
    get_client().collection(_COLLECTION).document(session.session_id).set(
        session.model_dump(mode="json")
    )
    return session


def find_prior_session_summary(
    agent_type: AgentType, actor_ref: str | None, exclude_entity_id: str, org_id: str
) -> str | None:
    """같은 org_id·actor_ref(예: recipient_id)로 이미 닫힌 세션 중 가장 최근 것의
    요약을 찾는다. "새 세션엔 이전 세션 요약이 들어간다"는 요구사항의 구현체.

    org_id 필터가 없으면 actor_ref(예: 이메일)가 조직 간에 우연히 겹칠 때 다른
    조직의 세션 요약이 새어나갈 수 있다 — tiered-memory-review.html §7."""
    if not actor_ref:
        return None
    docs = (
        get_client()
        .collection(_COLLECTION)
        .where(filter=FieldFilter("agent_type", "==", agent_type.value))
        .where(filter=FieldFilter("org_id", "==", org_id))
        .where(filter=FieldFilter("actor_ref", "==", actor_ref))
        .where(filter=FieldFilter("status", "==", "CLOSED"))
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(5)
        .stream()
    )
    for doc in docs:
        data = doc.to_dict()
        if data.get("entity_id") != exclude_entity_id:
            return data.get("summary")
    return None


def fetch_full_session(session_id: str) -> AgentSession | None:
    """과거 세션의 턴 원문 전체를 불러온다. `fetch_full_session_history` 툴이 이
    함수를 감싼다 — 에이전트가 요약만으로 부족할 때 호출한다."""
    doc = get_client().collection(_COLLECTION).document(session_id).get()
    return AgentSession.model_validate(doc.to_dict()) if doc.exists else None
