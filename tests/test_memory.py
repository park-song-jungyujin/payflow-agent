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

    def set(self, data):
        self._store[self.id] = data


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs
        self._filters = []
        self._order = None
        self._limit = None

    def where(self, filter=None):
        self._filters.append((filter.field_path, filter.value))
        return self

    def order_by(self, field, direction=None):
        self._order = (field, direction)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def stream(self):
        hits = [
            d for d in self._docs if all(d.get(f) == v for f, v in self._filters)
        ]
        if self._order:
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
        self.data = {"agent_sessions": {}}

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
    assert "CLAIMANT__clm_req_1" not in fake.data["agent_sessions"]  # 아직 안 썼다


def test_get_or_create_session_stamps_org_id_on_new_session(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1")
    assert session.org_id == "org_1"


def test_get_or_create_session_keeps_existing_org_id_on_reload(fake):
    """org_id는 신규 생성 시에만 반영한다 — 기존 세션을 다시 조회할 때 다른
    org_id를 넘겨도 저장된 값이 이긴다."""
    original = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_1")
    memory.append_turn(original, role="INPUT", content="첫 턴")

    reloaded = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1", org_id="org_2")

    assert reloaded.org_id == "org_1"


def test_get_or_create_session_returns_existing_when_present(fake):
    existing = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    memory.append_turn(existing, role="INPUT", content="첫 턴")

    reloaded = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")

    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].content == "첫 턴"


def test_append_turn_persists_immediately(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    memory.append_turn(session, role="INPUT", content="영수증 검토 요청", untrusted=True)

    doc = fake.data["agent_sessions"]["CLAIMANT__clm_req_1"]
    assert len(doc["turns"]) == 1
    assert doc["turns"][0]["untrusted"] is True


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


def test_fetch_full_session_returns_none_when_missing(fake):
    assert memory.fetch_full_session("CLAIMANT__nope") is None


def test_fetch_full_session_round_trips(fake):
    session = memory.get_or_create_session(memory.AgentType.CLAIMANT, "clm_req_1")
    memory.append_turn(session, role="INPUT", content="원문")

    fetched = memory.fetch_full_session(session.session_id)

    assert fetched.turns[0].content == "원문"
