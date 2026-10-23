"""모듈 2 파이프라인 조립 (FR-M2-1~10).

2트랙 수집 구조:
  트랙 1 (법인명 검색, FR-M2-2): 관리 중인 법인명으로 각각 검색한다.
    결과는 검색에 쓴 그 법인 자신의 뉴스이므로 항상 "기존 거래 법인"으로
    분류되고, source-of-truth CSV에 있는 소속 팀 전부에 배정된다.
  트랙 2 (키워드 검색, 신규 법인 탐지): §8.1 키워드로 법인명 없이
    검색한다. 결과 기사에 관리 중인 법인이 하나라도 언급되면 트랙1과
    중복이므로 버리고, 아무도 매칭되지 않으면 "신규 후보"로 분류해
    LLM 리뷰 배치로 넘긴다 — 어느 회사 얘기인지 자동으로 확정하지 않는다.

각 트랙 모두 최종 필터 통과 여부가 애매한 건, 요약이 부실한 건은 전부
LLM 리뷰 배치 파일로 모아, 담당자가 Claude Code 세션에서 판단하게 한다
(스펙 NFR-4 — 종량제 API 대신 구독 사용량으로 처리).

수집 기간(FR-M2-1, 기본 D-1)은 네이버 API 자체에는 없는 기능이라
`_fetch_in_range()`가 date(최신순) 정렬을 이용해 직접 페이지를 넘기며
pubDate로 걸러낸다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from article_meta import fetch_meta_description, is_usable_meta_summary
from classify_corp import (
    MIN_NORMALIZED_KEY_LENGTH,
    CorpDirectory,
    compute_alias,
    compute_base_name,
    compute_search_name,
    normalized_names,
)
from date_utils import default_d1_range, in_range, month_folder_name, parse_pub_date, today_kst
from dedup import cluster_similar_issues, dedup_articles
from filter_articles import filter_article, noise_reason
from naver_client import NaverNewsClient
from summarize import summarize
from text_utils import clean_text

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CORP_CSV_PATH = REPO_ROOT / "data" / "corp_team_mapping" / "corp_team_mapping.csv"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"
NAVER_MAX_START = 1000  # 네이버 API가 start=1000 초과를 거부한다(실측 확인, HTTP 400 SE03).
# 재현율 우선(NFR-7) 원칙에 따라 캡은 실제로 자르기 위해서가 아니라, 이 API의
# 물리적 한계(쿼리당 최대 1000건)까지는 항상 다 긁어오기 위해 존재한다.
MAX_PAGES_PER_QUERY = NAVER_MAX_START // 100  # 법인명 검색(트랙1)
TRACK2_MAX_PAGES = NAVER_MAX_START // 100  # 키워드 전국 검색(트랙2) — 물량이 훨씬 크지만
# 못 보고 놓치는 것보다는 검토 물량이 느는 게 낫다는 판단(사용자 확인)


@dataclass
class IssueRecord:
    article_id: str
    title: str
    summary: str  # 기본은 네이버 API description 정제본. needs_llm_polish=True였다가
    # og:description 보강(article_meta)이 검증을 통과해 교체됐거나,
    # llm_review에서 실제로 다듬어진 경우에만 원래 description에서 바뀐다.
    originallink: str
    link: str
    pub_date: str
    matched_keywords: list[str]
    matched_corp: str
    corp_status: str  # "기존" | "신규후보"
    team: list[str] = field(default_factory=list)
    keyword_tag: str | None = None
    needs_llm_polish: bool = False
    needs_llm_verdict: bool = False
    # 같은 기사가 다른 관리 법인 검색에도 걸렸을 때 그 법인들. LLM 검토에는 기사를
    # 한 번만 보내고, 판정에서 실제 당사자 법인을 고르게 한다.
    also_matched_corps: list[str] = field(default_factory=list)


def _article_id(article: dict) -> str:
    """같은 기사는 실행이 달라도 같은 ID가 나와야 한다. 파이썬 hash()는
    프로세스마다 값이 바뀌어(PYTHONHASHSEED) 날짜 간 비교가 불가능했다."""
    basis = article.get("originallink") or article.get("link") or article.get("title", "")
    return str(int.from_bytes(hashlib.sha1(basis.encode("utf-8")).digest()[:8], "big"))


def _load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _all_keywords(keyword_groups: dict[str, list[str]]) -> list[str]:
    seen: list[str] = []
    for keywords in keyword_groups.values():
        for kw in keywords:
            if kw not in seen:
                seen.append(kw)
    return seen


@dataclass
class FetchResult:
    items: list[dict]
    # True면 date_from 이전 기사를 확인하기 전에 네이버 API의 물리적 한도
    # (start<=1000)에 걸려 멈췄다는 뜻 — 그날 기사를 못 다 긁었을 수 있다.
    truncated: bool = False


def _fetch_in_range(
    client: NaverNewsClient,
    query: str,
    date_from: date,
    date_to: date,
    max_pages: int = MAX_PAGES_PER_QUERY,
) -> FetchResult:
    """query로 검색하되, pubDate가 [date_from, date_to] 안인 기사만 남긴다
    (FR-M2-1). 네이버 API가 날짜 범위를 지원하지 않으므로, date(최신순)
    정렬을 이용해 직접 페이지를 넘기며 걸러내고, 범위보다 오래된 기사가
    나오면(더 넘겨봐도 전부 더 오래된 기사뿐이므로) 그 페이지를 마지막으로
    멈춘다. max_pages는 재현율을 일부러 줄이려는 캡이 아니라 네이버 API
    자체의 상한(start<=1000, NAVER_MAX_START)에 맞춘 것이라, 정상적으로는
    그 전에 reached_too_old로 먼저 멈춘다.
    """
    collected: list[dict] = []
    start = 1
    page_size = client.display

    for _ in range(max_pages):
        if start > NAVER_MAX_START:
            return FetchResult(items=collected, truncated=True)

        items = client.search(query, start=start)
        if not items:
            # 결과가 더 없으면 다 받은 것이다. break로 빠져나가면 아래의
            # "상한까지 다 썼다" 반환으로 떨어져, 기사가 0건인 법인이 매일
            # "수집 누락 가능"(검색 상한 도달)으로 잘못 표시됐다.
            return FetchResult(items=collected, truncated=False)

        for item in items:
            pub_date = parse_pub_date(item.get("pubDate", ""))
            if pub_date is not None and in_range(pub_date, date_from, date_to):
                collected.append(item)

        last_pub_date = parse_pub_date(items[-1].get("pubDate", ""))
        reached_too_old = last_pub_date is not None and last_pub_date.date() < date_from
        if reached_too_old or len(items) < page_size:
            return FetchResult(items=collected, truncated=False)
        start += page_size

    # max_pages를 다 썼는데도 reached_too_old를 못 만났다 = 더 있을 수 있다.
    return FetchResult(items=collected, truncated=True)


def _fetch_all(
    client,
    queries: list[str],
    date_from: date,
    date_to: date,
    max_pages: int,
    workers: int,
    on_done=None,
) -> list[tuple[FetchResult, float]]:
    """queries를 동시에 검색해 (결과, 걸린 초)를 queries와 같은 순서로 돌려준다.

    on_done(끝난 개수, 인덱스, 결과, 걸린 초)은 쿼리가 끝날 때마다(끝난
    순서대로) 메인 스레드에서 불린다. 하나라도 실패하면 아직 시작하지 않은
    쿼리는 취소하고 그 예외를 그대로 올린다.
    """

    def task(index: int) -> tuple[int, FetchResult, float]:
        started = time.monotonic()
        result = _fetch_in_range(client, queries[index], date_from, date_to, max_pages=max_pages)
        return index, result, time.monotonic() - started

    results: list[tuple[FetchResult, float] | None] = [None] * len(queries)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = [pool.submit(task, i) for i in range(len(queries))]
        for done, future in enumerate(as_completed(futures), 1):
            index, result, elapsed = future.result()
            results[index] = (result, elapsed)
            if on_done:
                on_done(done, index, result, elapsed)
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return results  # type: ignore[return-value]


def _build_client(config: dict) -> NaverNewsClient:
    api_cfg = config["naver_api"]
    variant = api_cfg.get("variant", "hub")
    endpoint = api_cfg["hub_endpoint"] if variant == "hub" else api_cfg["legacy_endpoint"]
    return NaverNewsClient.from_env(
        variant=variant,
        endpoint=endpoint,
        display=api_cfg.get("display", 100),
        sort=api_cfg.get("sort", "date"),
        client_id_env=api_cfg.get("client_id_env", "NAVER_CLIENT_ID"),
        client_secret_env=api_cfg.get("client_secret_env", "NAVER_CLIENT_SECRET"),
    )


META_FETCH_WORKERS = 32
# 네이버 검색은 법인 429곳 + 키워드 94개를 한 쿼리씩 차례로 부르면 호출당
# 1.1초씩 14분이 걸렸다(2026-10-15 실행). 쿼리끼리는 서로 기다릴 이유가
# 없어 여러 개를 동시에 부른다. 한 쿼리의 페이지는 여전히 차례로 넘기고,
# 결과는 쿼리 순서대로 처리하므로 산출물은 순차 실행과 같다. 429(호출 한도)가
# 나면 클라이언트가 물러났다가 다시 부른다. config.json의
# naver_api.search_workers로 바꿀 수 있다.
NAVER_SEARCH_WORKERS = 4


def _log(message: str) -> None:
    # 수집이 몇 시간씩 걸려도 어디서 느린지 Actions 로그로 볼 수 있게 한다.
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class _Progress:
    """지금 어느 단계의 무엇을 하고 있는지 기록하고, 60초마다 그 상태를 찍는다.

    한 요청이 멈춰도 "원문 메타 812/1500, 마지막 진행 4분 전, 처리 중 URL"처럼
    어디서 멈췄는지 실행 중 로그에서 바로 보이게 한다(2026-10-01: 진행 로그
    없이 3시간, 90분씩 멈춘 실행을 원인 모른 채 취소했다).
    """

    def __init__(self, interval: float = 60.0):
        self.stage = "시작"
        self.detail = ""
        self.last_change = time.monotonic()
        self.started = time.monotonic()
        self.naver_calls = 0
        self.naver_seconds = 0.0
        self.naver_retries = 0
        self.in_flight: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, args=(interval,), daemon=True)

    def start(self) -> "_Progress":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def set(self, stage: str, detail: str = "") -> None:
        with self._lock:
            self.stage, self.detail = stage, detail
            self.last_change = time.monotonic()

    def touch(self, detail: str) -> None:
        with self._lock:
            self.detail = detail
            self.last_change = time.monotonic()

    def begin(self, what: str) -> None:
        with self._lock:
            self.in_flight[threading.get_ident()] = (what, time.monotonic())

    def end(self) -> None:
        with self._lock:
            self.in_flight.pop(threading.get_ident(), None)

    def _beat(self, interval: float) -> None:
        while not self._stop.wait(interval):
            now = time.monotonic()
            with self._lock:
                idle = now - self.last_change
                slowest = sorted(self.in_flight.values(), key=lambda v: v[1])[:3]
                calls, secs, retries = self.naver_calls, self.naver_seconds, self.naver_retries
                stage, detail = self.stage, self.detail
            line = (
                f"[진행 중] 경과 {(now - self.started) / 60:.1f}분 | {stage} {detail} | "
                f"마지막 진행 {idle:.0f}초 전 | 네이버 호출 {calls}회 "
                f"(평균 {secs / calls if calls else 0:.2f}초, 3초 넘은 호출 {retries}회)"
            )
            if slowest:
                line += " | 오래 걸리는 요청: " + ", ".join(
                    f"{what[:80]} {now - since:.0f}초" for what, since in slowest
                )
            _log(line)


class _TimedClient:
    """네이버 호출 횟수·시간·재시도를 _Progress에 기록하고, 5초 넘는 호출을 찍는다."""

    def __init__(self, client, progress: _Progress):
        self._client = client
        self._progress = progress
        self.display = client.display

    def search(self, query: str, start: int = 1) -> list[dict]:
        self._progress.begin(f"네이버 '{query}' start={start}")
        started = time.monotonic()
        try:
            return self._client.search(query, start=start)
        finally:
            elapsed = time.monotonic() - started
            self._progress.end()
            with self._progress._lock:
                self._progress.naver_calls += 1
                self._progress.naver_seconds += elapsed
                if elapsed > 3:
                    # 재시도(백오프)가 끼면 3초를 넘는다.
                    self._progress.naver_retries += 1
            if elapsed > 5:
                _log(f"  느린 네이버 호출 {elapsed:.1f}초: '{query}' start={start}")


def _needs_meta(article: dict) -> str | None:
    """og:description 보강 대상이면 그 링크를, 아니면 None."""
    if not summarize(article.get("description", "")).needs_llm_polish:
        return None
    return article.get("originallink") or article.get("link") or None


def _prefetch_meta(links: list[str], meta_fetcher, progress: "_Progress | None" = None) -> dict:
    """원문 메타 요청을 병렬로 미리 받아 둔다. 기사마다 순서대로 최대 10초씩
    기다리면 관리 법인 기사가 수천 건일 때 수집이 몇 시간으로 늘어난다
    (2026-10-01 브랜치 실행: 3시간 넘게 끝나지 않음)."""
    unique = list(dict.fromkeys(links))
    if meta_fetcher is None or not unique:
        return {}

    def fetch(link: str):
        if progress:
            progress.begin(f"원문 {link}")
        started = time.monotonic()
        try:
            return meta_fetcher(link)
        finally:
            if progress:
                progress.end()
            elapsed = time.monotonic() - started
            if elapsed > 12:
                _log(f"  느린 원문 조회 {elapsed:.1f}초: {link}")

    results: dict = {}
    ok = 0
    with ThreadPoolExecutor(max_workers=META_FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch, link): link for link in unique}
        for done, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results[futures[future]] = result
            ok += bool(result and result.text)
            if progress:
                progress.touch(f"{done}/{len(unique)}")
            if done % 200 == 0 or done == len(unique):
                _log(f"  원문 메타 {done}/{len(unique)} (메타 있음 {ok}건)")
    return results


def _group_same_article(records: list[IssueRecord]) -> list[IssueRecord]:
    """같은 기사(같은 article_id)가 여러 관리 법인 검색에 걸리면 한 건으로 묶는다.

    2026-10-01 재수집: 별빛금융 CEO 승계 기사 하나가 별빛은행·별빛카드·별빛증권
    등 6개 법인 검색에 각각 걸려, 애매 1,684건이 실제 기사로는 821건이었다.
    첫 번째 법인을 대표로 두고 나머지는 also_matched_corps에 남긴다. 법인명이
    본문에서 확인된 쪽을 대표로 고른다.
    """
    groups: dict[str, list[IssueRecord]] = {}
    for record in records:
        groups.setdefault(record.article_id, []).append(record)

    grouped: list[IssueRecord] = []
    for members in groups.values():
        members.sort(key=lambda r: r.needs_llm_verdict)  # 이름 확인된 법인 먼저
        primary = members[0]
        for other in members[1:]:
            if other.matched_corp != primary.matched_corp and other.matched_corp not in primary.also_matched_corps:
                primary.also_matched_corps.append(other.matched_corp)
        grouped.append(primary)
    return grouped


def _to_issue_record(
    article: dict,
    filter_result,
    corp_status: str,
    matched_corp: str,
    teams: list[str],
    meta_fetcher=None,
) -> IssueRecord:
    summary_result = summarize(article.get("description", ""))
    summary = summary_result.summary
    needs_llm_polish = summary_result.needs_llm_polish

    if needs_llm_polish and meta_fetcher is not None:
        link = article.get("originallink") or article.get("link", "")
        if link:
            meta_result = meta_fetcher(link)
            candidate = meta_result.text if meta_result else None
            if candidate and is_usable_meta_summary(
                candidate, article.get("title", ""), matched_corp
            ):
                summary = clean_text(candidate)
                needs_llm_polish = False

    return IssueRecord(
        article_id=_article_id(article),
        title=clean_text(article.get("title", "")),
        summary=summary,
        originallink=article.get("originallink") or article.get("link", ""),
        link=article.get("link", ""),
        pub_date=article.get("pubDate", ""),
        matched_keywords=filter_result.matched_keywords,
        matched_corp=matched_corp,
        corp_status=corp_status,
        team=teams,
        keyword_tag=filter_result.sales_signal_category,
        needs_llm_polish=needs_llm_polish,
        needs_llm_verdict=(filter_result.verdict == "ambiguous"),
    )


def run_pipeline(
    config_path: Path = DEFAULT_CONFIG_PATH,
    corp_csv_path: Path = DEFAULT_CORP_CSV_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    run_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    meta_fetcher=fetch_meta_description,
    save_dropped: bool = False,
) -> dict[str, Path]:
    """meta_fetcher: needs_llm_polish로 걸린 기사만 og:description으로
    보강할 때 쓰는 함수. 기본은 실제 네트워크 요청(article_meta.fetch_meta_description).
    테스트에서는 대역으로 교체하고, config의 enable_meta_description_fallback을
    false로 두면(또는 meta_fetcher=None을 넘기면) 이 보강 단계를 건너뛴다.
    """
    config = _load_config(config_path)
    if not config.get("enable_meta_description_fallback", True):
        meta_fetcher = None
    directory = CorpDirectory.load(
        corp_csv_path,
        alias_prefix_map=config.get("corp_alias_prefix_map"),
        fof_suffix=config.get("corp_fof_suffix", "_FOF"),
    )
    progress = _Progress().start()
    client = _TimedClient(_build_client(config), progress)

    keyword_groups = config["keyword_groups"]
    group_to_signal = config["keyword_group_to_sales_signal"]
    weak_match_count = config.get("weak_match_keyword_count", 1)
    dedup_threshold = config.get("dedup_title_similarity_threshold", 0.82)
    always_review_groups = set(config.get("always_llm_review_keyword_groups") or [])
    # 관리 법인 기사도 이름 확인 + 키워드 2개 이상이면 LLM 판정 없이 바로
    # 통과시키는 경로. 2026-10-01 배치로 재현해 보니 이 경로로 갈 6건이 전부
    # LLM이 반려한 기사(애널리스트 코멘트, 통계 인용, 계열사 오매칭)라서
    # 운영 설정에서는 끈다. 키워드 수로는 그 법인 자신의 사건인지 알 수 없다.
    track1_auto_pass = config.get("track1_auto_pass", True)
    search_workers = config.get("naver_api", {}).get("search_workers", NAVER_SEARCH_WORKERS)

    default_from, default_to = default_d1_range()
    collect_from = date.fromisoformat(date_from) if date_from else default_from
    collect_to = date.fromisoformat(date_to) if date_to else default_to

    issues: list[IssueRecord] = []
    llm_review_ambiguous: list[IssueRecord] = []
    llm_review_new_candidates: list[dict[str, Any]] = []
    # 네이버 API 물리적 한도(start<=1000)에 걸려 그날 기사를 다 못 긁었을
    # 수 있는 법인/키워드 — 담당자가 "이 결과는 하한선일 수 있다"고 알 수
    # 있도록 산출물에 그대로 남긴다.
    truncated_corps: list[str] = []
    truncated_keywords: list[str] = []

    # --- 트랙 1: 법인명 검색 (FR-M2-2, FR-M2-5, FR-M2-6) ---
    alias_prefix_map = config.get("corp_alias_prefix_map") or []
    fof_suffix = config.get("corp_fof_suffix", "_FOF")
    known_corp_names = sorted({row for row in directory._teams_by_corp})
    all_keyword_set = _all_keywords(keyword_groups)
    track1_started = time.monotonic()
    progress.set("트랙1 법인명 검색", f"0/{len(known_corp_names)}")
    # (기사, 필터 결과, 법인명, 이름 확인 여부). 원문 메타를 병렬로 받은 뒤 레코드를 만든다.
    track1_hits: list[tuple[dict, Any, str, bool]] = []
    # 키워드가 하나도 안 걸려 버린 관리 법인 기사. save_dropped일 때만 파일로
    # 남긴다. 키워드 필터가 진짜 사건을 얼마나 버리는지 재기 위한 측정용이다.
    track1_dropped: list[dict[str, str]] = []
    noise_count = 0

    # 검색어는 법인격 표기를 뗀 이름으로 한다. "가람투자증권주식회사"로
    # 검색하면 "가람투자증권" 기사가 거의 안 나온다. 다만 뗀 이름이 너무
    # 짧으면("보령", "넥센") 지명·다른 회사 기사로 1000건이 차므로 원래
    # 이름으로 검색한다.
    search_names = []
    for corp_name in known_corp_names:
        search_name = compute_search_name(corp_name, fof_suffix)
        if len(search_name) < MIN_NORMALIZED_KEY_LENGTH:
            search_name = corp_name
        search_names.append(search_name)

    track1_fetched_articles = 0

    def _track1_done(done: int, index: int, fetch_result: FetchResult, elapsed: float) -> None:
        nonlocal track1_fetched_articles
        track1_fetched_articles += len(fetch_result.items)
        progress.touch(f"{done}/{len(known_corp_names)} '{search_names[index]}'")
        if elapsed > 15 or fetch_result.truncated:
            _log(
                f"  '{search_names[index]}': 기사 {len(fetch_result.items)}건, {elapsed:.0f}초"
                + (" (검색 상한 도달)" if fetch_result.truncated else "")
            )
        if done % 25 == 0:
            _log(
                f"트랙1 {done}/{len(known_corp_names)} 법인 검색, 기사 {track1_fetched_articles}건, "
                f"경과 {time.monotonic() - track1_started:.0f}초"
            )

    track1_fetched = _fetch_all(
        client,
        search_names,
        collect_from,
        collect_to,
        MAX_PAGES_PER_QUERY,
        search_workers,
        on_done=_track1_done,
    )
    _log(
        f"트랙1 네이버 검색 {len(search_names)}건, 동시 {search_workers}개, "
        f"{time.monotonic() - track1_started:.0f}초"
    )

    for corp_name, search_name, (fetch_result, _elapsed) in zip(
        known_corp_names, search_names, track1_fetched
    ):
        if fetch_result.truncated:
            truncated_corps.append(corp_name)
        deduped = dedup_articles(fetch_result.items, dedup_threshold).kept

        # 회사 이름 자체에 §8.1 키워드가 들어있는 경우(예: "하늘헤지자산운용"의
        # "헤지"/"자산운용") 기사 내용과 무관하게 매칭되는 걸 막기 위해, 이
        # 법인명·별칭·베이스명을 매칭 텍스트에서 제외한다.
        exclude_names = [
            corp_name,
            compute_alias(corp_name, alias_prefix_map),
            compute_base_name(corp_name, fof_suffix),
        ]
        # 정규화명은 본문에 실제로 나오는 이름이라, 키워드가 들어 있을 때만
        # 지운다. 무조건 지우면 "KB파랑은행장"에서 "파랑은행"이 빠져
        # "은행장" 키워드까지 사라진다.
        match_names = normalized_names(corp_name, alias_prefix_map, fof_suffix)
        exclude_names += [
            name for name in match_names if any(kw in name for kw in all_keyword_set)
        ]

        for article in deduped:
            noise = noise_reason(article, exclude_names + match_names)
            if noise:
                noise_count += 1
                if save_dropped:
                    track1_dropped.append(
                        {
                            "matched_corp": corp_name,
                            "search_name": search_name,
                            "reason": noise,
                            "title": clean_text(article.get("title", "")),
                            "description": clean_text(article.get("description", "")),
                            "link": article.get("originallink") or article.get("link", ""),
                            "pub_date": article.get("pubDate", ""),
                        }
                    )
                continue
            result = filter_article(
                article,
                keyword_groups,
                group_to_signal,
                weak_match_count,
                exclude_names,
                always_review_groups=always_review_groups,
            )
            if result.verdict == "reject":
                if save_dropped:
                    track1_dropped.append(
                        {
                            "matched_corp": corp_name,
                            "search_name": search_name,
                            "reason": "키워드 없음",
                            "title": clean_text(article.get("title", "")),
                            "description": clean_text(article.get("description", "")),
                            "link": article.get("originallink") or article.get("link", ""),
                            "pub_date": article.get("pubDate", ""),
                        }
                    )
                continue

            # 네이버 검색이 정확한 문구 매칭이 아니라 느슨한 관련도 매칭이라,
            # 법인명으로 검색해도 그 법인이 실제로는 전혀 언급되지 않은 기사가
            # 섞여 들어올 수 있다(실측 확인: "하늘헤지자산운용"으로 검색했는데
            # 실제로는 "하늘자산운용"·"미래에셋자산운용" 얘기인 기사가 나옴).
            # 다만 corp_name에 "(주)" 등 법인격 표기가 붙어 있어 뉴스 본문
            # 표기("NH농협"처럼 별칭 사전에 없는 축약형 등)와 문자 그대로는
            # 안 맞는 진짜 매칭도 많다 — 그래서 못 찾았다고 바로 버리지
            # 않고, 사람이 볼 수 있게 ambiguous로 내려보낸다(재현율 우선).
            combined_text = clean_text(article.get("title", "")) + " " + clean_text(
                article.get("description", "")
            )
            corp_name_confirmed = any(
                name and name in combined_text for name in exclude_names + match_names
            )

            track1_hits.append((article, result, corp_name, corp_name_confirmed))

    _log(f"잡음 기사(사진·애널리스트 인용) {noise_count}건 제외")
    _log(
        f"트랙1 검색 끝: 후보 {len(track1_hits)}건, "
        f"{time.monotonic() - track1_started:.0f}초"
    )
    meta_started = time.monotonic()
    meta_links = [link for article, *_ in track1_hits if (link := _needs_meta(article))]
    progress.set("원문 메타 조회", f"0/{len(set(meta_links))}")
    _log(f"원문 메타 조회 시작: {len(set(meta_links))}건, 병렬 {META_FETCH_WORKERS}")
    prefetched = _prefetch_meta(meta_links, meta_fetcher, progress)
    _log(f"원문 메타 {len(prefetched)}건 병렬 조회, {time.monotonic() - meta_started:.0f}초")

    for article, result, corp_name, corp_name_confirmed in track1_hits:
        record = _to_issue_record(
            article,
            result,
            corp_status="기존",
            matched_corp=corp_name,
            teams=directory.teams_for(corp_name),
            meta_fetcher=prefetched.get if meta_fetcher is not None else None,
        )
        if not corp_name_confirmed:
            record.needs_llm_verdict = True
        if result.verdict == "ambiguous" or not corp_name_confirmed or not track1_auto_pass:
            llm_review_ambiguous.append(record)
        else:
            issues.append(record)

    before_grouping = len(llm_review_ambiguous)
    llm_review_ambiguous = _group_same_article(llm_review_ambiguous)
    _log(f"같은 기사 묶기: 애매 {before_grouping}건 -> {len(llm_review_ambiguous)}건")

    # --- 트랙 2: 키워드 검색 (신규 법인 탐지) ---
    # 키워드 단독으로 전국 뉴스를 훑는 구조라 물량이 트랙1과는 차원이 다르게
    # 나온다(실측: 키워드 24개로 신규 후보 2062건). 사람이 매일 검토할 수
    # 있는 규모로 두 가지를 제한한다:
    #  - 페이지를 트랙1보다 적게 가져온다 (TRACK2_MAX_PAGES)
    #  - 키워드가 1개만 약하게 걸린 애매한 건은 후보로 올리지 않는다
    #    (verdict가 "pass"인 것만, 즉 트랙1의 ambiguous 기준과 동일한
    #    weak_match_keyword_count를 그대로 적용해 강한 신호만 통과)
    # 인사변동처럼 관리 법인에만 의미 있는 그룹은 트랙2에서 검색하지도,
    # 신규 후보의 키워드 수를 셀 때 넣지도 않는다.
    track1_only = set(config.get("track1_only_keyword_groups") or [])
    track2_keyword_groups = {
        group: keywords for group, keywords in keyword_groups.items() if group not in track1_only
    }
    all_keywords = _all_keywords(track2_keyword_groups)
    track2_raw: list[dict] = []
    progress.set("트랙2 키워드 검색", f"0/{len(all_keywords)}")
    track2_started = time.monotonic()
    def _track2_done(done: int, index: int, fetch_result: FetchResult, elapsed: float) -> None:
        keyword = all_keywords[index]
        progress.touch(f"{done}/{len(all_keywords)} '{keyword}'")
        _log(
            f"  트랙2 {done}/{len(all_keywords)} '{keyword}': {len(fetch_result.items)}건, "
            f"{elapsed:.0f}초" + (" (검색 상한 도달)" if fetch_result.truncated else "")
        )

    track2_fetched = _fetch_all(
        client,
        all_keywords,
        collect_from,
        collect_to,
        TRACK2_MAX_PAGES,
        search_workers,
        on_done=_track2_done,
    )
    # 결과는 키워드 순서대로 붙인다. 중복 제거가 먼저 나온 기사를 그룹
    # 대표로 삼으므로, 순서가 바뀌면 남는 기사가 달라진다.
    for keyword, (fetch_result, _elapsed) in zip(all_keywords, track2_fetched):
        if fetch_result.truncated:
            truncated_keywords.append(keyword)
        track2_raw.extend(fetch_result.items)
    _log(f"트랙2 검색 {time.monotonic() - track2_started:.0f}초, 동시 {search_workers}개")

    _log(f"트랙2 검색 끝: 원문 {len(track2_raw)}건")
    dedup_started = time.monotonic()
    progress.set("트랙2 중복 제거", f"{len(track2_raw)}건")
    track2_deduped = dedup_articles(track2_raw, dedup_threshold).kept
    _log(f"트랙2 중복 제거: {len(track2_deduped)}건, {time.monotonic() - dedup_started:.0f}초")
    progress.set("트랙2 필터·법인 매칭", f"0/{len(track2_deduped)}")
    for t2_index, article in enumerate(track2_deduped, 1):
        if t2_index % 500 == 0:
            progress.touch(f"{t2_index}/{len(track2_deduped)}")
        combined_text = clean_text(article.get("title", "")) + " " + clean_text(
            article.get("description", "")
        )
        match = directory.find_in_text(combined_text)
        if match is not None:
            # 이미 트랙1에서 해당 법인 검색으로 잡혔을 것이므로 중복으로 버린다.
            continue

        result = filter_article(article, track2_keyword_groups, group_to_signal, weak_match_count)
        if result.verdict != "pass":
            continue

        summary_result = summarize(article.get("description", ""))
        llm_review_new_candidates.append(
            {
                "article_id": _article_id(article),
                "title": clean_text(article.get("title", "")),
                "description": summary_result.summary,
                "originallink": article.get("originallink") or article.get("link", ""),
                "link": article.get("link", ""),
                "pub_date": article.get("pubDate", ""),
                "matched_keywords": result.matched_keywords,
                "candidate_sales_signal": result.sales_signal_category,
                "note": "이 기사에 언급된 법인 중 관리 리스트에 없는 곳이 있는지, "
                "있다면 어느 회사이고 영업 시그널로 볼 만한지 판단 필요",
            }
        )

    # date.today()는 러너의 시스템 타임존(GitHub Actions는 UTC)을 쓰므로
    # KST 자정 전후에 실행하면 하루 어긋난다 — 반드시 KST 기준으로 잡는다.
    run_date = run_date or today_kst().isoformat()
    # 날짜별 파일이 output/ 바로 밑에 계속 쌓이면 훑어보기 어려워지므로,
    # "2026년 9월"처럼 월 단위 폴더로 묶는다.
    output_dir = output_dir / month_folder_name(run_date)
    output_dir.mkdir(parents=True, exist_ok=True)

    issues_path = output_dir / f"issues_{run_date}.json"
    issues_by_corp: dict[str, list[dict]] = {}
    for record in issues:
        issues_by_corp.setdefault(record.matched_corp, []).append(asdict(record))
    for corp_name, corp_issues in issues_by_corp.items():
        issues_by_corp[corp_name] = cluster_similar_issues(corp_issues)
    issues_path.write_text(
        json.dumps(
            {
                "run_date": run_date,
                "collected_date_from": collect_from.isoformat(),
                "collected_date_to": collect_to.isoformat(),
                "issues_by_corp": issues_by_corp,
                "coverage_warnings": {
                    "possibly_incomplete_corps": truncated_corps,
                    "possibly_incomplete_keywords": truncated_keywords,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    review_path = output_dir / f"llm_review_batch_{run_date}.json"
    review_path.write_text(
        json.dumps(
            {
                "run_date": run_date,
                "collected_date_from": collect_from.isoformat(),
                "collected_date_to": collect_to.isoformat(),
                "ambiguous_existing_corp_issues": [asdict(r) for r in llm_review_ambiguous],
                "new_corp_candidates": llm_review_new_candidates,
                "summary_polish_needed": [
                    asdict(r) for r in issues + llm_review_ambiguous if r.needs_llm_polish
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 같은 날짜를 다시 수집하면 issues/batch가 새로 바뀌어 예전 LLM 판정과
    # 맞지 않는다. 결과 파일이 남아 있으면 검토 워크플로우가 "이미 검토됨"으로
    # 보고 건너뛰므로, 지워서 새 배치가 다시 검토되게 한다.
    stale_result_path = output_dir / f"llm_review_result_{run_date}.json"
    stale_result_path.unlink(missing_ok=True)

    paths = {"issues": issues_path, "llm_review_batch": review_path}
    if save_dropped:
        dropped_path = output_dir / f"track1_dropped_{run_date}.json"
        per_corp: dict[str, int] = {}
        for item in track1_dropped:
            per_corp[item["matched_corp"]] = per_corp.get(item["matched_corp"], 0) + 1
        dropped_path.write_text(
            json.dumps(
                {
                    "run_date": run_date,
                    "note": "관리 법인 검색 결과 중 키워드가 하나도 안 걸려 버린 기사(측정용)",
                    "kept_count": len(track1_hits),
                    "dropped_count": len(track1_dropped),
                    "dropped_by_corp": dict(sorted(per_corp.items(), key=lambda kv: -kv[1])),
                    "items": track1_dropped,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        paths["track1_dropped"] = dropped_path
    _log(
        f"완료: 트랙1 남긴 기사 {len(track1_hits)}건, 키워드 없어 버린 기사 "
        f"{len(track1_dropped) if save_dropped else '미측정'}, 애매 {len(llm_review_ambiguous)}건, "
        f"신규 후보 {len(llm_review_new_candidates)}건, 네이버 호출 {progress.naver_calls}회, "
        f"총 {(time.monotonic() - progress.started) / 60:.1f}분"
    )
    progress.stop()
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--corp-csv", type=Path, default=DEFAULT_CORP_CSV_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--date", type=str, default=None, help="출력 파일명에 쓸 실행 날짜 (기본: 오늘)")
    parser.add_argument(
        "--date-from", type=str, default=None, help="수집 대상 기간 시작일 (기본: D-1, 즉 어제)"
    )
    parser.add_argument(
        "--date-to", type=str, default=None, help="수집 대상 기간 종료일 (기본: D-1, 즉 어제)"
    )
    parser.add_argument(
        "--save-dropped",
        action="store_true",
        help="키워드가 안 걸려 버린 관리 법인 기사를 track1_dropped_<날짜>.json으로 남긴다(측정용)",
    )
    args = parser.parse_args()

    paths = run_pipeline(
        args.config,
        args.corp_csv,
        args.output_dir,
        args.date,
        args.date_from,
        args.date_to,
        save_dropped=args.save_dropped,
    )
    print(f"이슈: {paths['issues']}")
    print(f"LLM 리뷰 배치: {paths['llm_review_batch']}")


if __name__ == "__main__":
    main()
