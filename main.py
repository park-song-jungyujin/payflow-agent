"""architecture.md, schema-contract.md §9 — agent 서비스 진입점.

이 서비스는 비공개다(infra/iam.tf agent_invoker_api_only) — Cloud Tasks가 api SA로
만든 OIDC 토큰만 통과시킨다. 응답 본문은 의미가 없다: 결과는 agent_drafts에 쓰고
200만 돌려준다 — api가 draft를 읽어간다.

claimant/executor 라우트는 자리만 잡아둔다. 로직은 각 트랙(A/B) 담당이다.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.auth.transport import requests as google_requests  # noqa: E402
from google.genai import types  # noqa: E402
from google.oauth2 import id_token  # noqa: E402

from safety.agent import root_agent as safety_agent  # noqa: E402

app = FastAPI()
_google_request = google_requests.Request()

APP_NAME = "payflow-agent"


def _verify_oidc(authorization: str) -> None:
    """schema-contract.md §9 — api가 Cloud Tasks로 넘긴 OIDC 토큰만 통과시킨다.
    api/src/guards/oidc.py와 동일한 검증 방식이다."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.removeprefix("Bearer ")
    audience = os.environ["OIDC_AUDIENCE"]
    try:
        id_token.verify_oauth2_token(token, _google_request, audience=audience)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.get("/")
def health():
    return {"status": "ok"}


async def _run_once(agent, session_id: str, prompt: str) -> None:
    """세션은 매 요청마다 새로 만든다 — architecture.md: ADK 세션은
    InMemorySessionService, 재시작 후 살아남을 필요가 없다."""
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=APP_NAME, user_id="system", session_id=session_id
    )
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)

    content = types.Content(role="user", parts=[types.Part(text=prompt)])
    async for _event in runner.run_async(
        user_id="system", session_id=session_id, new_message=content
    ):
        pass  # 결과는 안전 확인 에이전트가 submit_risk_report 툴로 이미 api에 썼다.


@app.post("/agents/safety/report")
async def safety_report(body: dict, authorization: str = Header(default="")):
    _verify_oidc(authorization)

    run_id = body.get("settlement_run_id")
    task_id = body.get("task_id")
    snapshot = body.get("settlement_run_snapshot")
    if not run_id or not task_id:
        raise HTTPException(status_code=400, detail="settlement_run_id, task_id required")

    prompt = (
        f"settlement_run_id: {run_id!r}\n"
        f"task_id: {task_id!r}\n"
        f"settlement_run 스냅샷:\n{snapshot}\n\n"
        "위 내용을 검토해 리스크 리포트를 작성한 뒤, submit_risk_report 툴을 호출해 "
        f"제출하세요. settlement_run_id에는 {run_id!r}을, task_id에는 {task_id!r}을 "
        "그대로 넘기세요."
    )
    await _run_once(safety_agent, session_id=task_id, prompt=prompt)
    return {"status": "ok"}


@app.post("/agents/claimant/review")
def claimant_review(body: dict, authorization: str = Header(default="")):
    _verify_oidc(authorization)
    raise HTTPException(status_code=501, detail="claimant agent not implemented yet (Track A)")


@app.post("/agents/executor/analyze")
def executor_analyze(body: dict, authorization: str = Header(default="")):
    _verify_oidc(authorization)
    raise HTTPException(status_code=501, detail="executor agent not implemented yet (Track B)")
