"""공개 저장소에 올릴 파일에 실제 관리 법인명이 남았는지 검사한다.

법인·팀 매핑 CSV는 비공개 데이터 저장소에만 있다. 이 스크립트는 그 CSV를
읽어, 공개 저장소에서 추적하는 파일(데이터 경로 제외)에 법인명이나 검색어
(법인격 표기를 뗀 이름)가 들어 있으면 줄 번호와 함께 보여 주고 실패한다.

사용:
    python3 .github/scripts/check_no_corp_names.py --csv <비공개 CSV 경로>

별칭 접두어 규칙(config.json의 corp_alias_prefix_map)에 있는 "에스케이" 같은
접두어는 법인 목록을 드러내지 않으므로 검사에서 뺀다.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "module2_news_pipeline"))

from classify_corp import MIN_NORMALIZED_KEY_LENGTH, compute_search_name  # noqa: E402

# 공개 저장소에 두지 않는 데이터 경로. .gitignore와 같다.
DATA_PATHS = (
    "data/corp_team_mapping/corp_team_mapping.csv",
    "module2_news_pipeline/output/",
    "outputs/",
)


def corp_keys(csv_path: Path) -> set[str]:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        names = {row["corp_name"].strip() for row in csv.DictReader(f) if row.get("corp_name")}
    keys = set(names)
    for name in names:
        search = compute_search_name(name)
        if len(search) >= MIN_NORMALIZED_KEY_LENGTH:
            keys.add(search)
    config = json.loads((ROOT / "module2_news_pipeline" / "config.json").read_text(encoding="utf-8"))
    prefixes = {prefix for prefix, _ in config.get("corp_alias_prefix_map", [])}
    return {k for k in keys if k and k not in prefixes}


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [f for f in out.splitlines() if f and not f.startswith(DATA_PATHS)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=ROOT / DATA_PATHS[0])
    args = parser.parse_args()
    if not args.csv.is_file():
        print(f"CSV가 없습니다: {args.csv}", file=sys.stderr)
        return 2

    keys = sorted(corp_keys(args.csv), key=len, reverse=True)
    found = 0
    for rel in tracked_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            hits = [k for k in keys if k in line]
            if hits:
                found += 1
                print(f"{rel}:{lineno}: {', '.join(hits[:3])}")
    if found:
        print(f"실제 법인명이 들어간 줄 {found}개", file=sys.stderr)
        return 1
    print("실제 법인명 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
