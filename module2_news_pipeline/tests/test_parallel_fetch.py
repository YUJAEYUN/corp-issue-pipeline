"""네이버 검색을 동시에 불러도 결과가 순차 실행과 같은지 확인한다."""
import json
import random
import threading
import time
from datetime import date

import pytest

import naver_client
import pipeline
from test_pipeline import config_path, corp_csv  # noqa: F401  (pytest fixture)

D = date(2026, 9, 16)


def _item(query: str, n: int) -> dict:
    return {
        "title": f"{query} 관련 기사 {n}",
        "description": f"{query} 설명 {n}",
        "originallink": f"http://example.com/{query}/{n}",
        "link": f"http://news.naver.com/{query}/{n}",
        "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
    }


class SlowClient:
    """쿼리마다 끝나는 시간이 뒤죽박죽이 되게 조금씩 쉬는 대역."""

    display = 10

    def __init__(self, fail_on: str | None = None):
        self.fail_on = fail_on
        self.started: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def search(self, query: str, start: int = 1) -> list[dict]:
        with self._lock:
            self.started.append(query)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(random.uniform(0, 0.01))
            if query == self.fail_on:
                raise naver_client.NaverApiError("boom")
            # 두 페이지(10건 + 3건)로 나눠 준다.
            if start == 1:
                return [_item(query, i) for i in range(10)]
            if start == 11:
                return [_item(query, i) for i in range(10, 13)]
            return []
        finally:
            with self._lock:
                self.active -= 1


def test_fetch_all_keeps_query_order_and_runs_concurrently():
    queries = [f"q{i}" for i in range(30)]
    client = SlowClient()
    done_calls = []

    results = pipeline._fetch_all(
        client, queries, D, D, max_pages=10, workers=4,
        on_done=lambda done, index, result, elapsed: done_calls.append((done, index)),
    )

    assert [r.items[0]["title"] for r, _ in results] == [f"{q} 관련 기사 0" for q in queries]
    assert all(len(r.items) == 13 and not r.truncated for r, _ in results)
    assert [d for d, _ in done_calls] == list(range(1, 31))
    assert sorted(i for _, i in done_calls) == list(range(30))
    assert 1 < client.max_active <= 4


def test_fetch_all_raises_and_stops_on_failure():
    queries = [f"q{i}" for i in range(200)]
    client = SlowClient(fail_on="q3")

    with pytest.raises(naver_client.NaverApiError):
        pipeline._fetch_all(client, queries, D, D, max_pages=10, workers=2)

    # 실패 뒤에는 아직 시작하지 않은 쿼리를 부르지 않는다.
    assert len(set(client.started)) < len(queries)


def test_pipeline_output_same_for_sequential_and_parallel(monkeypatch, corp_csv, config_path, tmp_path):
    responses = {
        "가나다전자": [
            {
                "title": "가나다전자(주), 유상증자 이어 회사채 발행",
                "description": "가나다전자가 대규모 자금을 조달한다고 밝혔다는 소식이 전해졌다",
                "originallink": "http://example.com/1",
                "link": "http://news.naver.com/1",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
        "유상증자": [
            {
                "title": f"마바사그룹{i}, 유상증자 이어 회사채 발행",
                "description": f"마바사그룹{i}이 신규 사업 확장을 위해 유상증자와 회사채 발행을 함께 결정했다",
                "originallink": f"http://example.com/2{i}",
                "link": f"http://news.naver.com/2{i}",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
            for i in range(3)
        ],
        "회사채": [
            {
                "title": "마바사그룹0, 유상증자 이어 회사채 발행",
                "description": "같은 기사가 다른 키워드에도 걸린다",
                "originallink": "http://example.com/dup",
                "link": "http://news.naver.com/dup",
                "pubDate": "Wed, 16 Sep 2026 08:00:00 +0900",
            }
        ],
    }

    class JitterClient:
        display = 10

        def search(self, query, start=1):
            time.sleep(random.uniform(0, 0.01))
            return [] if start > 1 else responses.get(query, [])

    monkeypatch.setattr(pipeline, "_build_client", lambda config: JitterClient())
    config = json.loads(config_path.read_text(encoding="utf-8"))

    outputs = []
    for workers in (1, 8):
        config.setdefault("naver_api", {})["search_workers"] = workers
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        paths = pipeline.run_pipeline(
            config_path=config_path,
            corp_csv_path=corp_csv,
            output_dir=tmp_path / f"out{workers}",
            run_date="2026-09-16",
            date_from="2026-09-16",
            date_to="2026-09-16",
        )
        outputs.append({k: p.read_text(encoding="utf-8") for k, p in paths.items()})

    assert outputs[0] == outputs[1]


class _Resp:
    def __init__(self, status, items=()):
        self.status_code = status
        self.text = ""
        self._items = list(items)

    def json(self):
        return {"items": self._items}


def _client():
    return naver_client.NaverNewsClient(client_id="id", client_secret="secret")


def test_client_backs_off_on_429_then_succeeds(monkeypatch):
    responses = [_Resp(429), _Resp(429), _Resp(429), _Resp(200, [{"title": "ok"}])]
    sleeps = []
    monkeypatch.setattr(naver_client.requests, "get", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(naver_client.time, "sleep", sleeps.append)

    assert _client().search("q") == [{"title": "ok"}]
    assert sleeps == [1.0, 2.0, 4.0]


def test_client_gives_up_after_rate_limit_retries(monkeypatch):
    monkeypatch.setattr(naver_client.requests, "get", lambda *a, **k: _Resp(429))
    monkeypatch.setattr(naver_client.time, "sleep", lambda s: None)

    with pytest.raises(naver_client.NaverApiError):
        _client().search("q")
