"""schema-contract.md §2 `agent_sessions`, architecture.md "agent" 예외 — 청구자·
집행자 세션(대화) 이어가기의 유일한 창구. `agent` 서비스가 Firestore에 직접 쓰는
유일한 지점이다. 이 모듈이 `agent_sessions` 외 다른 컬렉션을 건드리면 안 된다 —
그 경계는 IAM이 아니라 이 파일 하나로 지킨다(§2 "IAM 한계").

안전 확인 에이전트는 쓰지 않는다 — 1회성 호출이라 이어갈 세션이 없다.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from enum import Enum

from google import genai
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from pydantic import BaseModel

_client: firestore.Client | None = None
_COLLECTION_PREFIX = "agent_sessions"
_log = logging.getLogger(__name__)


def _collection_name(org_id: str) -> str:
    return f"{_COLLECTION_PREFIX}__{org_id}" if org_id else f"{_COLLECTION_PREFIX}__unknown"


def get_client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(
            project=os.environ.get("GCP_PROJECT"),
            database=os.environ.get("FIRESTORE_DATABASE", "development"),
        )
    return _client


_EMBEDDING_MODEL_ENV = "AGENT_MEMORY_EMBEDDING_MODEL"
_DEFAULT_EMBEDDING_MODEL = "text-embedding-005"


def _embed_text(text: str) -> list[float] | None:
    """summary(코드 생성 텍스트)를 임베딩한다. 실패해도 세션 종료를 막지 않는다 —
    임베딩은 부가 기능이지 필수 경로가 아니다."""
    try:
        client = genai.Client()
        model = os.environ.get(_EMBEDDING_MODEL_ENV, _DEFAULT_EMBEDDING_MODEL)
        response = client.models.embed_content(model=model, contents=text)
        return list(response.embeddings[0].values)
    except Exception:
        _log.warning("agent_sessions 임베딩 실패 — 유사 세션 검색이 조용히 비활성화된다", exc_info=True)
        return None


class AgentType(str, Enum):
    CLAIMANT = "CLAIMANT"
    EXECUTOR = "EXECUTOR"


CATEGORY_DISPLAY: dict[str, str] = {
    "PAYMENT_FEE": "지급수수료",
    "EMPLOYEE_BENEFIT": "복리후생비",
    "TRAVEL": "여비교통비",
    "SUPPLIES": "소모품비",
    "ADVERTISING": "광고선전비",
    "RENT": "지급임차료",
    "UNCLASSIFIED": "미분류",
}


def extract_claimant_features(snapshot: dict) -> dict:
    """영수증 파싱 스냅샷에서 결정론적 사건 특징을 추출한다. 금액 숫자는 절대 포함하지 않는다."""
    merchant = snapshot.get("merchant_name") or ""
    cat_code = snapshot.get("account_category_code") or "UNCLASSIFIED"
    cat_display = CATEGORY_DISPLAY.get(cat_code, cat_code)
    currency = snapshot.get("currency") or ""
    anomalies = []
    if snapshot.get("parsed_amount_minor") is None:
        anomalies.append("금액 미기재")
    if not snapshot.get("transaction_date"):
        anomalies.append("거래일자 미기재")
    conf = snapshot.get("parse_confidence")
    if conf is not None and conf < 0.7:
        anomalies.append("저신뢰도 파싱")
    if cat_code == "UNCLASSIFIED":
        anomalies.append("미분류 계정과목")
    if not anomalies:
        anomalies.append("정상 파싱")
    return {
        "merchant_name": merchant,
        "category": cat_display,
        "currency": currency,
        "anomalies": anomalies,
    }


def extract_executor_features(
    candidate_claims: list[dict],
    duplicate_groups: list[dict] | None = None,
    exact_duplicate_groups: list[dict] | None = None,
) -> dict:
    """후보 claim 목록 및 중복 그룹에서 결정론적 사건 특징을 추출한다. 금액 숫자는 절대 포함하지 않는다."""
    merchants = sorted(
        {c["merchant_name"] for c in candidate_claims if c.get("merchant_name")}
    )
    categories = sorted(
        {
            CATEGORY_DISPLAY.get(
                c.get("account_category_code", ""), c.get("account_category_code", "")
            )
            for c in candidate_claims
            if c.get("account_category_code")
        }
    )
    currencies = sorted({c["currency"] for c in candidate_claims if c.get("currency")})
    anomalies = []
    if exact_duplicate_groups:
        anomalies.append("영수증 고유번호 중복")
    if duplicate_groups:
        anomalies.append("중복 청구 의심")
    today_iso = datetime.now(timezone.utc).date().isoformat()
    if any(c.get("transaction_date") and c["transaction_date"] > today_iso for c in candidate_claims):
        anomalies.append("미래 거래일")
    if len(currencies) > 1:
        anomalies.append("다중 통화 혼재")
    if not anomalies:
        anomalies.append("이상 없음")
    return {
        "merchants": merchants,
        "categories": categories,
        "currencies": currencies,
        "anomalies": anomalies,
    }


def format_case_features(agent_type: AgentType, features: dict) -> str:
    """사건 특징을 요약 및 벡터 검색 쿼리에서 공유할 표준 포맷 문자열로 변환한다."""
    parts = []
    if agent_type == AgentType.CLAIMANT:
        if features.get("merchant_name"):
            parts.append(f"가맹점: {features['merchant_name']}")
        if features.get("category"):
            parts.append(f"카테고리: {features['category']}")
        if features.get("anomalies"):
            anom_str = ", ".join(features["anomalies"])
            parts.append(f"이상유형: {anom_str}")
    elif agent_type == AgentType.EXECUTOR:
        if features.get("merchants"):
            parts.append(f"가맹점: {', '.join(features['merchants'])}")
        if features.get("categories"):
            parts.append(f"카테고리: {', '.join(features['categories'])}")
        if features.get("anomalies"):
            anom_str = ", ".join(features["anomalies"])
            parts.append(f"이상유형: {anom_str}")
    return ", ".join(parts)


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
    case_features: dict = {}
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
    doc = get_client().collection(_collection_name(org_id)).document(doc_id).get()
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
    close_session이 닫힌 세션에 한해 코드로 만든다.

    turns는 ArrayUnion으로 append한다 — 전체 문서를 set(merge=True)하면
    Firestore가 list 필드는 통째로 교체해버려, 동시에 두 호출이 append_turn을
    부르면 먼저 쓴 턴이 사라진다."""
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
    doc_data = session.model_dump(mode="json", exclude={"turns"})
    doc_data["turns"] = firestore.ArrayUnion([turn.model_dump(mode="json")])
    get_client().collection(_collection_name(session.org_id)).document(session.session_id).set(
        doc_data, merge=True
    )
    return session


