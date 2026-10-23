from datetime import date, datetime, timedelta, timezone

from date_utils import default_d1_range, in_range, parse_pub_date, today_kst


def test_parse_pub_date_handles_rfc2822_with_kst_offset():
    parsed = parse_pub_date("Tue, 16 Sep 2026 09:00:00 +0900")
    assert parsed is not None
    assert parsed.date() == date(2026, 9, 16)


def test_parse_pub_date_converts_non_kst_offset_to_kst():
    # UTC 자정은 KST로 오전 9시, 같은 날짜.
    parsed = parse_pub_date("Tue, 16 Sep 2026 00:00:00 +0000")
    assert parsed.date() == date(2026, 9, 16)
    assert parsed.hour == 9


def test_parse_pub_date_invalid_returns_none():
    assert parse_pub_date("") is None
    assert parse_pub_date("이건 날짜가 아님") is None


def test_today_kst_uses_kst_not_system_timezone(monkeypatch):
    import date_utils

    # UTC 16:15는 KST로 다음날 01:15 — 러너 시스템 타임존(UTC)의
    # date.today()라면 여기서 하루 어긋난 날짜가 나온다.
    fixed_utc = datetime(2026, 9, 20, 16, 15, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(date_utils, "datetime", _FixedDatetime)
    assert date_utils.today_kst() == date(2026, 9, 21)


def test_default_d1_range_is_yesterday():
    today = date(2026, 9, 17)
    date_from, date_to = default_d1_range(today)
    assert date_from == date_to == today - timedelta(days=1)


def test_in_range_boundaries():
    d = date(2026, 9, 16)
    pub_date = parse_pub_date("Wed, 16 Sep 2026 23:59:00 +0900")
    assert in_range(pub_date, d, d) is True

    too_early = parse_pub_date("Tue, 15 Sep 2026 23:59:00 +0900")
    assert in_range(too_early, d, d) is False

    too_late = parse_pub_date("Thu, 17 Sep 2026 00:01:00 +0900")
    assert in_range(too_late, d, d) is False
