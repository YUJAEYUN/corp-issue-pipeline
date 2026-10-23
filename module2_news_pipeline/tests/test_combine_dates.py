import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm_review"))

import combine_dates as combine_mod


def _issue(article_id, title, summary, pub_date):
    return {
        "article_id": article_id,
        "title": title,
        "summary": summary,
        "link": f"http://example.com/{article_id}",
        "originallink": f"http://example.com/{article_id}",
        "pub_date": pub_date,
    }


def _write_issues(month_dir, run_date, issues_by_corp, warnings=()):
    (month_dir / f"issues_{run_date}.json").write_text(
        json.dumps(
            {
                "run_date": run_date,
                "collected_date_from": run_date,
                "collected_date_to": run_date,
                "issues_by_corp": issues_by_corp,
                "coverage_warnings": list(warnings),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_combine_merges_days_into_range_file_and_dedups_same_event(tmp_path):
    month_dir = tmp_path / "2026년 9월"
    month_dir.mkdir()
    same_event = "홈플러스, 대형마트 67개점 M&A 착수…폐점 점포 19곳도 매각"
    _write_issues(
        month_dir,
        "2026-09-19",
        {"홈플러스": [_issue("1", same_event, "홈플러스가 67개점 매각에 나선다.", "2026-09-19")]},
        warnings=["검색어 A 누락 가능"],
    )
    _write_issues(
        month_dir,
        "2026-09-20",
        {
            "홈플러스": [_issue("2", same_event, "홈플러스가 67개점과 19곳 매각에 착수한다.", "2026-09-20")],
            "가나다전자": [_issue("3", "가나다전자 회사채 발행", "가나다전자가 500억 회사채를 발행한다.", "2026-09-20")],
        },
    )

    out_path = combine_mod.combine(["2026-09-20", "2026-09-19"], tmp_path)

    assert out_path == month_dir / "issues_2026-09-19~2026-09-20.json"
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["run_date"] == "2026-09-19~2026-09-20"
    assert data["collected_date_from"] == "2026-09-19"
    assert data["collected_date_to"] == "2026-09-20"
    assert data["coverage_warnings"] == ["검색어 A 누락 가능"]
    assert len(data["issues_by_corp"]["홈플러스"]) == 1
    assert len(data["issues_by_corp"]["가나다전자"]) == 1


def test_combine_requires_two_or_more_dates(tmp_path):
    with pytest.raises(ValueError):
        combine_mod.combine(["2026-09-19"], tmp_path)


# --- 연휴 합본 (--holiday-span) ---

from datetime import date  # noqa: E402

# 테스트가 holidays 패키지 버전에 흔들리지 않게 2026년 10월 공휴일만 직접 준다.
_OCT_2026_HOLIDAYS = {date(2026, 10, 3), date(2026, 10, 5), date(2026, 10, 9)}


@pytest.mark.parametrize(
    "run_date, expected",
    [
        # 한글날(금) + 주말 뒤 월요일: 실제로 손으로 만들었던 2026-10-09~2026-10-12 합본과 같다.
        ("2026-10-12", ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"]),
        # 개천절(토) + 일 + 대체휴일(월) 뒤 화요일: 2026-10-03~2026-10-06 합본과 같다.
        ("2026-10-06", ["2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"]),
        # 평일 다음 평일은 합본이 없다.
        ("2026-10-14", ["2026-10-14"]),
        # 쉬는 날 당일 실행은 합본을 만들지 않는다(다음 영업일에 만든다).
        ("2026-10-10", ["2026-10-10"]),
        ("2026-10-09", ["2026-10-09"]),
    ],
)
def test_holiday_span(run_date, expected):
    assert combine_mod.holiday_span(run_date, _OCT_2026_HOLIDAYS) == expected


def test_holiday_span_respects_extra_non_business_days():
    # 2026-10-15(목)를 회사 휴무로 두면 10-16(금) 실행이 둘을 묶는다.
    assert combine_mod.holiday_span(
        "2026-10-16", _OCT_2026_HOLIDAYS, {date(2026, 10, 15)}
    ) == ["2026-10-15", "2026-10-16"]


def test_holiday_span_uses_korean_public_holidays_by_default():
    assert combine_mod.holiday_span("2026-10-12")[0] == "2026-10-09"


def test_combine_holiday_span_writes_range_file(tmp_path):
    month_dir = tmp_path / "2026년 10월"
    month_dir.mkdir()
    events = {
        "2026-10-09": "[테스트은행] 본사 사옥 3,000억원에 매각",
        "2026-10-10": "[테스트은행] 신임 은행장에 김철수 부행장 선임",
        "2026-10-11": "[테스트은행] 베트남 법인 지분 49% 인수 계약",
        "2026-10-12": "[테스트은행] 퇴직연금 적립금 20조원 돌파",
    }
    for d, summary in events.items():
        _write_issues(month_dir, d, {"테스트은행": [_issue(f"a-{d}", summary, summary, d)]})

    label = combine_mod.combine_holiday_span("2026-10-12", tmp_path, _OCT_2026_HOLIDAYS)

    assert label == "2026-10-09~2026-10-12"
    combined = json.loads((month_dir / "issues_2026-10-09~2026-10-12.json").read_text(encoding="utf-8"))
    assert combined["run_date"] == label
    assert len(combined["issues_by_corp"]["테스트은행"]) == 4


def test_combine_holiday_span_skips_when_a_day_is_missing(tmp_path):
    month_dir = tmp_path / "2026년 10월"
    month_dir.mkdir()
    for d in ("2026-10-09", "2026-10-11", "2026-10-12"):  # 10-10 검토 실패
        _write_issues(month_dir, d, {})

    assert combine_mod.combine_holiday_span("2026-10-12", tmp_path, _OCT_2026_HOLIDAYS) is None
    assert not list(month_dir.glob("issues_*~*.json"))


def test_combine_holiday_span_none_on_ordinary_weekday(tmp_path):
    assert combine_mod.combine_holiday_span("2026-10-14", tmp_path, _OCT_2026_HOLIDAYS) is None
