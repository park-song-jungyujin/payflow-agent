from scripts.migrate_agent_sessions_v2 import should_skip, target_collection_for


def test_target_collection_uses_org_id():
    assert target_collection_for({"org_id": "org_1"}) == "agent_sessions__org_1"


def test_target_collection_falls_back_to_unknown():
    assert target_collection_for({"org_id": ""}) == "agent_sessions__unknown"
    assert target_collection_for({}) == "agent_sessions__unknown"


def test_should_skip_when_target_already_up_to_date():
    source = {"updated_at": "2026-08-25T00:00:00Z"}
    existing = {"updated_at": "2026-08-25T00:00:00Z"}
    assert should_skip(existing, source) is True


def test_should_skip_false_when_target_missing():
    assert should_skip(None, {"updated_at": "2026-08-25T00:00:00Z"}) is False


def test_should_skip_false_when_source_is_newer():
    existing = {"updated_at": "2026-08-24T00:00:00Z"}
    source = {"updated_at": "2026-08-25T00:00:00Z"}
    assert should_skip(existing, source) is False
