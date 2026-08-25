"""schema-contract.md §2 agent_sessions — 청구자/집행자 세션 이어가기의 유일한 창구.
tests/ingest/test_store.py(backend)와 같은 fake Firestore 전략을 쓴다."""

import pytest

from shared import memory


class FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data else None


class FakeDocRef:
    def __init__(self, store_dict, doc_id):
        self._store, self.id = store_dict, doc_id

    def get(self):
        return FakeSnapshot(self._store.get(self.id))

    def set(self, data, merge=False):
        if merge and self.id in self._store:
            self._store[self.id] = {**self._store[self.id], **data}
        else:
            self._store[self.id] = data


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs
        self._filters = []
        self._order = None
        self._limit = None
        self._vector_field = None
        self._query_vector = None

    def where(self, filter=None):
        self._filters.append((filter.field_path, filter.value))
        return self

    def order_by(self, field, direction=None):
        self._order = (field, direction)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def find_nearest(self, vector_field, query_vector, limit, distance_measure=None, **kw):
        self._vector_field = vector_field
        self._query_vector = list(query_vector)
        self._limit = limit
        return self

    def stream(self):
        hits = [d for d in self._docs if all(d.get(f) == v for f, v in self._filters)]
        if self._vector_field:
            def cosine(vec):
                a, b = list(vec), self._query_vector
                dot = sum(x * y for x, y in zip(a, b))
                na = sum(x * x for x in a) ** 0.5
                nb = sum(x * x for x in b) ** 0.5
                return dot / (na * nb) if na and nb else 0.0

            hits = [d for d in hits if d.get(self._vector_field) is not None]
            hits.sort(key=lambda d: cosine(d[self._vector_field]), reverse=True)
        elif self._order:
            hits = sorted(hits, key=lambda d: d[self._order[0]], reverse=True)
        return iter([FakeSnapshot(d) for d in (hits[: self._limit] if self._limit else hits)])


class FakeCollection:
    def __init__(self, store_dict):
        self._store = store_dict

    def document(self, doc_id):
        return FakeDocRef(self._store, doc_id)

    def where(self, filter=None):
        return FakeQuery(list(self._store.values())).where(filter=filter)


class FakeClient:
    def __init__(self):
        self.data = {}

    def collection(self, name):
        return FakeCollection(self.data.setdefault(name, {}))


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(memory, "get_client", lambda: client)
    return client


def test_session_id_is_deterministic_per_agent_and_entity():
    assert memory.session_id_for(memory.AgentType.CLAIMANT, "clm_req_1") == "CLAIMANT__clm_req_1"


