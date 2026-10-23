"""corp_team_mapping.csv 무결성 검증.

법인 리스트를 갱신(재커밋)할 때마다 실행해, 팀 번호가 유효한지,
(corp_name, team) 중복이 없는지, 실제 법인명이 아닌 소계/합계 라벨이
섞여 있지 않은지 확인한다.

이 파일은 모듈1이 뽑는 그대로 "corp_name,team"만 담는다 — alias/
base_name 계산은 모듈2 책임이라 여기서는 검증하지 않는다.

사용법:
    python3 data/corp_team_mapping/validate.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE1_CONFIG_PATH = REPO_ROOT / "module1_vba_generator" / "config.json"
CSV_PATH = Path(__file__).parent / "corp_team_mapping.csv"


def main() -> int:
    module1_config = json.loads(MODULE1_CONFIG_PATH.read_text(encoding="utf-8"))
    valid_teams = {str(t) for t in module1_config["team_numbers"]}
    excluded_labels = set(module1_config.get("excluded_labels", []))

    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    errors: list[str] = []
    seen_pairs: Counter = Counter()

    for i, row in enumerate(rows, start=2):  # 헤더가 1행
        corp_name = row["corp_name"]
        team = row["team"]

        if team not in valid_teams:
            errors.append(f"L{i}: 유효하지 않은 team 값 '{team}' ({corp_name})")

        if corp_name in excluded_labels:
            errors.append(f"L{i}: 제외 대상 라벨이 법인명으로 남아있음 ('{corp_name}')")

        seen_pairs[(corp_name, team)] += 1

    for (corp_name, team), count in seen_pairs.items():
        if count > 1:
            errors.append(f"중복 행: '{corp_name}' / {team}팀이 {count}번 등장")

    team_counts = Counter(row["team"] for row in rows)
    print("팀별 건수:")
    for team in sorted(team_counts, key=int):
        print(f"  {team}팀: {team_counts[team]}")
    print(f"총 {len(rows)}건, 고유 법인명 {len({r['corp_name'] for r in rows})}건")

    if errors:
        print(f"\n{len(errors)}건의 문제 발견:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\n검증 통과: 이상 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
