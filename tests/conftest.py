"""테스트 전역 환경변수 스텁. 실제 GCP·Vertex·api에 붙지 않는다."""

import os

import pytest

# main.py가 모듈 임포트 시점에 claimant/executor agent(LlmAgent 생성 — AGENT_MODEL
# 필요)를 끌어온다. pytest는 fixture보다 먼저 테스트 모듈을 import하므로, 여기서
# 미리 값을 심어둔다 — 아래 autouse fixture와 별개로 collection 단계에 필요하다.
os.environ.setdefault("AGENT_MODEL", "gemini-test")
os.environ.setdefault("OIDC_AUDIENCE", "https://agent.test.invalid")
os.environ.setdefault("API_OIDC_AUDIENCE", "https://api.test.invalid")
os.environ.setdefault("API_BASE_URL", "https://api.test.invalid")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_OIDC_AUDIENCE", "https://api.test.invalid")
    monkeypatch.setenv("API_BASE_URL", "https://api.test.invalid")
    monkeypatch.setenv("AGENT_MODEL", "gemini-test")
    monkeypatch.setenv("OIDC_AUDIENCE", "https://agent.test.invalid")
    monkeypatch.setenv("GCP_PROJECT", "payflow-test")
    monkeypatch.setenv("FIRESTORE_DATABASE", "development")
    monkeypatch.setenv("AGENT_TOOL_MAX_CALLS_PER_SESSION", "3")
    yield
