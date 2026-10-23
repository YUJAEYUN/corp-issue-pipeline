import csv
import json

import pytest

import pipeline
from article_meta import MetaFetchResult


class FakeClient:
    """corp_name/keyword로 나뉜 고정 응답을 돌려주는 네이버 API 대역.

    실제 API처럼 display 크기보다 응답이 작으면 pipeline._fetch_in_range가
    첫 페이지에서 멈추도록, display 속성을 노출한다.
    """

    def __init__(self, responses: dict[str, list[dict]], display: int = 10):
        self.responses = responses
        self.display = display

    def search(self, query: str, start: int = 1) -> list[dict]:
        if start > 1:
            return []
        return self.responses.get(query, [])


@pytest.fixture
def corp_csv(tmp_path):
    path = tmp_path / "corp_team_mapping.csv"
    rows = [
        {"corp_name": "가나다전자(주)", "team": "1"},
        {"corp_name": "가나다전자(주)", "team": "2"},
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["corp_name", "team"])
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def config_path(tmp_path):
    config = {
        "naver_api": {
            "variant": "hub",
            "hub_endpoint": "https://example.invalid/hub",
            "legacy_endpoint": "https://example.invalid/legacy",
            "display": 10,
            "sort": "date",
            "client_id_env": "NAVER_CLIENT_ID",
            "client_secret_env": "NAVER_CLIENT_SECRET",
        },
        "keyword_groups": {
            "자금조달": ["유상증자", "회사채"],
        },
        "keyword_group_to_sales_signal": {"자금조달": "조달 니즈 발생"},
        "dedup_title_similarity_threshold": 0.82,
        "weak_match_keyword_count": 1,
        # 이 테스트는 네트워크를 쓰지 않는다 — 명시적으로 꺼서 실수로
        # 실제 요청이 나가지 않게 한다. 보강 로직 자체는
        # test_meta_fallback_enriches_short_summary에서 대역으로 검증한다.
        "enable_meta_description_fallback": False,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def test_pipeline_classifies_known_corp_and_finds_new_candidate(
    monkeypatch, corp_csv, config_path, tmp_path
):
    responses = {
        # 검색어는 법인격 표기를 뗀 이름이다.
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
                "title": "마바사그룹, 유상증자 이어 회사채 발행",
                "description": "마바사그룹이 신규 사업 확장을 위해 유상증자와 회사채 발행을 함께 결정했다",
                "originallink": "http://example.com/2",
                "link": "http://news.naver.com/2",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
        "회사채": [],
    }
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient(responses))

    result_paths = pipeline.run_pipeline(
        config_path=config_path,
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
    )

    issues = json.loads(result_paths["issues"].read_text(encoding="utf-8"))
    assert "가나다전자(주)" in issues["issues_by_corp"]
    known_issue = issues["issues_by_corp"]["가나다전자(주)"][0]
    assert known_issue["corp_status"] == "기존"
    assert known_issue["team"] == ["1", "2"]

    review = json.loads(result_paths["llm_review_batch"].read_text(encoding="utf-8"))
    new_titles = [c["title"] for c in review["new_corp_candidates"]]
    assert any("마바사그룹" in t for t in new_titles)


