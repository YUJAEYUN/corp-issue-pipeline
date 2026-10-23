"""법인·팀 매핑 CSV의 법인명을 GitHub Actions 로그에서 가린다.

이 저장소는 공개라 Actions 로그를 누구나 볼 수 있다. job에서 데이터를 받은
직후 이 스크립트를 실행하면, 그 job의 이후 모든 로그에서 CSV 법인명과 그
검색어(법인격 표기를 뗀 이름), 별칭이 ***로 바뀐다(GitHub의 ::add-mask::).

가리기는 로그에만 적용된다. Step Summary와 아티팩트에는 적용되지 않으므로
거기에는 법인명을 쓰지 않고, 아티팩트는 seal.sh로 암호화한다.

사용:
    python3 .github/scripts/mask_corp_names.py [CSV 경로]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "module2_news_pipeline"))

from classify_corp import compute_alias, compute_search_name  # noqa: E402

DEFAULT_CSV = ROOT / "data" / "corp_team_mapping" / "corp_team_mapping.csv"


def main() -> int:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        # 가리지 못한 채 다음 단계로 넘어가면 법인명이 로그에 찍힌다.
        print(f"::error::법인 매핑 CSV가 없어 로그를 가릴 수 없습니다: {csv_path}", file=sys.stderr)
        return 1
    config = json.loads((ROOT / "module2_news_pipeline" / "config.json").read_text(encoding="utf-8"))
    prefix_map = config.get("corp_alias_prefix_map", [])
    fof_suffix = config.get("corp_fof_suffix", "_FOF")

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        names = {row["corp_name"].strip() for row in csv.DictReader(f) if row.get("corp_name")}

    values: set[str] = set()
    for name in names:
        search = compute_search_name(name, fof_suffix)
        values.update({name, search, compute_alias(search, prefix_map)})
    # 한 글자는 거의 모든 줄을 가려 로그를 읽을 수 없게 만든다.
    values = {v.strip() for v in values if v and len(v.strip()) >= 2}

    for value in sorted(values, key=len, reverse=True):
        print(f"::add-mask::{value}")
    print(f"로그에서 가릴 이름 {len(values)}개를 등록했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
