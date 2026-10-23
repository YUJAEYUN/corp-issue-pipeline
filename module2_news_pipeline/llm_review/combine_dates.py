"""검토를 마친 여러 날짜의 이슈 파일을 하나로 합친다.

주말·공휴일처럼 여러 날의 배치를 다음 영업일에 한 번에 전달할 때 쓴다.

입력:
  output/<월>/issues_<날짜>.json  (날짜마다 검토·병합을 마친 최종본)

출력:
  output/<첫 날짜의 월>/issues_<첫 날짜>~<마지막 날짜>.json
  run_date는 "2026-09-19~2026-09-21"처럼 기간으로 적는다. 이 값을 그대로
  모듈 3의 --date에 넘기면 이슈트래커(260919~260921).xlsx가 나온다.

--holiday-span <날짜>는 검토 워크플로우가 매일 부른다. 그 날짜가 영업일이고
바로 앞 날짜들이 주말·공휴일이면, 그 쉬는 날들부터 그 날짜까지를 합본으로
만들고 기간 값(엑셀 --date에 넘길 값)을 출력한다. 쉬는 날 배치도 날마다 따로
검토하는 건 Claude 구독의 5시간 사용 한도에 한 번에 몰리지 않게 하려는
것이고, 합본은 이미 검토된 파일을 합치기만 해서 LLM을 쓰지 않는다.
날짜 값은 배치 날짜(수집 실행일)다. 2026-10-12(월) 배치까지 오면
2026-10-09(한글날)~2026-10-11 배치와 묶어 2026-10-09~2026-10-12가 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

MODULE2_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = MODULE2_ROOT / "output"
DEFAULT_CONFIG_PATH = MODULE2_ROOT / "config.json"
# 연휴가 길어도 이보다 길게 거슬러 올라가지 않는다(설·추석 + 주말 + 대체휴일).
MAX_HOLIDAY_SPAN_DAYS = 10

sys.path.insert(0, str(MODULE2_ROOT))
from date_utils import month_folder_name  # noqa: E402
from dedup import cluster_similar_issues  # noqa: E402


def combine(dates: list[str], output_dir: Path) -> Path:
    dates = sorted(dates)
    if len(dates) < 2:
        raise ValueError("합칠 날짜를 두 개 이상 지정해야 합니다.")

    issues_by_corp: dict[str, list[dict]] = {}
    coverage_warnings: list = []
    collected_from: list[str] = []
    collected_to: list[str] = []
    for run_date in dates:
        path = output_dir / month_folder_name(run_date) / f"issues_{run_date}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for corp_name, records in data.get("issues_by_corp", {}).items():
            issues_by_corp.setdefault(corp_name, []).extend(records)
        coverage_warnings.extend(data.get("coverage_warnings", []))
        if data.get("collected_date_from"):
            collected_from.append(data["collected_date_from"])
        if data.get("collected_date_to"):
            collected_to.append(data["collected_date_to"])

    for corp_name, records in issues_by_corp.items():
        issues_by_corp[corp_name] = cluster_similar_issues(records)

    run_date = f"{dates[0]}~{dates[-1]}"
    combined = {
        "run_date": run_date,
        "collected_date_from": min(collected_from) if collected_from else None,
        "collected_date_to": max(collected_to) if collected_to else None,
        "issues_by_corp": issues_by_corp,
        "coverage_warnings": coverage_warnings,
    }
    out_path = output_dir / month_folder_name(dates[0]) / f"issues_{run_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _korean_holidays(years: set[int]):
    import holidays  # 이 경로에서만 필요해 늦게 불러온다.

    return holidays.KR(years=sorted(years))


def load_extra_non_business_days(config_path: Path = DEFAULT_CONFIG_PATH) -> set[date]:
    """config.json의 extra_non_business_days(공휴일 패키지에 없는 회사 휴무일)."""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return {date.fromisoformat(d) for d in config.get("extra_non_business_days", [])}


def is_business_day(day: date, holiday_calendar, extra_non_business_days=frozenset()) -> bool:
    return day.weekday() < 5 and day not in holiday_calendar and day not in extra_non_business_days


def holiday_span(
    run_date: str, holiday_calendar=None, extra_non_business_days=frozenset()
) -> list[str]:
    """run_date가 영업일이면 바로 앞의 쉬는 날들과 run_date를 날짜순으로 돌려준다.

    run_date가 쉬는 날이거나 바로 앞날이 영업일이면 [run_date] 하나만 돌려준다.
    """
    day = date.fromisoformat(run_date)
    if holiday_calendar is None:
        holiday_calendar = _korean_holidays({day.year, day.year - 1})
    if not is_business_day(day, holiday_calendar, extra_non_business_days):
        return [run_date]
    span = [day]
    prev = day - timedelta(days=1)
    while len(span) <= MAX_HOLIDAY_SPAN_DAYS and not is_business_day(
        prev, holiday_calendar, extra_non_business_days
    ):
        span.insert(0, prev)
        prev -= timedelta(days=1)
    return [d.isoformat() for d in span]


def combine_holiday_span(
    run_date: str,
    output_dir: Path,
    holiday_calendar=None,
    extra_non_business_days=frozenset(),
) -> str | None:
    """연휴 합본을 만들고 기간 값을 돌려준다. 만들 필요가 없거나 못 만들면 None."""
    span = holiday_span(run_date, holiday_calendar, extra_non_business_days)
    if len(span) < 2:
        return None
    missing = [
        d
        for d in span
        if not (output_dir / month_folder_name(d) / f"issues_{d}.json").exists()
    ]
    if missing:
        # 검토가 실패해 빠진 날이 있으면 합본을 만들지 않는다. 빠진 날을
        # 수동으로 다시 검토하면 그 실행이 여러 날짜를 묶어 합본을 만든다.
        print(f"연휴 합본 건너뜀: 이슈 파일이 없는 날짜 {' '.join(missing)}", file=sys.stderr)
        return None
    combine(span, output_dir)
    return f"{span[0]}~{span[-1]}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dates", nargs="+", help="합칠 날짜들 (YYYY-MM-DD)")
    group.add_argument(
        "--holiday-span",
        metavar="DATE",
        help="DATE가 연휴 다음 첫 영업일이면 연휴 합본을 만들고 기간 값만 출력한다",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    if args.holiday_span:
        label = combine_holiday_span(
            args.holiday_span,
            args.output_dir,
            extra_non_business_days=load_extra_non_business_days(args.config),
        )
        if label:
            print(label)
        return

    path = combine(args.dates, args.output_dir)
    print(f"합본 완료: {path}")


if __name__ == "__main__":
    main()
