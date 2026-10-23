"""네이버 뉴스 API pubDate 파싱 및 수집 기간(D-1 등) 판정 (FR-M2-1).

네이버 검색 API는 date(최신순) 정렬만 지원하고 날짜 범위 파라미터가
없다 — "어제 하루"를 받으려면 응답을 pubDate 기준으로 직접 걸러야
한다. pubDate는 RFC 2822 형식("Tue, 16 Sep 2026 09:00:00 +0900")으로
온다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))


def parse_pub_date(raw: str) -> datetime | None:
    """pubDate 문자열을 KST 기준 datetime으로 파싱한다. 실패하면 None."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def today_kst() -> date:
    """KST 기준 오늘 날짜. GitHub Actions 러너는 시스템 타임존이 UTC라,
    date.today()를 그대로 쓰면 KST 자정 전후(예: 새벽 실행)에 하루
    어긋난다 — run_date처럼 "오늘" 의미로 쓰는 곳은 이 함수를 쓴다."""
    return datetime.now(KST).date()


def default_d1_range(today: date | None = None) -> tuple[date, date]:
    """기본 수집 기간: D-1(어제) 하루. today를 안 주면 KST 기준 오늘."""
    today = today or today_kst()
    yesterday = today - timedelta(days=1)
    return yesterday, yesterday


def in_range(pub_date: datetime, date_from: date, date_to: date) -> bool:
    return date_from <= pub_date.astimezone(KST).date() <= date_to


def month_folder_name(run_date: str) -> str:
    """`run_date`("YYYY-MM-DD")가 속한 달의 산출물 폴더명을 만든다.

    파일이 날짜별로만 쌓이면 폴더 하나에 계속 늘어나므로, 월 단위로
    묶어 "2026년 9월"처럼 훑어보기 쉽게 한다. 월은 앞에 0을 붙이지
    않는다(예: "2026년 9월", "2026년 12월").
    """
    parsed = date.fromisoformat(run_date)
    return f"{parsed.year}년 {parsed.month}월"
