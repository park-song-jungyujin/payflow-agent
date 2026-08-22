"""schema-contract.md §9 — 청구자 에이전트 입력의 `raw_text_gcs_uri`를 읽는다.

계약이 원문 자체가 아니라 URI를 주는 이유: Cloud Tasks 본문은 큐에 영속화되고
요청 로그에 남는다. 원문을 본문에 실으면 §2가 막아둔 유출 경로(감사 기록·draft의
reason을 타고 audit_logs로)가 다시 열린다.

**GCS SDK를 쓰지 않는다.** google-cloud-storage 의존성을 새로 넣지 않으려고
JSON API를 requests로 직접 부른다 — 인증은 shared/api_client.py와 같은
google.auth 기본 자격증명(Cloud Run에서는 agent SA)이다. 권한은
`raw_text/` 프리픽스 한정 objectViewer다(payflow-backend infra/storage.tf).

architecture.md의 "Firestore SDK 직접 쓰기 금지"와 충돌하지 않는다 — 그건
Firestore 얘기이고, §9이 이 URI 읽기를 전제한다.
"""

from urllib.parse import quote

import google.auth
import google.auth.transport.requests
import requests


class ReceiptTextUnavailable(RuntimeError):
    """원문을 못 읽었다. 판단 자체를 막지는 않는다 — 호출부가 구조화 필드만으로
    진행할지 정한다."""


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def fetch_raw_text(gcs_uri: str, *, max_chars: int = 20000) -> str:
    """`gs://bucket/key`를 읽어 문자열로 돌려준다.

    max_chars로 자른다 — 원문 길이는 영수증이 정하므로(비신뢰 입력) 프롬프트가
    무한정 커지는 걸 막는다.
    """
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        raise ReceiptTextUnavailable(f"not a gs:// uri: {gcs_uri!r}")

    bucket, _, key = gcs_uri.removeprefix("gs://").partition("/")
    if not bucket or not key:
        raise ReceiptTextUnavailable(f"malformed gs:// uri: {gcs_uri!r}")

    # 오브젝트 키의 `/`도 인코딩해야 한다 — JSON API는 키 전체를 경로 한 조각으로 받는다.
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(key, safe='')}?alt=media"
    try:
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=10
        )
        resp.raise_for_status()
    except Exception as e:
        raise ReceiptTextUnavailable(str(e)) from e

    resp.encoding = "utf-8"
    return resp.text[:max_chars]