def test_get_or_create_session_creates_new_when_absent(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    assert session.status == "ACTIVE"
    assert session.turns == []
    assert "CLAIMANT__clm_req_1" not in fake.data["agent_sessions__unknown"]  # 아직 안 썼다


def test_get_or_create_session_stamps_org_id_on_new_session(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1")
    assert session.org_id == "org_1"


def test_get_or_create_session_with_different_org_id_does_not_reuse_other_partition(fake):
    """v2 파티셔닝 — org_id는 컬렉션을 고르므로, 다른 org_id로 조회하면
    남의 세션을 이어받는 대신 자기 파티션에 새 세션이 생긴다."""
    original = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1")
    memory.append_turn(original, role="INPUT", content="첫 턴")

    other = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_2")

    assert other.org_id == "org_2"
    assert other.turns == []
    assert fake.data["agent_sessions__org_1"]["CLAIMANT__clm_req_1"]["org_id"] == "org_1"


def test_get_or_create_session_returns_existing_when_present(fake):
    existing = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    memory.append_turn(existing, role="INPUT", content="첫 턴")

    reloaded = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")

    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].content == "첫 턴"


def test_append_turn_persists_immediately(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    memory.append_turn(session, role="INPUT", content="영수증 검토 요청", untrusted=True)

    doc = fake.data["agent_sessions__unknown"]["CLAIMANT__clm_req_1"]
    assert len(doc["turns"]) == 1
    assert doc["turns"][0]["untrusted"] is True


def test_append_turn_writes_to_org_partitioned_collection(fake):
    session = memory.get_or_create_session(
        memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1"
    )
    memory.append_turn(session, role="INPUT", content="첫 턴")

    assert "CLAIMANT__clm_req_1" in fake.data["agent_sessions__org_1"]
    assert "agent_sessions" not in fake.data or fake.data["agent_sessions"] == {}


def test_append_turn_without_org_id_uses_unknown_partition(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_2")
    memory.append_turn(session, role="INPUT", content="첫 턴")

    assert "CLAIMANT__clm_req_2" in fake.data["agent_sessions__unknown"]


def test_close_session_summary_never_contains_amounts(fake):
    """money-safety.md — 요약은 코드가 만들고 금액은 절대 넣지 않는다."""
    session = memory.get_or_create_session(memory.AgentType.EXECUTOR, "run_1")
    memory.append_turn(session, role="OUTPUT", content="총 12,345원 지급 예정", doc_refs=["rct_1"])

    closed = memory.close_session(session)

    assert closed.status == "CLOSED"
    assert "12,345" not in closed.summary
    assert "rct_1" in closed.summary


def test_close_session_summary_without_doc_refs_omits_them(fake):
    session = memory.get_or_create_session(memory.AgentType.EXECUTOR, "run_1")
    memory.append_turn(session, role="OUTPUT", content="분석 완료")

    closed = memory.close_session(session)

    assert "관련 문서" not in closed.summary
    assert "1턴" in closed.summary


def test_find_prior_session_summary_excludes_current_entity(fake):
    """같은 actor_ref라도 지금 처리 중인 entity_id 자신의 과거 종료 세션은 제외해야
    한다 — 아니면 자기 자신을 "이전 세션"으로 착각한다."""
    s1 = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_old", actor_ref="rcp_1", org_id="org_1"
    )
    memory.append_turn(s1, role="OUTPUT", content="이전 분석")
    memory.close_session(s1)

    s2 = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_new", actor_ref="rcp_1", org_id="org_1"
    )
    memory.append_turn(s2, role="OUTPUT", content="같은 run의 재시도")
    memory.close_session(s2)

    result = memory.find_prior_session_summary(
        memory.AgentType.EXECUTOR, actor_ref="rcp_1", exclude_entity_id="run_new", org_id="org_1"
    )

    assert result == s1.summary  # run_new(자기 자신)는 후보에서 빠진다


def test_find_prior_session_summary_returns_none_without_actor_ref(fake):
    assert (
        memory.find_prior_session_summary(memory.AgentType.EXECUTOR, None, "run_1", org_id="org_1")
        is None
    )


def test_find_prior_session_summary_skips_open_sessions(fake):
    """status=CLOSED 필터 — 아직 진행 중인 세션은 후보가 아니다."""
    session = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_open", actor_ref="rcp_1", org_id="org_1"
    )
    memory.append_turn(session, role="OUTPUT", content="진행 중")  # close 안 함

    result = memory.find_prior_session_summary(
        memory.AgentType.EXECUTOR, actor_ref="rcp_1", exclude_entity_id="run_new", org_id="org_1"
    )

    assert result is None


def test_find_prior_session_summary_does_not_leak_across_orgs(fake):
    """tiered-memory-review.html §7 — 서로 다른 조직이 같은 actor_ref(예: 이메일
    재사용)를 갖더라도, org_id가 다르면 상대 조직의 세션 요약을 돌려주면 안 된다."""
    other_org = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_other_org", actor_ref="shared@example.com", org_id="org_A"
    )
    memory.append_turn(other_org, role="OUTPUT", content="org_A의 분석 내용")
    memory.close_session(other_org)

    result = memory.find_prior_session_summary(
        memory.AgentType.EXECUTOR,
        actor_ref="shared@example.com",
        exclude_entity_id="run_new",
        org_id="org_B",
    )

    assert result is None


