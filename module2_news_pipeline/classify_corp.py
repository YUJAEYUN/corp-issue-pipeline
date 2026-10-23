"""기사 속 법인 매칭 및 신규/기존 분류 (FR-M2-5, FR-M2-6).

data/corp_team_mapping/corp_team_mapping.csv를 유일한 source of truth로
읽는다(FR-M2-0). 그 파일은 모듈1이 뽑은 그대로 "corp_name,team"만 담고
있다 — alias(별칭)·base_name(정규화명) 계산은 뉴스 매칭에만 쓰이는
가공이라 여기(모듈2)서 로드할 때 직접 계산한다. 폐쇄망에서 돌리는
모듈1의 VBA는 최대한 단순하게 유지하고, 이런 가공 로직은 고치기 쉬운
이 Python 쪽에 둔다.

기사 제목/본문에 등장하는 법인명을 corp_name/alias/base_name 어느
쪽으로든 매칭한다. 매칭되면 "기존 거래 법인"으로 소속된 모든 팀을
반환하고(FR-M2-6, 다대다 지원), 매칭되는 법인이 하나도 없으면 "신규
후보"로 본다 — 실제로 어느 회사인지는 이 모듈이 자동으로 특정하지
않고(오탐 위험), LLM 리뷰 단계에서 사람이 사실상 판단한다.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALIAS_PREFIX_MAP: list[list[str]] = [
    ["에스케이", "SK"],
    ["케이비", "KB"],
    ["엔에이치", "NH"],
    ["디비", "DB"],
    ["지에스", "GS"],
    ["아이비케이", "IBK"],
    ["에이치디", "HD"],
    ["비지에프", "BGF"],
    ["엘지", "LG"],
    ["엘에스", "LS"],
]
DEFAULT_FOF_SUFFIX = "_FOF"


def compute_alias(corp_name: str, alias_prefix_map: list[list[str]]) -> str:
    """스펙 §12.1 권장안: 접두어 치환 규칙. 매칭 안 되면 빈 문자열."""
    for prefix, replacement in alias_prefix_map:
        if corp_name.startswith(prefix):
            return replacement + corp_name[len(prefix):]
    return ""


def compute_base_name(corp_name: str, fof_suffix: str) -> str:
    """스펙 §9.1.2: `_FOF` 접미사를 제거한 매칭용 정규화명."""
    return corp_name.replace(fof_suffix, "")


def normalized_names(
    corp_name: str, alias_prefix_map: list[list[str]], fof_suffix: str = DEFAULT_FOF_SUFFIX
) -> list[str]:
    """본문 매칭에 쓸 정규화명과 그 별칭. 너무 짧은 이름은 뺀다."""
    search_name = compute_search_name(corp_name, fof_suffix)
    candidates = [search_name, compute_alias(search_name, alias_prefix_map)]
    return [
        name
        for name in dict.fromkeys(candidates)
        if name and len(name) >= MIN_NORMALIZED_KEY_LENGTH
    ]


# 법인격 표기. 뉴스 본문은 "가람투자증권"이라고 쓰지 CSV처럼
# "가람투자증권주식회사"나 "(주)파랑은행"이라고 쓰지 않는다. 이 표기를 붙인
# 채로 검색·매칭하면 "주식회사"가 붙은 법인은 2주 동안 136곳 중 7곳만 잡혔다
# (2026-10-01 실측, "(주)"는 205곳 중 73곳).
_LEGAL_MARKER_RE = re.compile(r"\(주\)|㈜|\(유\)|\(재\)|\(사\)|주식회사|유한회사")

# 정규화명이 이보다 짧으면 매칭 키로 쓰지 않는다. "(주)가람" -> "가람"처럼
# 짧은 이름은 "가람생명" 기사에도 들어 있어 계열사 기사를 그룹사로 오인한다.
MIN_NORMALIZED_KEY_LENGTH = 3


def compute_search_name(corp_name: str, fof_suffix: str = DEFAULT_FOF_SUFFIX) -> str:
    """뉴스 검색어와 본문 매칭에 쓰는 이름. 법인격 표기와 `_FOF`를 뗀다.

    "가람투자증권주식회사" -> "가람투자증권", "(주) 다온투자증권" ->
    "다온투자증권", "다솜생명보험(주)_FOF" -> "다솜생명보험".
    """
    name = compute_base_name(corp_name, fof_suffix)
    name = _LEGAL_MARKER_RE.sub(" ", name)
    return " ".join(name.split()) or corp_name


@dataclass
class CorpMatch:
    corp_name: str
    teams: list[str] = field(default_factory=list)


@dataclass
class CorpDirectory:
    # 매칭 키(법인명/별칭/베이스명) -> 대표 corp_name -> teams
    _lookup: dict[str, str]
    _teams_by_corp: dict[str, list[str]]

    @classmethod
    def load(
        cls,
        csv_path: Path,
        alias_prefix_map: list[list[str]] | None = None,
        fof_suffix: str = DEFAULT_FOF_SUFFIX,
    ) -> "CorpDirectory":
        alias_prefix_map = alias_prefix_map if alias_prefix_map is not None else DEFAULT_ALIAS_PREFIX_MAP

        lookup: dict[str, str] = {}
        teams_by_corp: dict[str, set[str]] = {}

        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                corp_name = row["corp_name"]
                teams_by_corp.setdefault(corp_name, set()).add(row["team"])

                alias = compute_alias(corp_name, alias_prefix_map)
                base_name = compute_base_name(corp_name, fof_suffix)
                for key in (corp_name, alias, base_name):
                    if key:
                        lookup[key] = corp_name
                for key in normalized_names(corp_name, alias_prefix_map, fof_suffix):
                    # 원래 이름 키가 우선이다. 정규화명이 다른 법인의 원래
                    # 이름과 같으면 덮어쓰지 않는다.
                    lookup.setdefault(key, corp_name)

        return cls(
            _lookup=lookup,
            _teams_by_corp={k: sorted(v, key=int) for k, v in teams_by_corp.items()},
        )

    def find_in_text(self, text: str) -> CorpMatch | None:
        """text 안에 등록된 법인명/별칭/베이스명이 하나라도 있으면 매칭한다.

        여러 법인이 동시에 매칭될 수 있는 경우, 텍스트 안에서 더 긴(더
        구체적인) 매칭 키를 우선한다 — 예: "삼성전자"와 "삼성"이 동시에
        등록돼 있으면 더 긴 쪽을 우선.
        """
        candidates = [key for key in self._lookup if key and key in text]
        if not candidates:
            return None
        best_key = max(candidates, key=len)
        corp_name = self._lookup[best_key]
        return CorpMatch(corp_name=corp_name, teams=self._teams_by_corp[corp_name])

    def is_known(self, corp_name: str) -> bool:
        return corp_name in self._teams_by_corp

    def teams_for(self, corp_name: str) -> list[str]:
        return self._teams_by_corp.get(corp_name, [])
