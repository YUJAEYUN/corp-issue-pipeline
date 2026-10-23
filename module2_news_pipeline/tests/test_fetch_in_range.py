from datetime import date

from pipeline import _fetch_in_range


class PagedFakeClient:
    """start 값에 따라 다른 페이지를 돌려주는, display 크기가 작은 대역."""

    def __init__(self, pages: list[list[dict]], display: int):
        self.pages = pages
        self.display = display

    def search(self, query: str, start: int = 1):
        page_index = (start - 1) // self.display
        if page_index >= len(self.pages):
            return []
        return self.pages[page_index]


def _item(pub_date: str) -> dict:
    return {"title": "제목", "description": "설명", "pubDate": pub_date}


def test_filters_out_articles_outside_range():
    client = PagedFakeClient(
        pages=[
            [
                _item("Wed, 16 Sep 2026 09:00:00 +0900"),  # 범위 안
                _item("Mon, 14 Sep 2026 09:00:00 +0900"),  # 범위 밖 (너무 이름)
            ]
        ],
        display=10,
    )
    result = _fetch_in_range(client, "쿼리", date(2026, 9, 16), date(2026, 9, 16))
    assert len(result.items) == 1
    assert result.items[0]["pubDate"] == "Wed, 16 Sep 2026 09:00:00 +0900"
    assert result.truncated is False


def test_stops_paginating_once_page_goes_too_old():
    # 페이지1: 범위 안 1건 + 범위보다 오래된 1건(=마지막 항목) -> 여기서 멈춰야 함
    # 페이지2가 실제로 있더라도 호출되면 안 됨(호출되면 그 결과가 섞여 assert 실패)
    page1 = [_item("Wed, 16 Sep 2026 09:00:00 +0900"), _item("Mon, 14 Sep 2026 09:00:00 +0900")]
    page2 = [_item("Sun, 13 Sep 2026 09:00:00 +0900")]
    client = PagedFakeClient(pages=[page1, page2], display=2)

    result = _fetch_in_range(client, "쿼리", date(2026, 9, 16), date(2026, 9, 16))
    assert len(result.items) == 1
    assert all(r["pubDate"] != "Sun, 13 Sep 2026 09:00:00 +0900" for r in result.items)
    assert result.truncated is False


def test_continues_paginating_when_page_still_within_range():
    page1 = [_item("Wed, 16 Sep 2026 12:00:00 +0900"), _item("Wed, 16 Sep 2026 09:00:00 +0900")]
    page2 = [_item("Wed, 16 Sep 2026 08:00:00 +0900")]
    client = PagedFakeClient(pages=[page1, page2], display=2)

    result = _fetch_in_range(client, "쿼리", date(2026, 9, 16), date(2026, 9, 16))
    assert len(result.items) == 3
    assert result.truncated is False


def test_stops_when_fewer_items_than_page_size_returned():
    page1 = [_item("Wed, 16 Sep 2026 09:00:00 +0900")]  # display=10보다 적음 -> 마지막 페이지
    client = PagedFakeClient(pages=[page1], display=10)

    result = _fetch_in_range(client, "쿼리", date(2026, 9, 16), date(2026, 9, 16))
    assert len(result.items) == 1
    assert result.truncated is False


def test_flags_truncated_when_max_pages_exhausted_without_reaching_too_old():
    # 페이지마다 display 개수를 꽉 채워서 반환하고, date_from보다 오래된
    # 기사가 한 번도 안 나오면 -> max_pages를 다 써도 끝을 못 봤다는 뜻.
    page = [_item("Wed, 16 Sep 2026 09:00:00 +0900")] * 10
    client = PagedFakeClient(pages=[page, page, page], display=10)

    result = _fetch_in_range(client, "쿼리", date(2026, 9, 16), date(2026, 9, 16), max_pages=3)
    assert len(result.items) == 30
    assert result.truncated is True


def test_query_with_no_results_is_not_flagged_truncated():
    # 2026-10-01 로그: 기사가 0건인 특수목적법인이 "검색 상한 도달"로 찍혔다.
    client = PagedFakeClient(pages=[], display=10)
    result = _fetch_in_range(client, "누리플랜제이차", date(2026, 9, 30), date(2026, 9, 30))
    assert result.items == []
    assert result.truncated is False
