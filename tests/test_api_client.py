"""architecture.md — agent가 api를 부르는 유일한 창구. 실제 네트워크/ADC 없이
요청 모양(URL, 헤더, 바디)과 에러 전파만 검증한다."""

import pytest

from shared import api_client


class FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._json_body


@pytest.fixture(autouse=True)
def _fake_id_token(monkeypatch):
    monkeypatch.setattr(api_client, "_fetch_id_token", lambda: "fake-id-token")


def test_write_agent_draft_posts_expected_body_and_auth_header(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse({"draft_id": "drf_1", "status": "ok"})

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    result = api_client.write_agent_draft(
        agent="EXECUTOR",
        target_type="SETTLEMENT_RUN",
        target_id="run_1",
        task_id="task_1",
        payload={"anomalies": []},
    )

    assert result == {"draft_id": "drf_1", "status": "ok"}
    assert captured["url"] == "https://api.test.invalid/agents/drafts"
    assert captured["headers"] == {"Authorization": "Bearer fake-id-token"}
    assert captured["json"] == {
        "agent": "EXECUTOR",
        "target_type": "SETTLEMENT_RUN",
        "target_id": "run_1",
        "task_id": "task_1",
        "payload": {"anomalies": []},
    }


def test_record_tool_call_audit_posts_to_audit_path(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api_client.requests,
        "post",
        lambda url, json, headers, timeout: captured.update(url=url, json=json)
        or FakeResponse({"status": "ok"}),
    )

    api_client.record_tool_call_audit(
        agent="EXECUTOR", action="TOOL_CALL_REJECTED", run_id="run_1", reason="cap exceeded"
    )

    assert captured["url"] == "https://api.test.invalid/agents/audit"
    assert captured["json"] == {
        "agent": "EXECUTOR",
        "action": "TOOL_CALL_REJECTED",
        "run_id": "run_1",
        "reason": "cap exceeded",
    }


def test_base_url_trailing_slash_is_stripped(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://api.test.invalid/")
    captured = {}
    monkeypatch.setattr(
        api_client.requests,
        "post",
        lambda url, json, headers, timeout: captured.update(url=url) or FakeResponse({}),
    )

    api_client.record_tool_call_audit(agent="EXECUTOR", action="TOOL_CALL_STARTED")

    assert captured["url"] == "https://api.test.invalid/agents/audit"


def test_http_error_propagates(monkeypatch):
    monkeypatch.setattr(
        api_client.requests,
        "post",
        lambda url, json, headers, timeout: FakeResponse({"detail": "bad request"}, status_code=400),
    )

    with pytest.raises(RuntimeError):
        api_client.write_agent_draft(
            agent="EXECUTOR",
            target_type="SETTLEMENT_RUN",
            target_id="run_1",
            task_id="task_1",
            payload={},
        )
