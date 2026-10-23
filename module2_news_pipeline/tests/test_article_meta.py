import requests

import article_meta
from article_meta import fetch_meta_description, is_usable_meta_summary


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", chunks=None):
        self.status_code = status_code
        self.text = text
        self.encoding = "utf-8"
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=1):
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self.text.encode("utf-8")

    def close(self):
        self.closed = True


def test_fetch_meta_description_prefers_og_description(monkeypatch):
    html = """
    <html><head>
    <meta property="og:description" content="하늘생명과 하늘화재가 최대 9조원 규모의 해외 M&amp;A를 추진한다.">
    <meta name="twitter:description" content="트위터용 설명">
    </head></html>
    """
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, html))

    result = fetch_meta_description("https://example.com/article")

    assert result.source == "og:description"
    assert "9조원" in result.text


def test_fetch_meta_description_falls_back_to_twitter_then_plain(monkeypatch):
    html = '<meta name="description" content="일반 description 태그만 있음">'
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, html))

    result = fetch_meta_description("https://example.com/article")

    assert result.source == "description"
    assert result.text == "일반 description 태그만 있음"


def test_fetch_meta_description_handles_reversed_attribute_order(monkeypatch):
    html = '<meta content="속성 순서가 반대인 경우" property="og:description">'
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, html))

    result = fetch_meta_description("https://example.com/article")

    assert result.text == "속성 순서가 반대인 경우"


def test_fetch_meta_description_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(403, ""))

    result = fetch_meta_description("https://example.com/blocked")

    assert result.text is None
    assert result.source is None


def test_fetch_meta_description_returns_none_on_network_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise requests.RequestException("connection failed")

    monkeypatch.setattr(requests, "get", raise_error)

    result = fetch_meta_description("https://example.com/timeout")

    assert result.text is None


def test_is_usable_meta_summary_rejects_too_short():
    assert is_usable_meta_summary("짧음", "아무 제목") is False


def test_is_usable_meta_summary_rejects_title_echo():
    title = "韓 금융부자 47만명 시대…글로벌 금융사와 손잡는 국내 증권사"
    candidate = f"{title}, 최다은 기자, 경제"
    assert is_usable_meta_summary(candidate, title) is False


def test_is_usable_meta_summary_rejects_when_matched_corp_missing():
    candidate = "이번 주 글로벌 정세의 핵심 변수는 호르무즈 해협을 둘러싼 미국과 한국의 움직임입니다."
    assert is_usable_meta_summary(candidate, "머니웨이브", matched_corp="(주)가람") is False


def test_is_usable_meta_summary_rejects_when_corp_base_name_not_a_substring():
    # "하늘생명보험" 전체가 들어있진 않으므로(부분 표기 "하늘생명"만 있음) 거부한다.
    candidate = "하늘생명과 하늘화재가 최대 9조원 규모의 해외 인수합병을 동시에 추진하고 있다."
    assert is_usable_meta_summary(candidate, "제목", matched_corp="하늘생명보험(주)") is False


def test_is_usable_meta_summary_accepts_when_matched_corp_present_with_suffix():
    candidate = "하늘생명과 하늘화재가 최대 9조원 규모의 해외 인수합병을 동시에 추진하고 있다."
    assert is_usable_meta_summary(candidate, "제목", matched_corp="하늘생명") is True


def test_is_usable_meta_summary_strips_leading_corp_suffix():
    candidate = "가람의 오스탈 USA 인수전에 미국 사모펀드가 참여할 가능성이 제기됐다."
    assert is_usable_meta_summary(candidate, "제목", matched_corp="(주)가람") is True


def test_fetch_meta_description_stops_slow_drip_server(monkeypatch):
    # 바이트를 조금씩 끝없이 흘려보내는 서버. 예전에는 여기서 수집 전체가 멈췄다.
    import time

    head = '<meta property="og:description" content="가나다전자가 유상증자를 결정했다">'.encode()

    def endless():
        yield head
        while True:
            time.sleep(0.05)
            yield b" "

    response = FakeResponse(200, "", chunks=endless())
    monkeypatch.setattr(requests, "get", lambda *a, **k: response)

    started = time.monotonic()
    result = fetch_meta_description("http://example.com/slow", timeout=0.5)

    assert time.monotonic() - started < 7
    assert result.text == "가나다전자가 유상증자를 결정했다"
    assert response.closed


def test_fetch_meta_description_is_fast_on_pages_with_many_meta_tags(monkeypatch):
    import time

    metas = "".join(f'<meta property="article:tag{i}" content="태그 {i}">' for i in range(200))
    body = ('<a href="x" title="제목 \'따옴표\'">링크</a><img src="y" alt="설명">' * 10 + "\n") * 1200
    page = metas + '<meta content="가나다전자가 유상증자를 결정했다" property="og:description">' + body
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, page[:300_000]))

    started = time.monotonic()
    result = fetch_meta_description("http://example.com/many-meta")

    assert result.text == "가나다전자가 유상증자를 결정했다"
    assert time.monotonic() - started < 0.5
