"""원문 페이지의 og:description 등 메타태그로 요약을 보강한다.

summarize.needs_llm_polish로 걸러진 기사에만 쓴다 — 매일 도는 파이프라인에
네트워크 요청을 전수로 늘리지 않기 위해서다 (스펙 NFR-4, 비용 절감 원칙).

실측 비교(2026-09-17 수집분 14건, docs 참고) 결과 og:description은:
  - 네이버 API description보다 문장이 완결돼 있는 경우가 많지만
  - 일부 사이트는 og:description을 아예 못 채워넣거나(제목을 그대로
    반복) 접근 자체를 막는다(403)
  - 여러 주제를 다루는 칼럼/위클리 기사에서는 기사 전체의 대표 리드만
    주고, 정작 검색으로 매칭된 법인 관련 문장은 빠지는 경우가 있다
그래서 가져온 값을 그대로 쓰지 않고 is_usable_meta_summary로 검증한 뒤
통과한 것만 채택한다.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass

import requests

from text_utils import clean_text

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# <meta ...> 태그를 하나씩 잘라 속성을 읽는다. 예전에는 property/name과 content의
# 순서 두 가지를 정규식 두 개로 문서 전체에서 찾았는데, 역순 패턴이 메타 태그마다
# 문서 끝까지 되짚어 300KB·메타 200개 페이지 하나에 3초가 걸렸다(2026-10-01 수집에서
# 원문 821건에 32분). 태그 단위로 읽으면 순서와 상관없고 문서 길이에 비례한다.
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", re.DOTALL)

_PREFERRED_KEYS = ("og:description", "twitter:description", "description")
_CORP_SUFFIXES = ("(주)", "㈜")
MIN_META_LENGTH = 20


@dataclass
class MetaFetchResult:
    text: str | None
    source: str | None  # "og:description" | "twitter:description" | "description" | None


# 메타 태그는 <head>에 있으므로 앞부분만 읽는다.
MAX_META_BYTES = 300_000


def _read_head(response, deadline: float) -> str | None:
    """본문을 MAX_META_BYTES까지, deadline 전까지만 읽는다.

    requests의 timeout은 바이트 사이 대기 시간이라, 데이터를 조금씩 계속
    흘려보내는 서버는 끝없이 붙잡고 있는다. 2026-10-01 브랜치 수집이 이
    단계에서 90분 넘게 멈췄다. 전체 시간과 크기를 직접 자른다.
    """
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=16_384):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_META_BYTES or time.monotonic() > deadline:
                break
    except requests.RequestException:
        if not chunks:
            return None
    finally:
        response.close()
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


def fetch_meta_description(url: str, timeout: float = 10.0) -> MetaFetchResult:
    """실패(네트워크 오류, 403 등)는 예외를 올리지 않고 빈 결과로 돌려준다 —
    이 보강 단계가 실패해도 파이프라인 본류는 계속 돌아야 한다.
    한 요청은 timeout + 5초 안에 끝난다.
    """
    deadline = time.monotonic() + timeout + 5
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout, stream=True
        )
    except requests.RequestException:
        return MetaFetchResult(text=None, source=None)
    if response.status_code != 200:
        response.close()
        return MetaFetchResult(text=None, source=None)
    text = _read_head(response, deadline)
    if text is None:
        return MetaFetchResult(text=None, source=None)

    found: dict[str, str] = {}
    for tag in _META_TAG_RE.finditer(text):
        attrs = {name.lower(): value for name, _, value in _ATTR_RE.findall(tag.group(0))}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in _PREFERRED_KEYS and key not in found and "content" in attrs:
            found[key] = html.unescape(attrs["content"]).strip()

    for key in _PREFERRED_KEYS:
        if found.get(key):
            return MetaFetchResult(text=found[key], source=key)
    return MetaFetchResult(text=None, source=None)


def _strip_corp_suffix(name: str) -> str:
    stripped = name
    for suffix in _CORP_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
        if stripped.startswith(suffix):
            stripped = stripped[len(suffix) :]
    return stripped.strip()


def is_usable_meta_summary(candidate: str, title: str, matched_corp: str | None = None) -> bool:
    """제목을 그대로 반복하거나 너무 짧으면 못 쓴다. matched_corp가 있으면
    (트랙1 기존 법인 기사) 그 이름이 요약에 최소한 언급돼 있는지도 본다 —
    여러 주제를 묶은 칼럼 기사에서 og:description이 기사 전체의 리드만 주고
    정작 매칭된 법인 얘기는 빠지는 경우를 걸러내기 위해서다.
    """
    if not candidate:
        return False
    cleaned = clean_text(candidate)
    if len(cleaned) < MIN_META_LENGTH:
        return False

    cleaned_title = clean_text(title)
    if cleaned_title and cleaned.startswith(cleaned_title):
        return False

    if matched_corp:
        base = _strip_corp_suffix(matched_corp)
        if base and base not in cleaned:
            return False

    return True
