"""키워드 기반 1차 필터링 (FR-M2-3, v1 — 임베딩 없이 키워드만).

재현율 우선 원칙에 따라 키워드는 넓게 잡는다. 키워드가 1개만 약하게 걸린
기사는 "확정 통과"로 보지 않고 애매(ambiguous) 판정을 내려, LLM 리뷰
배치로 넘긴다 (스펙 5.2절 FR-M2-3의 "애매한 경계 구간"에 해당).

키워드가 하나도 안 걸린 기사는 reject다. 애초에 "애매한 경계 구간"이
아니라 아무 신호가 없는 것이므로, 관리 법인 관련 기사라는 이유만으로
매일 수천 건씩 검토 큐에 쌓이는 것을 막는다 (실측: 트랙1 한 번 실행에
법인 429개 중 151개에서 히트, 그중 98%가 키워드 0개 매칭인 순수 노이즈
였음).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from text_utils import clean_text


@dataclass
class FilterResult:
    matched_keywords: list[str] = field(default_factory=list)
    keyword_group: str | None = None
    sales_signal_category: str | None = None
    verdict: str = "reject"  # "pass" | "ambiguous" | "reject"


def _find_matches(text: str, keyword_groups: dict[str, list[str]]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    lowered = text.lower()
    for group, keywords in keyword_groups.items():
        group_hits = [kw for kw in keywords if kw.lower() in lowered]
        if group_hits:
            hits[group] = group_hits
    return hits


def filter_article(
    article: dict,
    keyword_groups: dict[str, list[str]],
    keyword_group_to_sales_signal: dict[str, str],
    weak_match_keyword_count: int = 1,
    exclude_names: list[str] | None = None,
    always_review_groups: set[str] | None = None,
) -> FilterResult:
    """exclude_names: 검색에 사용한 법인명(및 별칭)을 넘기면, 그 문자열을
    매칭 대상 텍스트에서 지우고 나서 키워드를 찾는다. "하늘헤지자산운용"처럼
    §8.1 키워드(자산운용, 헤지 등)가 회사 이름 자체에 들어있는 경우, 기사
    내용과 무관하게 회사명 검색이라는 이유만으로 매칭되는 오탐을 막는다.

    always_review_groups: 이 그룹 키워드가 하나라도 걸리면 키워드 수와
    상관없이 ambiguous로 내려 LLM 판정을 받게 한다. 인사변동처럼 한 기사에
    "신임 행장"과 "은행장"이 함께 나오기 쉬운 그룹은 키워드 수가 신호의
    세기를 뜻하지 않는다(부장급 인사 명단도 2개 이상 걸린다).
    """
    title = clean_text(article.get("title", ""))
    description = clean_text(article.get("description", ""))
    combined = f"{title} {description}"

    for name in exclude_names or []:
        if name:
            combined = combined.replace(name, " ")

    hits_by_group = _find_matches(combined, keyword_groups)
    if not hits_by_group:
        return FilterResult(verdict="reject")

    total_hits = sum(len(kws) for kws in hits_by_group.values())
    # 가장 많이 매칭된 그룹을 대표 카테고리로 삼는다 (동률이면 keyword_groups 순서상 먼저 오는 쪽).
    best_group = max(hits_by_group, key=lambda g: len(hits_by_group[g]))
    matched_keywords = hits_by_group[best_group]
    sales_signal = keyword_group_to_sales_signal.get(best_group)

    verdict = "ambiguous" if total_hits <= weak_match_keyword_count else "pass"
    if always_review_groups and always_review_groups & hits_by_group.keys():
        verdict = "ambiguous"
    return FilterResult(
        matched_keywords=matched_keywords,
        keyword_group=best_group,
        sales_signal_category=sales_signal,
        verdict=verdict,
    )


# [포토], [HD포토], [S포토], [사진], [화보] 같은 사진 기사. 사건 정보가 없다.
_PHOTO_TITLE_RE = re.compile(r"^\s*[\[(【][^\])】]*(포토|사진|화보)[^\])】]*[\])】]")
_ANALYST_SUFFIX = r"\s*(?:\S{1,6}\s*)?(?:연구원|애널리스트|리서치센터장|센터장|이코노미스트|전략가|팀장)"


def noise_reason(article: dict, corp_names: list[str]) -> str | None:
    """관리 법인 검색 결과 중 사건이 아닌 기사의 이유. 아니면 None.

    2026-10-01 재수집 표본에서 많이 나온 유형만 좁게 거른다.
      - 사진 기사: 제목이 [포토]·[사진] 등으로 시작
      - 애널리스트 인용: 법인 이름이 "○○증권 연구원" 형태로만 나옴
        (그 법인 자신의 사건이 아니라 다른 종목·시장에 대한 의견)
    """
    title = clean_text(article.get("title", ""))
    if _PHOTO_TITLE_RE.search(title):
        return "사진 기사"

    text = f"{title} {clean_text(article.get('description', ''))}"
    names = [name for name in corp_names if name]
    if not names or not any(name in text for name in names):
        return None
    stripped = text
    for name in names:
        stripped = re.sub(re.escape(name) + _ANALYST_SUFFIX, " ", stripped)
    if stripped != text and not any(name in stripped for name in names):
        return "애널리스트 인용"
    return None
