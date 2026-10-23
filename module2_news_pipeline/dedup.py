"""동일 사건 중복 기사 제거 (FR-M2-4).

제목을 정규화한 뒤, 완전히 같은 정규화 제목은 즉시 중복으로 묶고, 다르지만
유사한 제목은 SequenceMatcher 비율로 판정한다. 같은 그룹으로 묶인 기사
중에는 originallink가 있는 쪽, 그마저 없으면 발행일이 더 이른 쪽을 대표로
남긴다.

이 dedup_articles()는 검색 API 원본 응답(같은 쿼리 결과) 안에서만 비교하는
1차 중복제거다. 언론사마다 헤드라인 문구를 아예 다르게 쓰는 경우(예: 같은
"홈플러스 대형마트 매각" 사건을 "M&A 착수" vs "새 주인 찾기 본격화"로 각자
표현)는 제목 유사도만으로는 못 잡는다. cluster_similar_issues()가 최종
이슈 목록(법인별로 이미 묶인 상태) 안에서 이를 보완하는 2차 중복제거다.
"""
from __future__ import annotations

import itertools
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from text_utils import normalize_title


@dataclass
class DedupResult:
    kept: list[dict]
    duplicate_count: int


def _similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    return _similar_with(SequenceMatcher(None, "", b), a, b, threshold)


def _similar_with(matcher: SequenceMatcher, a: str, b: str, threshold: float) -> bool:
    """matcher는 b를 seq2로 이미 들고 있다(b의 색인을 매번 다시 만들지 않는다).

    ratio()는 비싸다. 길이로 구한 상한, real_quick_ratio(), quick_ratio()는
    모두 ratio()의 상한이라 여기서 떨어지면 ratio()도 떨어진다. 결과는 같고,
    키워드 전국 검색(수천 건) 중복 제거에서 대부분의 쌍을 여기서 거른다.
    """
    if 2 * min(len(a), len(b)) / (len(a) + len(b)) < threshold:
        return False
    matcher.set_seq1(a)
    return (
        matcher.real_quick_ratio() >= threshold
        and matcher.quick_ratio() >= threshold
        and matcher.ratio() >= threshold
    )


def _pick_representative(group: list[dict]) -> dict:
    with_link = [a for a in group if a.get("originallink")]
    pool = with_link or group
    return sorted(pool, key=lambda a: a.get("pubDate", ""))[0]


def dedup_articles(articles: list[dict], similarity_threshold: float = 0.82) -> DedupResult:
    """articles: 각 dict는 최소 title/pubDate/originallink 키를 가진다.

    새 기사는 앞서 만든 그룹 대표 제목과 순서대로 비교해 처음으로 비슷한
    그룹에 들어간다. 키워드 전국 검색은 하루 1만 4천 건이 넘어 모든 그룹과
    일일이 비교하면 2분 넘게 걸렸다(2026-10-15 실행: 132초). 그래서 글자
    겹침으로 구한 ratio()의 상한으로 후보 그룹을 먼저 한 번에 거르고, 남은
    후보만 원래 순서대로 _similar_with()로 비교한다.

    상한: SequenceMatcher가 맞추는 글자 수 M은 두 제목에 같이 나오는 글자의
    개수(멀티셋 교집합)를 넘을 수 없고, 그 값은 다시 "새 제목의 글자 중
    기존 제목에도 있는 글자의 개수"를 넘을 수 없다. 이 값이 ratio() 기준
    (2M / 길이 합 >= threshold)을 못 넘기는 그룹은 비교해 봐야 떨어진다.
    그래서 결과(어느 그룹에 들어가는지)는 일일이 비교할 때와 같다.
    """
    norms = [normalize_title(article.get("title", "")) for article in articles]
    vocab: dict[str, int] = {}
    for norm in norms:
        for ch in norm:
            vocab.setdefault(ch, len(vocab))
    # 그룹(대표 제목)마다 어떤 글자가 들어 있는지와 제목 길이.
    has_char = np.zeros((len(articles), max(len(vocab), 1)), dtype=np.uint8)
    lengths = np.zeros(len(articles), dtype=np.int64)

    groups: list[list[dict]] = []
    norm_titles: list[str] = []
    matchers: list[SequenceMatcher] = []
    exact_index: dict[str, int] = {}

    for article, norm in zip(articles, norms):
        placed_idx = exact_index.get(norm)
        if placed_idx is None and norm and groups:
            counts = Counter(norm)
            cols = np.fromiter((vocab[ch] for ch in counts), dtype=np.int64, count=len(counts))
            weights = np.fromiter(counts.values(), dtype=np.int64, count=len(counts))
            n = len(groups)
            shared = has_char[:n, cols] @ weights
            # 부동소수 반올림으로 경계의 그룹을 놓치지 않게 아주 조금 느슨하게 둔다.
            bound_ok = 2 * shared >= similarity_threshold * (len(norm) + lengths[:n]) - 1e-9
            for idx in np.flatnonzero(bound_ok):
                existing_norm = norm_titles[idx]
                if existing_norm and _similar_with(
                    matchers[idx], norm, existing_norm, similarity_threshold
                ):
                    placed_idx = int(idx)
                    break
        if placed_idx is not None:
            groups[placed_idx].append(article)
        else:
            exact_index.setdefault(norm, len(groups))
            if norm:
                has_char[len(groups), [vocab[ch] for ch in set(norm)]] = 1
                lengths[len(groups)] = len(norm)
            groups.append([article])
            norm_titles.append(norm)
            matchers.append(SequenceMatcher(None, "", norm))

    kept = [_pick_representative(group) for group in groups]
    duplicate_count = len(articles) - len(kept)
    return DedupResult(kept=kept, duplicate_count=duplicate_count)


