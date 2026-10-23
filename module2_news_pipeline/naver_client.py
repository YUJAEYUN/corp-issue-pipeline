"""네이버 뉴스 검색 API 클라이언트 (FR-M2-1, FR-M2-2).

법인명/키워드 단독으로 쿼리를 생성하고(스펙 5.2절 FR-M2-2), 정렬은
date(최신순)를 쓴다. 인증키는 코드에 하드코딩하지 않고 환경변수
(NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)로만 읽는다.

2026-07-31부로 네이버가 신규 발급을 NAVER API HUB(네이버클라우드플랫폼)로
이전했다 — 엔드포인트·인증 헤더 이름이 기존 개발자센터(openapi.naver.com)
방식과 다르다. variant="hub"가 기본값이며, 그 전에 발급받은 레거시 키를
쓰는 경우에만 variant="legacy"로 바꾼다.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

HUB_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/news"
LEGACY_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"


class NaverApiError(RuntimeError):
    pass


class NaverCredentialsMissing(NaverApiError):
    pass


@dataclass
class NaverNewsClient:
    client_id: str
    client_secret: str
    variant: str = "hub"  # "hub" | "legacy"
    endpoint: str | None = None
    display: int = 100
    sort: str = "date"
    timeout: float = 10.0
    retry_count: int = 2
    rate_limit_retry_count: int = 6
    retry_backoff_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.endpoint is None:
            self.endpoint = HUB_ENDPOINT if self.variant == "hub" else LEGACY_ENDPOINT

    @classmethod
    def from_env(
        cls,
        variant: str = "hub",
        endpoint: str | None = None,
        display: int = 100,
        sort: str = "date",
        client_id_env: str = "NAVER_CLIENT_ID",
        client_secret_env: str = "NAVER_CLIENT_SECRET",
    ) -> "NaverNewsClient":
        client_id = os.environ.get(client_id_env)
        client_secret = os.environ.get(client_secret_env)
        if not client_id or not client_secret:
            raise NaverCredentialsMissing(
                f"{client_id_env}/{client_secret_env} 환경변수가 설정되어 있지 않다."
            )
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            variant=variant,
            endpoint=endpoint,
            display=display,
            sort=sort,
        )

    def _headers(self) -> dict[str, str]:
        if self.variant == "hub":
            return {
                "X-NCP-APIGW-API-KEY-ID": self.client_id,
                "X-NCP-APIGW-API-KEY": self.client_secret,
            }
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }

    def search(self, query: str, start: int = 1) -> list[dict]:
        """query 단독으로 뉴스를 검색해 원본 응답 item 리스트를 반환한다.

        법인명 검색(FR-M2-2)과 키워드 검색(신규 법인 탐지용) 양쪽에 같은
        메서드를 쓴다 — 어느 쪽이든 "단독" 검색어라는 제약은 동일하다.
        """
        params = {
            "query": query,
            "display": self.display,
            "start": start,
            "sort": self.sort,
        }

        last_error: Exception | None = None
        errors = 0  # 네트워크 오류 재시도 횟수
        throttled = 0  # 429(호출 한도) 재시도 횟수
        while errors <= self.retry_count and throttled <= self.rate_limit_retry_count:
            try:
                response = requests.get(
                    self.endpoint, headers=self._headers(), params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                errors += 1
                time.sleep(self.retry_backoff_seconds * errors)
                continue

            if response.status_code == 200:
                return response.json().get("items", [])

            if response.status_code == 429:
                # 여러 쿼리를 동시에 부르므로 한도에 걸릴 수 있다. 실패로 끝내지
                # 않고 점점 길게 물러났다가 다시 부른다(1, 2, 4, 8, 16, 30초).
                throttled += 1
                time.sleep(min(self.retry_backoff_seconds * 2 ** (throttled - 1), 30.0))
                continue

            raise NaverApiError(
                f"네이버 API 오류: HTTP {response.status_code} - {response.text[:200]}"
            )

        raise NaverApiError(f"네이버 API 호출 재시도 초과: {query}") from last_error
