"""agent-session-memory-v2-design.md §1 — 기존 단일 agent_sessions 컬렉션을
org별 agent_sessions__{org_id} 컬렉션으로 복사한다. 원본은 삭제하지 않는다
(롤백·검증용 보존). 멱등 — 두 번 실행해도 안전하다.

실행: `uv run python -m scripts.migrate_agent_sessions_v2 --execute`
기본은 dry-run(--execute 없으면 아무것도 쓰지 않고 계획만 출력)."""

import argparse

from shared.memory import _COLLECTION_PREFIX, get_client


def target_collection_for(doc: dict) -> str:
    org_id = doc.get("org_id") or ""
    return f"{_COLLECTION_PREFIX}__{org_id}" if org_id else f"{_COLLECTION_PREFIX}__unknown"


def should_skip(existing: dict | None, source: dict) -> bool:
    if existing is None:
        return False
    return existing.get("updated_at") >= source.get("updated_at")


def migrate(dry_run: bool = True) -> dict:
    client = get_client()
    plan = {"copied": 0, "skipped": 0}
    for doc in client.collection(_COLLECTION_PREFIX).stream():
        source = doc.to_dict()
        target_name = target_collection_for(source)
        target_ref = client.collection(target_name).document(doc.id)
        existing = target_ref.get()
        existing_data = existing.to_dict() if existing.exists else None
        if should_skip(existing_data, source):
            plan["skipped"] += 1
            continue
        plan["copied"] += 1
        if not dry_run:
            target_ref.set(source)
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = migrate(dry_run=not args.execute)
    mode = "EXECUTED" if args.execute else "DRY-RUN"
    print(f"[{mode}] copied={result['copied']} skipped={result['skipped']}")