def test_close_session_stores_summary_embedding(fake, monkeypatch):
    monkeypatch.setattr(memory, "_embed_text", lambda text: [0.1, 0.2, 0.3])
    session = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_1", org_id="org_1"
    )
    memory.append_turn(session, role="OUTPUT", content="분석 완료")

    memory.close_session(session)

    stored = fake.data["agent_sessions__org_1"]["EXECUTOR__run_1"]
    assert list(stored["summary_embedding"]) == [0.1, 0.2, 0.3]


def test_close_session_survives_embedding_failure(fake, monkeypatch):
    monkeypatch.setattr(memory, "_embed_text", lambda text: None)
    session = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_2", org_id="org_1"
    )
    memory.append_turn(session, role="OUTPUT", content="분석 완료")

    closed = memory.close_session(session)

    assert closed.status == "CLOSED"
    stored = fake.data["agent_sessions__org_1"]["EXECUTOR__run_2"]
    assert "summary_embedding" not in stored


def test_append_turn_preserves_summary_embedding_on_reopened_session(fake, monkeypatch):
    """close_session이 저장한 summary_embedding은 AgentSession 모델 필드가 아니라서
    model_dump에 실리지 않는다. append_turn이 merge=True 없이 .set()하면 이후
    턴을 추가할 때(예: 닫힌 세션 재사용) 임베딩이 조용히 삭제된다."""
    monkeypatch.setattr(memory, "_embed_text", lambda text: [0.1, 0.2, 0.3])
    session = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_1", org_id="org_1"
    )
    memory.append_turn(session, role="OUTPUT", content="분석 완료")
    memory.close_session(session)

    memory.append_turn(session, role="INPUT", content="추가 턴")

    stored = fake.data["agent_sessions__org_1"]["EXECUTOR__run_1"]
    assert list(stored["summary_embedding"]) == [0.1, 0.2, 0.3]


def test_fetch_full_session_requires_org_id_to_locate_partition(fake):
    session = memory.get_or_create_session(
        memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1"
    )
    memory.append_turn(session, role="INPUT", content="원문")

    fetched = memory.fetch_full_session(session.session_id, org_id="org_1")

    assert fetched.turns[0].content == "원문"
    assert memory.fetch_full_session(session.session_id, org_id="org_2") is None


def test_fetch_full_session_returns_none_when_missing(fake):
    assert memory.fetch_full_session("CLAIMANT__nope", org_id="org_1") is None


def test_fetch_full_session_round_trips(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    memory.append_turn(session, role="INPUT", content="원문")

    fetched = memory.fetch_full_session(session.session_id, org_id="")

    assert fetched.turns[0].content == "원문"


def test_find_similar_sessions_ranks_by_cosine_similarity(fake, monkeypatch):
    embeddings = {
        "run_a": [1.0, 0.0],
        "run_b": [0.0, 1.0],
        "query": [0.9, 0.1],
    }
    monkeypatch.setattr(
        memory, "_embed_text", lambda text: next(v for k, v in embeddings.items() if k in text)
    )

    for entity_id in ("run_a", "run_b"):
        s = memory.get_or_create_session(
            memory.AgentType.EXECUTOR, entity_id, org_id="org_1"
        )
        memory.append_turn(s, role="OUTPUT", content="분석", doc_refs=[entity_id])
        memory.close_session(s)

    result = memory.find_similar_sessions(
        memory.AgentType.EXECUTOR, org_id="org_1", query_text="query",
        exclude_entity_id="run_new", limit=2,
    )

    assert result[0] == memory.get_client().collection("agent_sessions__org_1").document(
        "EXECUTOR__run_a"
    ).get().to_dict()["summary"]


def test_find_similar_sessions_excludes_self_and_other_orgs(fake, monkeypatch):
    monkeypatch.setattr(memory, "_embed_text", lambda text: [1.0, 0.0])

    same_entity = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_self", org_id="org_1"
    )
    memory.append_turn(same_entity, role="OUTPUT", content="분석")
    memory.close_session(same_entity)

    other_org = memory.get_or_create_session(
        memory.AgentType.EXECUTOR, "run_other", org_id="org_2"
    )
    memory.append_turn(other_org, role="OUTPUT", content="분석")
    memory.close_session(other_org)

    result = memory.find_similar_sessions(
        memory.AgentType.EXECUTOR, org_id="org_1", query_text="q",
        exclude_entity_id="run_self", limit=5,
    )

    assert result == []