def close_session(session: AgentSession) -> AgentSession:
    """세션을 CLOSED로 전환하고 결정론적 요약을 생성한다. 요약은 LLM이 아니라
    코드가 만든다(§2 "요약은 코드가 만든다") — 금액은 절대 넣지 않고 턴 수,
    코드 추출 사건 특징, 관련 문서 ID만 남긴다. 요약 임베딩은 있으면 같이
    저장하고, 실패해도 세션 종료는 계속 진행한다.

    이미 CLOSED인 세션이면 그대로 반환한다 — 재호출마다 요약을 다시 만들고
    임베딩 API를 다시 태우는 걸 막는다."""
    if session.status == "CLOSED":
        return session
    doc_refs = sorted({ref for turn in session.turns for ref in turn.doc_refs})
    features_str = ""
    if session.case_features:
        features_str = format_case_features(session.agent_type, session.case_features)

    summary_parts = [f"{len(session.turns)}턴"]
    if features_str:
        summary_parts.append(features_str)
    if doc_refs:
        summary_parts.append(f"관련 문서 {doc_refs}")
    summary_parts.append("상태 CLOSED")

    session.summary = ", ".join(summary_parts)
    session.status = "CLOSED"
    session.updated_at = datetime.now(timezone.utc)
    data = session.model_dump(mode="json")
    embedding = _embed_text(session.summary)
    if embedding is not None:
        data["summary_embedding"] = Vector(embedding)
    get_client().collection(_collection_name(session.org_id)).document(session.session_id).set(data)
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
    try:
        docs = (
            get_client()
            .collection(_collection_name(org_id))
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
    except Exception:
        # find_similar_sessions와 같은 폴백 — org별 복합 색인이 아직 없으면
        # FAILED_PRECONDITION이 나는데(신규 org 온보딩 시 흔함, 런북 §5),
        # 이 함수 하나 때문에 claimant_review/executor_analyze 엔드포인트
        # 전체가 500으로 죽으면 안 된다. 이전 세션 요약 없이 계속 진행한다.
        return None


def find_similar_sessions(
    agent_type: AgentType,
    org_id: str,
    query_text: str,
    exclude_entity_id: str,
    limit: int = 3,
) -> list[str]:
    """agent-session-memory-v2-design.md §3 — actor_ref가 달라도 같은 org 안에서
    의미상 유사한 과거 종료 세션을 찾는다. `summary`(코드 생성 텍스트)만
    반환한다 — 턴 원문은 절대 돌려주지 않는다. 임베딩 실패 시 빈 리스트."""
    embedding = _embed_text(query_text)
    if embedding is None:
        return []
    try:
        docs = (
            get_client()
            .collection(_collection_name(org_id))
            .where(filter=FieldFilter("agent_type", "==", agent_type.value))
            .where(filter=FieldFilter("status", "==", "CLOSED"))
            .find_nearest(
                vector_field="summary_embedding",
                query_vector=Vector(embedding),
                limit=limit + 1,
                distance_measure=DistanceMeasure.COSINE,
            )
            .stream()
        )
        results = []
        for doc in docs:
            data = doc.to_dict()
            if data.get("entity_id") == exclude_entity_id:
                continue
            if data.get("summary"):
                results.append(data["summary"])
            if len(results) == limit:
                break
        return results
    except Exception:
        return []


def fetch_full_session(session_id: str, org_id: str) -> AgentSession | None:
    """과거 세션의 턴 원문 전체를 불러온다. `fetch_full_session_history` 툴이 이
    함수를 감싼다 — 에이전트가 요약만으로 부족할 때 호출한다.

    org_id는 컬렉션명을 계산하는 데만 쓴다 — v2부터 세션이 org별로 파티셔닝돼
    있어 org_id 없이는 어느 컬렉션을 봐야 할지 알 수 없다."""
    doc = get_client().collection(_collection_name(org_id)).document(session_id).get()
    return AgentSession.model_validate(doc.to_dict()) if doc.exists else None