def test_track1_only_group_is_used_for_known_corps_but_not_keyword_search(
    monkeypatch, corp_csv, tmp_path
):
    config = {
        "naver_api": {
            "variant": "hub",
            "hub_endpoint": "https://example.invalid/hub",
            "legacy_endpoint": "https://example.invalid/legacy",
            "display": 10,
            "sort": "date",
            "client_id_env": "NAVER_CLIENT_ID",
            "client_secret_env": "NAVER_CLIENT_SECRET",
        },
        "keyword_groups": {
            "자금조달": ["유상증자", "회사채"],
            "인사변동": ["CFO 선임"],
        },
        "keyword_group_to_sales_signal": {"자금조달": "조달 니즈 발생", "인사변동": "인사 변동"},
        "track1_only_keyword_groups": ["인사변동"],
        "dedup_title_similarity_threshold": 0.82,
        "weak_match_keyword_count": 1,
        "enable_meta_description_fallback": False,
    }
    config_path = tmp_path / "config_track1_only.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    responses = {
        # 검색어는 법인격 표기를 뗀 이름이다.
        "가나다전자": [
            {
                "title": "가나다전자(주), 신임 CFO 선임",
                "description": "가나다전자가 재무 총괄로 새 CFO 선임을 결정했다고 밝혔다",
                "originallink": "http://example.com/1",
                "link": "http://news.naver.com/1",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
        # 트랙2에서 이 기사가 잡히면 유상증자 + CFO 선임 2개로 통과해 버린다.
        "유상증자": [
            {
                "title": "마바사그룹, 유상증자 앞두고 CFO 선임",
                "description": "마바사그룹이 유상증자를 앞두고 새 CFO 선임을 마쳤다는 소식이 전해졌다",
                "originallink": "http://example.com/2",
                "link": "http://news.naver.com/2",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
    }
    queries: list[str] = []

    class RecordingClient(FakeClient):
        def search(self, query: str, start: int = 1) -> list[dict]:
            queries.append(query)
            return super().search(query, start)

    monkeypatch.setattr(pipeline, "_build_client", lambda config: RecordingClient(responses))

    result_paths = pipeline.run_pipeline(
        config_path=config_path,
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
    )

    assert "CFO 선임" not in queries
    review = json.loads(result_paths["llm_review_batch"].read_text(encoding="utf-8"))
    ambiguous = review["ambiguous_existing_corp_issues"]
    assert [r["keyword_tag"] for r in ambiguous] == ["인사 변동"]
    assert review["new_corp_candidates"] == []


def test_recollecting_same_date_removes_stale_llm_review_result(
    monkeypatch, corp_csv, config_path, tmp_path
):
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient({}))
    month_dir = tmp_path / "output" / "2026년 9월"
    month_dir.mkdir(parents=True)
    stale = month_dir / "llm_review_result_2026-09-16.json"
    stale.write_text("{}", encoding="utf-8")
    other_day = month_dir / "llm_review_result_2026-09-15.json"
    other_day.write_text("{}", encoding="utf-8")

    pipeline.run_pipeline(
        config_path=config_path,
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
    )

    assert not stale.exists()
    assert other_day.exists()


def _fallback_config(tmp_path):
    config = {
        "naver_api": {
            "variant": "hub",
            "hub_endpoint": "https://example.invalid/hub",
            "legacy_endpoint": "https://example.invalid/legacy",
            "display": 10,
            "sort": "date",
            "client_id_env": "NAVER_CLIENT_ID",
            "client_secret_env": "NAVER_CLIENT_SECRET",
        },
        "keyword_groups": {"자금조달": ["유상증자", "회사채"]},
        "keyword_group_to_sales_signal": {"자금조달": "조달 니즈 발생"},
        "dedup_title_similarity_threshold": 0.82,
        "weak_match_keyword_count": 1,
        "enable_meta_description_fallback": True,
    }
    path = tmp_path / "config_fallback.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def test_meta_fallback_enriches_short_summary(monkeypatch, corp_csv, tmp_path):
    responses = {
        # 검색어는 법인격 표기를 뗀 이름이다.
        "가나다전자": [
            {
                # 키워드 2개(유상증자/회사채) 매칭시켜 verdict가 ambiguous가 아닌
                # pass가 되게 한다 — needs_llm_polish 보강 로직만 별도로 본다.
                "title": "가나다전자(주), 유상증자 이어 회사채 발행",
                "description": "짧은 설명",  # 30자 미만 → needs_llm_polish
                "originallink": "http://example.com/short",
                "link": "http://news.naver.com/short",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
        "유상증자": [],
        "회사채": [],
    }
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient(responses))

    fake_summary = "가나다전자(주)가 유상증자를 통해 500억원을 조달하기로 이사회에서 결정했다."

    def fake_meta_fetcher(url: str) -> MetaFetchResult:
        assert url == "http://example.com/short"
        return MetaFetchResult(text=fake_summary, source="og:description")

    result_paths = pipeline.run_pipeline(
        config_path=_fallback_config(tmp_path),
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
        meta_fetcher=fake_meta_fetcher,
    )

    issues = json.loads(result_paths["issues"].read_text(encoding="utf-8"))
    issue = issues["issues_by_corp"]["가나다전자(주)"][0]
    assert issue["summary"] == fake_summary
    assert issue["needs_llm_polish"] is False


def test_meta_fallback_ignores_unusable_candidate(monkeypatch, corp_csv, tmp_path):
    responses = {
        # 검색어는 법인격 표기를 뗀 이름이다.
        "가나다전자": [
            {
                "title": "가나다전자(주), 유상증자 이어 회사채 발행",
                "description": "짧은 설명",  # 30자 미만 → needs_llm_polish
                "originallink": "http://example.com/short",
                "link": "http://news.naver.com/short",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
        "유상증자": [],
        "회사채": [],
    }
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient(responses))

    # 가나다전자를 언급하지 않는, 여러 주제를 다루는 기사의 대표 리드라고 가정.
    unrelated_summary = "이번 주 글로벌 정세의 핵심 변수는 미국과 한국의 외교 움직임입니다."

    def fake_meta_fetcher(url: str) -> MetaFetchResult:
        return MetaFetchResult(text=unrelated_summary, source="og:description")

    result_paths = pipeline.run_pipeline(
        config_path=_fallback_config(tmp_path),
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
        meta_fetcher=fake_meta_fetcher,
    )

    issues = json.loads(result_paths["issues"].read_text(encoding="utf-8"))
    issue = issues["issues_by_corp"]["가나다전자(주)"][0]
    assert issue["summary"] == "짧은 설명"
    assert issue["needs_llm_polish"] is True

    review = json.loads(result_paths["llm_review_batch"].read_text(encoding="utf-8"))
    polish_ids = [r["article_id"] for r in review["summary_polish_needed"]]
    assert issue["article_id"] in polish_ids


def test_strong_match_not_actually_mentioning_queried_corp_is_downgraded_to_ambiguous(
    monkeypatch, corp_csv, config_path, tmp_path
):
    # 네이버 검색이 느슨한 관련도 매칭이라, 법인명으로 검색해도 그 법인이
    # 실제로는 전혀 언급되지 않은 기사가 섞여 들어올 수 있다(실측 확인:
    # "하늘헤지자산운용"으로 검색했는데 실제로는 "하늘자산운용" 얘기인
    # 기사가 나옴). 키워드 2개가 강하게 걸려 원래는 "pass"였을 기사라도,
    # 검색에 쓴 법인명/별칭/베이스명이 본문에 전혀 없으면 issues로 바로
    # 보내지 않고 ambiguous로 내려서 사람이 확인하게 한다 — 회사 표기가
    # 별칭 사전에 없는 축약형이라 실제로는 맞는 매칭일 수도 있어서,
    # 조용히 버리면 재현율이 떨어진다.
    responses = {
        # 검색어는 법인격 표기를 뗀 이름이다.
        "가나다전자": [
            {
                "title": "완전히 다른 회사 유상증자 소식",
                "description": "마바사그룹이 유상증자와 회사채 발행을 함께 결정했다는 소식이 전해졌다",
                "originallink": "http://example.com/unrelated",
                "link": "http://news.naver.com/unrelated",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
        "유상증자": [],
        "회사채": [],
    }
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient(responses))

    result_paths = pipeline.run_pipeline(
        config_path=config_path,
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
    )

    issues = json.loads(result_paths["issues"].read_text(encoding="utf-8"))
    assert issues["issues_by_corp"] == {}

    review = json.loads(result_paths["llm_review_batch"].read_text(encoding="utf-8"))
    ambiguous = review["ambiguous_existing_corp_issues"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["matched_corp"] == "가나다전자(주)"
    assert ambiguous[0]["needs_llm_verdict"] is True


def test_article_id_is_stable_across_processes():
    import subprocess
    import sys
    from pathlib import Path

    code = "import pipeline; print(pipeline._article_id({'originallink': 'http://example.com/a'}))"
    cwd = Path(pipeline.__file__).parent
    ids = {
        subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True).stdout
        for _ in range(2)
    }
    assert len(ids) == 1
    assert ids.pop().strip() == pipeline._article_id({"originallink": "http://example.com/a"})


def test_track1_auto_pass_off_sends_confirmed_strong_matches_to_llm(
    monkeypatch, corp_csv, config_path, tmp_path
):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["track1_auto_pass"] = False
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
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
    }
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient(responses))

    result_paths = pipeline.run_pipeline(
        config_path=config_path,
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
    )

    issues = json.loads(result_paths["issues"].read_text(encoding="utf-8"))
    review = json.loads(result_paths["llm_review_batch"].read_text(encoding="utf-8"))
    assert issues["issues_by_corp"] == {}
    [record] = review["ambiguous_existing_corp_issues"]
    assert record["matched_corp"] == "가나다전자(주)"
    # 이름은 확인됐으므로 "법인명 미확인" 표시는 붙지 않는다
    assert record["needs_llm_verdict"] is False


def test_prefetch_meta_fetches_unique_links_in_parallel():
    import threading
    import time

    calls: list[str] = []
    lock = threading.Lock()

    def slow_fetcher(link):
        with lock:
            calls.append(link)
        time.sleep(0.2)
        return MetaFetchResult(text=f"meta {link}", source="og:description")

    links = [f"http://example.com/{i}" for i in range(16)] + ["http://example.com/0"]
    started = time.monotonic()
    result = pipeline._prefetch_meta(links, slow_fetcher)

    assert sorted(calls) == sorted(set(links))  # 같은 링크는 한 번만
    assert result["http://example.com/3"].text == "meta http://example.com/3"
    # 순서대로면 16 x 0.2초 = 3.2초. 병렬이면 1초 안쪽.
    assert time.monotonic() - started < 1.5


def test_save_dropped_records_known_corp_articles_without_keywords(
    monkeypatch, corp_csv, config_path, tmp_path
):
    responses = {
        "가나다전자": [
            {
                "title": "가나다전자, 현금성 자산 1조 돌파",
                "description": "가나다전자가 쌓아둔 현금이 1조원을 넘었다",
                "originallink": "http://example.com/cash",
                "link": "http://news.naver.com/cash",
                "pubDate": "Wed, 16 Sep 2026 09:00:00 +0900",
            }
        ],
    }
    monkeypatch.setattr(pipeline, "_build_client", lambda config: FakeClient(responses))

    kwargs = dict(
        config_path=config_path,
        corp_csv_path=corp_csv,
        output_dir=tmp_path / "output",
        run_date="2026-09-16",
        date_from="2026-09-16",
        date_to="2026-09-16",
    )
    assert "track1_dropped" not in pipeline.run_pipeline(**kwargs)

    paths = pipeline.run_pipeline(save_dropped=True, **kwargs)
    dropped = json.loads(paths["track1_dropped"].read_text(encoding="utf-8"))
    assert dropped["dropped_count"] == 1
    assert dropped["items"][0]["matched_corp"] == "가나다전자(주)"
    assert dropped["items"][0]["title"] == "가나다전자, 현금성 자산 1조 돌파"


def test_group_same_article_keeps_one_item_with_other_corps():
    def record(corp, article_id, verdict_needed):
        return pipeline.IssueRecord(
            article_id=article_id, title="t", summary="s", originallink="l", link="l",
            pub_date="", matched_keywords=[], matched_corp=corp, corp_status="기존",
            needs_llm_verdict=verdict_needed,
        )

    grouped = pipeline._group_same_article(
        [record("별빛카드(주)", "A", True), record("(주)별빛은행", "A", False), record("(주)별빛은행", "B", False)]
    )
    assert [(r.article_id, r.matched_corp, r.also_matched_corps) for r in grouped] == [
        ("A", "(주)별빛은행", ["별빛카드(주)"]),  # 이름이 확인된 법인이 대표
        ("B", "(주)별빛은행", []),
    ]