def test_find_similar_sessions_returns_empty_when_embedding_fails(fake, monkeypatch):
    monkeypatch.setattr(memory, "_embed_text", lambda text: None)

    result = memory.find_similar_sessions(
        memory.AgentType.EXECUTOR, org_id="org_1", query_text="q",
        exclude_entity_id="run_x",
    )

    assert result == []


def test_find_similar_sessions_returns_empty_when_firestore_query_fails(fake, monkeypatch):
    """find_nearest 대상 벡터 인덱스가 아직 없는 org에서 Firestore가
    FAILED_PRECONDITION 등을 던져도 find_similar_sessions는 예외를 삼키고
    빈 리스트로 안전하게 내려가야 한다."""
    monkeypatch.setattr(memory, "_embed_text", lambda text: [1.0, 0.0])

    class BoomQuery:
        def where(self, filter=None):
            return self

        def find_nearest(self, **kw):
            raise RuntimeError("FAILED_PRECONDITION: no matching vector index")

    monkeypatch.setattr(memory, "get_client", lambda: type(
        "C", (), {"collection": lambda self, name: BoomQuery()}
    )())

    result = memory.find_similar_sessions(
        memory.AgentType.EXECUTOR, org_id="org_1", query_text="q",
        exclude_entity_id="run_x",
    )

    assert result == []


# --- 사건 특징 추출 및 의미 공간 일치화 (PR #10 피드백) --------------------------


def test_extract_claimant_features_pulls_merchant_category_and_anomalies():
    snapshot = {
        "merchant_name": "스타벅스",
        "account_category_code": "EMPLOYEE_BENEFIT",
        "currency": "KRW",
        "parsed_amount_minor": 50000,
        "transaction_date": "2026-08-25",
        "parse_confidence": 0.9,
    }

    features = memory.extract_claimant_features(snapshot)

    assert features["merchant_name"] == "스타벅스"
    assert features["category"] == "복리후생비"
    assert features["currency"] == "KRW"
    assert features["anomalies"] == ["정상 파싱"]


def test_extract_claimant_features_flags_anomalies_without_amounts():
    snapshot = {
        "merchant_name": "",
        "account_category_code": "UNCLASSIFIED",
        "currency": "",
        "parsed_amount_minor": None,
        "transaction_date": "",
        "parse_confidence": 0.3,
    }

    features = memory.extract_claimant_features(snapshot)

    assert features["merchant_name"] == ""
    assert features["category"] == "미분류"
    assert "금액 미기재" in features["anomalies"]
    assert "거래일자 미기재" in features["anomalies"]
    assert "저신뢰도 파싱" in features["anomalies"]
    assert "미분류 계정과목" in features["anomalies"]


