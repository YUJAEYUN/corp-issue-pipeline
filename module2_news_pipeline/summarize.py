"""1줄 요약 (FR-M2-8).

기본값은 네이버 API의 description 필드를 정제해 그대로 쓴다 (추가 LLM
비용 없음). 아래 경우엔 그대로 쓰기 애매해 needs_llm_polish=True로 표시해
후속 보강(og:description 확인) 또는 LLM 리뷰 배치로 넘긴다:
  - 너무 짧다 (정보가 사실상 없음)
  - 너무 길다 (엑셀에서 한 줄로 훑어보기 어려움) — "왜 중요한지"는 별도
    필드인 영업시그널 카테고리(keyword_tag)가 맡으므로, 이 요약은 "무슨
    일이 있었는지" 사실만 짧게 전달하는 역할로 좁힌다
  - 인용부호로 시작한다 (앞의 화자·주어가 잘려나간 인용문 조각일 가능성이
    높다 — 예: 원문은 '...라고 조언했다. "환헤지형과 환노출형을 나눠
    담고..."'인데 앞부분이 잘려 인용문부터 시작하는 경우)
  - 따옴표·괄호가 짝이 안 맞는다 (인용문이나 부연설명이 열린 채로 끝까지
    안 닫혀서, 뒷부분이 잘려나갔다는 뜻일 가능성이 높다 — 예: '현대차
    관계자는 "최근 미국 장기금리...'처럼 인용부호를 열고 안 닫은 채 끝남)

문장이 "..."로 끝나는지 자체는 걸러내는 기준으로 쓰지 않는다 — 네이버 API
description은 길이 제한 때문에 완결된 기사조차 거의 항상 "..."로 끝나서,
이 신호만으로는 변별력이 없다(실측: 샘플 14건 전부 "..."로 끝남). 다만
"..." 직전에 따옴표/괄호가 안 닫혀 있으면(위 인용문 사례) 그건 잡아낸다.

이 검사들은 전부 구조적 신호일 뿐, "의미상 완결됐는지"를 정규식으로
판단할 수는 없다 — 그 판단은 LLM 리뷰 단계(원문 확인 포함)로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass

from text_utils import clean_text

MIN_USABLE_LENGTH = 30
MAX_USABLE_LENGTH = 60  # 엑셀 한 줄로 바로 읽히는 길이 기준(대략, 실측 조정 가능)
_LEADING_QUOTE_CHARS = "\"'“”‘’「『"

# (여는 문자, 닫는 문자). 여는 것과 닫는 것이 같은 문자(예: 곧은 따옴표 ")면
# 짝수 개여야 안 잘린 것으로 본다.
_PAIR_CHARS = [
    ('"', '"'),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("(", ")"),
]


@dataclass
class SummaryResult:
    summary: str
    needs_llm_polish: bool


def _has_unbalanced_quote_or_bracket(text: str) -> bool:
    for open_char, close_char in _PAIR_CHARS:
        if open_char == close_char:
            if text.count(open_char) % 2 == 1:
                return True
        elif text.count(open_char) != text.count(close_char):
            return True
    return False


def summarize(description: str) -> SummaryResult:
    cleaned = clean_text(description)
    needs_polish = (
        len(cleaned) < MIN_USABLE_LENGTH
        or len(cleaned) > MAX_USABLE_LENGTH
        or (bool(cleaned) and cleaned[0] in _LEADING_QUOTE_CHARS)
        or _has_unbalanced_quote_or_bracket(cleaned)
    )
    # 길다고 잘라내면 정보가 통째로 날아간다 — 진짜 요약(의미 압축)은
    # og:description 보강 또는 llm_review에서 원문을 보고 새로 쓴다.
    return SummaryResult(summary=cleaned, needs_llm_polish=needs_polish)