_TITLE_NGRAM_SIZE = 3
# 숫자와 바로 뒤의 단위를 함께 본다. 날짜·시각(28일, 9월, 2026년, 10시)은
# 같은 날 나온 서로 다른 사건 기사에도 흔히 겹쳐서 수치 신호에서 뺀다
# (2026-10-01: 파랑은행 BNY MOU와 아인병원 협약이 "28일"·"29일"로 묶임).
_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)*)\s*(\S?)")
_DATE_UNITS = set("일월년시분")
_YEAR_RE = re.compile(r"(19|20)\d\d")
_ISSUE_SIMILARITY_THRESHOLD = 0.3
_RELATED_ARTICLE_FIELDS = ("article_id", "title", "link", "originallink", "pub_date")


def _char_ngrams(text: str, n: int = _TITLE_NGRAM_SIZE) -> set[str]:
    compact = "".join((text or "").split())
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def _event_numbers(text: str) -> set[str]:
    numbers = set()
    for number, unit in _NUMBER_RE.findall(text or ""):
        if unit in _DATE_UNITS or _YEAR_RE.fullmatch(number):
            continue
        numbers.add(number)
    return numbers


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _issue_similarity(a: dict, b: dict) -> float:
    """제목 3-gram 유사도와, title+summary에 등장하는 숫자(수치) 겹침 중
    큰 값을 쓴다. 실측(2026-09-18, 홈플러스 28건)에서 순수 텍스트 유사도의
    중앙값은 0.16에 그쳤지만, 숫자 겹침의 중앙값은 0.5로 훨씬 강한 신호였다
    — "67개점", "19곳" 같은 구체적 수치는 언론사가 문장을 다르게 써도
    거의 그대로 반복하기 때문이다. 두 기사 다 숫자가 있을 때만 그 신호를
    쓰고, 없으면 텍스트 유사도만 본다.
    """
    summary_a = "".join((a.get("summary") or "").split())
    if summary_a and summary_a == "".join((b.get("summary") or "").split()):
        # 검토자가 같은 사건의 요약을 한 문장으로 통일해 둔 경우(2026-09-30,
        # 파랑은행·BNY MOU 11건). 헤드라인이 제각각이어도 같은 사건이다.
        return 1.0
    text_sim = _jaccard(_char_ngrams(a.get("title", "")), _char_ngrams(b.get("title", "")))
    numbers_a = _event_numbers((a.get("title") or "") + " " + (a.get("summary") or ""))
    numbers_b = _event_numbers((b.get("title") or "") + " " + (b.get("summary") or ""))
    if numbers_a and numbers_b:
        return max(text_sim, _jaccard(numbers_a, numbers_b))
    return text_sim


def _merge_issue_group(group: list[dict]) -> dict:
    if len(group) == 1:
        return group[0]

    # 이전 병합에서 이미 related_articles가 붙어있으면 펼쳐서 후보군에 합친다
    # (같은 법인 안에서 이 함수가 여러 번 실행돼도 관련기사 목록이 안 깨지게).
    flat: list[dict] = []
    for record in group:
        flat.append({k: v for k, v in record.items() if k != "related_articles"})
        flat.extend(record.get("related_articles", []))

    representative = max(flat, key=lambda r: (len(r.get("summary") or ""), r.get("pub_date", "")))
    others = [r for r in flat if r is not representative]

    merged = dict(representative)
    merged["related_articles"] = [
        {field: o.get(field) for field in _RELATED_ARTICLE_FIELDS} for o in others
    ]
    return merged


def cluster_similar_issues(
    records: list[dict], similarity_threshold: float = _ISSUE_SIMILARITY_THRESHOLD
) -> list[dict]:
    """법인 안에서 사실상 같은 사건을 다루는 기사(언론사만 다름)를 대표
    1건 + related_articles로 묶는다. 비교 범위를 법인 단위로 좁혀서
    (이미 issues_by_corp로 나뉜 상태) 비용과 오탐 위험을 둘 다 낮춘다.
    링크는 related_articles에 남기므로 잃지 않는다.
    """
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i, j in itertools.combinations(range(n), 2):
        if _issue_similarity(records[i], records[j]) >= similarity_threshold:
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    return [_merge_issue_group([records[i] for i in indices]) for indices in groups.values()]