def test_extract_executor_features_aggregates_merchants_categories_and_anomalies():
    candidate_claims = [
        {
            "merchant_name": "스타벅스",
            "account_category_code": "EMPLOYEE_BENEFIT",
            "currency": "KRW",
            "transaction_date": "2026-08-25",
        },
        {
            "merchant_name": "카페베네",
            "account_category_code": "SUPPLIES",
            "currency": "KRW",
            "transaction_date": "2026-09-10",  # 미래 거래일
        },
    ]

    features = memory.extract_executor_features(
        candidate_claims,
        duplicate_groups=[{"claim_ids": ["a", "b"]}],
        exact_duplicate_groups=[{"claim_ids": ["c", "d"]}],
    )

    assert features["merchants"] == ["스타벅스", "카페베네"]
    assert features["categories"] == ["복리후생비", "소모품비"]  # sorted() 가나다순
    assert features["currencies"] == ["KRW"]
    assert "영수증 고유번호 중복" in features["anomalies"]
    assert "중복 청구 의심" in features["anomalies"]
    assert "미래 거래일" in features["anomalies"]
    # 다중 통화 혼재는 아님
    assert "다중 통화 혼재" not in features["anomalies"]


def test_extract_executor_features_flags_multi_currency_and_no_anomaly_baseline():
    candidate_claims = [
        {"merchant_name": "A", "account_category_code": "TRAVEL", "currency": "KRW", "transaction_date": "2026-08-25"},
        {"merchant_name": "B", "account_category_code": "TRAVEL", "currency": "USD", "transaction_date": "2026-08-25"},
    ]

    features = memory.extract_executor_features(candidate_claims)

    assert "다중 통화 혼재" in features["anomalies"]
    # 아무 이상도 없으면 baseline 문구로 대체 — 빈 리스트가 되면 안 된다
    clean = memory.extract_executor_features(
        [{"merchant_name": "A", "account_category_code": "TRAVEL", "currency": "KRW", "transaction_date": "2026-08-25"}]
    )
    assert clean["anomalies"] == ["이상 없음"]


def test_format_case_features_uses_same_template_for_claimant_query_and_summary():
    """과거 사건 요약(close_session)과 현재 사건 검색 쿼리가 동일한 포맷터를
    거치므로 같은 의미 공간 텍스트가 된다."""
    features = {
        "merchant_name": "스타벅스",
        "category": "복리후생비",
        "anomalies": ["저신뢰도 파싱"],
    }

    summary_text = memory.format_case_features(memory.AgentType.CLAIMANT, features)
    query_text = memory.format_case_features(memory.AgentType.CLAIMANT, features)

    assert summary_text == query_text
    assert "가맹점: 스타벅스" in summary_text
    assert "카테고리: 복리후생비" in summary_text
    assert "이상유형: 저신뢰도 파싱" in summary_text


def test_format_case_features_executor_joins_multi_merchants():
    features = {
        "merchants": ["스타벅스", "카페베네"],
        "categories": ["소모품비", "복리후생비"],
        "anomalies": ["미래 거래일", "영수증 고유번호 중복"],
    }

    text = memory.format_case_features(memory.AgentType.EXECUTOR, features)

    assert "가맹점: 스타벅스, 카페베네" in text
    assert "카테고리: 소모품비, 복리후생비" in text
    assert "이상유형: 미래 거래일, 영수증 고유번호 중복" in text


def test_close_session_summary_includes_case_features_when_present(fake):
    """close_session이 case_features를 포함해 요약을 만든다 — 과거 사건 임베딩이
    'N턴, 관련 문서 [...]' 대신 사건 특징 텍스트를 담게 된다."""
    session = memory.get_or_create_session(
        memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1"
    )
    session.case_features = {
        "merchant_name": "스타벅스",
        "category": "복리후생비",
        "anomalies": ["정상 파싱"],
    }
    memory.append_turn(session, role="OUTPUT", content="검토 완료", doc_refs=["rct_1"])

    closed = memory.close_session(session)

    assert "가맹점: 스타벅스" in closed.summary
    assert "카테고리: 복리후생비" in closed.summary
    assert "관련 문서" in closed.summary  # doc_refs도 여전히 남는다
    assert "상태 CLOSED" in closed.summary


