"""agent-tools.md, schema-contract.md §9 "세션 메모리" — 청구자·집행자 공용 툴.

진입점(main.py)이 호출 전에 이미 현재 세션의 전체 턴 히스토리와 이전 세션 요약을
프롬프트에 얹어 놓는다. 이 툴은 그걸로 부족할 때, 즉 과거 세션의 턴 원문 전체가
필요할 때만 쓰는 선택 경로다.
"""

from .memory import fetch_full_session


def fetch_full_session_history(session_id: str) -> dict:
    """과거 세션(session_id)의 턴 전체를 원문 그대로 불러온다. 프롬프트에 이미 얹힌
    요약만으로 판단이 서지 않을 때만 호출한다 — 예: "이전 세션에서 정확히 뭐라고
    답했는지"가 필요한 경우.

    session_id: 프롬프트의 "이전 세션 요약"과 함께 주어지는 과거 세션의 doc ID.
    """
    session = fetch_full_session(session_id)
    if session is None:
        return {"status": "error", "detail": "session not found"}
    return {
        "status": "ok",
        "turns": [
            {"role": t.role, "content": t.content, "untrusted": t.untrusted}
            for t in session.turns
        ],
    }
