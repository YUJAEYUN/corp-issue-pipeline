"""네이버 뉴스 API 응답 텍스트 정제 유틸리티.

네이버 검색 API의 title/description 필드는 검색어 강조를 위해 <b> 태그가
섞여 있고 HTML 엔티티(&quot; 등)로 이스케이프되어 있다. 이 모듈은 그걸
사람이 읽을 표시용 텍스트로 정제하고, 제목 중복 판정에 쓸 정규화 키를
만든다.
"""
from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_NORMALIZE_STRIP_RE = re.compile(r"[^\w가-힣]")


def clean_text(raw: str) -> str:
    """<b> 태그를 제거하고 HTML 엔티티를 풀어, 표시용 텍스트로 정제한다."""
    without_tags = _TAG_RE.sub("", raw)
    unescaped = html.unescape(without_tags)
    return _WHITESPACE_RE.sub(" ", unescaped).strip()


def normalize_title(raw_title: str) -> str:
    """중복 판정용 정규화 키. 태그/엔티티 제거 후 공백·기호를 없애고 소문자화한다."""
    cleaned = clean_text(raw_title)
    return _TITLE_NORMALIZE_STRIP_RE.sub("", cleaned).lower()