def test_close_session_summary_never_leaks_amounts_even_with_features(fake):
    """money-safety.md — case_features에 금액을 넣지 않는 한 요약에 금액이
    새어나가면 안 된다. 턴 content에 숫자가 있어도 summary는 코드가 만들므로
    그 숫자가 들어가지 않는다."""
    session = memory.get_or_create_session(
        memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1"
    )
    session.case_features = {
        "merchant_name": "스타벅스",
        "category": "복리후생비",
        "anomalies": ["정상 파싱"],
    }
    memory.append_turn(session, role="OUTPUT", content="12,345원 지급 예정")

    closed = memory.close_session(session)

    assert "12,345" not in closed.summary


def test_close_session_summary_falls_back_to_legacy_format_without_case_features(fake):
    """case_features가 없으면 기존 포맷으로 안전하게 폴백한다 — 기존 세션/기존 테스트 호환."""
    session = memory.get_or_create_session(memory.AgentType.EXECUTOR, "run_1")
    memory.append_turn(session, role="OUTPUT", content="분석", doc_refs=["rct_1"])

    closed = memory.close_session(session)

    assert "가맹점:" not in closed.summary
    assert "카테고리:" not in closed.summary
    assert "rct_1" in closed.summary


def test_find_similar_sessions_surfaces_same_feature_space_past_case(fake, monkeypatch):
    """과거 사건 요약(close_session이 case_features 기반으로 생성)과 현재 쿼리가
    같은 format_case_features 포맷터를 거치면, 같은 의미 공간에서 cosine
    similarity 랭킹이 성립한다. 서로 다른 특징의 과거 사건보다 같은 특징의
    과거 사건이 먼저 랭크되어야 한다."""
    # 임베딩 텍스트에 특징 키워드가 들어가면 그 키워드 방향 벡터를 준다.
    # "스타벅스"가 들어간 텍스트는 [1, 0], "편의점"이 들어간 텍스트는 [0, 1],
    # 둘 다 아니면(기타) [0, 0].
    def fake_embed(text):
        if "스타벅스" in text:
            return [1.0, 0.0]
        if "편의점" in text:
            return [0.0, 1.0]
        return [0.0, 0.0]

    monkeypatch.setattr(memory, "_embed_text", fake_embed)

    # 과거 사건 1: 스타벅스 — 현재 사건과 같은 특징 공간
    past_star = memory.get_or_create_session(
        memory.AgentType.CLAIMANT, "clm_past_star", org_id="org_1"
    )
    past_star.case_features = {
        "merchant_name": "스타벅스",
        "category": "복리후생비",
        "anomalies": ["정상 파싱"],
    }
    memory.append_turn(past_star, role="OUTPUT", content="과거 스타벅스 사건")
    memory.close_session(past_star)

    # 과거 사건 2: 편의점 — 다른 특징 공간
    past_conv = memory.get_or_create_session(
        memory.AgentType.CLAIMANT, "clm_past_conv", org_id="org_1"
    )
    past_conv.case_features = {
        "merchant_name": "편의점",
        "category": "소모품비",
        "anomalies": ["정상 파싱"],
    }
    memory.append_turn(past_conv, role="OUTPUT", content="과거 편의점 사건")
    memory.close_session(past_conv)

    # 현재 사건: 스타벅스 — 같은 특징 공간의 과거 사건이 먼저 와야 한다
    query_features = {
        "merchant_name": "스타벅스",
        "category": "복리후생비",
        "anomalies": ["정상 파싱"],
    }
    query_text = memory.format_case_features(memory.AgentType.CLAIMANT, query_features)

    results = memory.find_similar_sessions(
        memory.AgentType.CLAIMANT,
        org_id="org_1",
        query_text=query_text,
        exclude_entity_id="clm_new",
        limit=2,
    )

    assert len(results) == 2
    # 스타벅스 과거 사건 요약이 첫 번째(코사인 1.0), 편의점은 0.0
    assert "스타벅스" in results[0]
    assert "편의점" in results[1]
