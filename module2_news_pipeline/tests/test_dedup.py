from dedup import cluster_similar_issues, dedup_articles


def _article(title, pub_date="2026-09-16", originallink="http://example.com/a"):
    return {"title": title, "pubDate": pub_date, "originallink": originallink}


def test_exact_duplicate_titles_are_merged():
    articles = [
        _article("삼성전자, 유상증자 결정"),
        _article("삼성전자, 유상증자 결정"),
    ]
    result = dedup_articles(articles)
    assert len(result.kept) == 1
    assert result.duplicate_count == 1


def test_near_duplicate_titles_are_merged():
    articles = [
        _article("<b>삼성전자</b>, 유상증자 결정!!"),
        _article("삼성전자 유상증자 결정"),
    ]
    result = dedup_articles(articles, similarity_threshold=0.8)
    assert len(result.kept) == 1


def test_distinct_titles_are_kept_separate():
    articles = [
        _article("삼성전자, 유상증자 결정"),
        _article("LG화학, 회사채 발행"),
    ]
    result = dedup_articles(articles)
    assert len(result.kept) == 2
    assert result.duplicate_count == 0


def test_representative_prefers_article_with_originallink():
    articles = [
        {"title": "A사 유상증자", "pubDate": "2026-09-16", "originallink": ""},
        {"title": "A사 유상증자", "pubDate": "2026-09-15", "originallink": "http://real.example.com"},
    ]
    result = dedup_articles(articles)
    assert len(result.kept) == 1
    assert result.kept[0]["originallink"] == "http://real.example.com"


def _issue(article_id, title, summary, pub_date="2026-09-18"):
    return {
        "article_id": article_id,
        "title": title,
        "summary": summary,
        "link": f"http://example.com/{article_id}",
        "originallink": f"http://example.com/{article_id}",
        "pub_date": pub_date,
    }


def test_cluster_merges_same_event_with_different_headlines():
    # 실측 사례 축약판: 같은 사건(홈플러스 대형마트 67개점 매각)을 언론사마다
    # 완전히 다른 문구의 헤드라인으로 보도했지만, 구체적 수치(67, 19)는 겹친다.
    records = [
        _issue("1", "홈플러스, 대형마트 67개점 M&A 착수…폐점 점포 19곳도 매각", "홈플러스가 대형마트 67개점과 폐점 점포 19곳 매각에 나선다."),
        _issue("2", "홈플러스 '본체'도 매물로…67개점·온라인·본사 통째로 M&A 착수", "매각대금이 회생계획에 반영될 경우 채권 변제 규모와 일정에도 영향을 미칠 수 있다."),
        _issue("3", "홈플러스, 67개 대형마트·본사·온라인 M&A 착수…폐점 19곳도 매각", "홈플러스가 회생계획에 따라 대형마트 67개점의 인수합병에 착수한다."),
    ]
    result = cluster_similar_issues(records)
    assert len(result) == 1
    assert len(result[0]["related_articles"]) == 2


def test_cluster_keeps_genuinely_different_events_separate():
    records = [
        _issue("1", "美 국채금리 5% 돌파…韓기업 '차환 폭탄' 돌아온다", "미국 국채금리 상승은 국내 회사채 금리를 끌어올려 차환이 필요한 기업의 부담을 키운다."),
        _issue("2", "토요타는 합치고 타래차는 키운다…엇갈린 중국 생존전략", "타래자동차는 베이징자동차그룹과 베이징타래에 11억달러를 투입하고 신차 20종을 출시하기로 했다."),
    ]
    result = cluster_similar_issues(records)
    assert len(result) == 2
    assert all("related_articles" not in r for r in result)


def test_cluster_flattens_related_articles_across_repeated_merges():
    # pipeline.py에서 한 번, merge_llm_results.py에서 한 번 더 실행돼도
    # 이전에 묶인 related_articles가 안 사라지는지 확인한다.
    first_pass = cluster_similar_issues(
        [
            _issue("1", "홈플러스 매각 착수, 67개점 대상", "홈플러스가 67개점 매각에 나선다."),
            _issue("2", "홈플러스, 67개점 새 주인 찾는다", "홈플러스가 매각 절차를 시작했다고 67개점 관련해 밝혔다."),
        ]
    )
    assert len(first_pass) == 1

    extra = _issue("3", "홈플러스 매각 절차 개시, 대상은 67개점", "홈플러스가 67개점을 대상으로 매각 절차를 시작한다.")
    second_pass = cluster_similar_issues(first_pass + [extra])

    assert len(second_pass) == 1
    related_ids = {a["article_id"] for a in second_pass[0]["related_articles"]}
    assert related_ids | {second_pass[0]["article_id"]} == {"1", "2", "3"}


def test_cluster_merges_issues_with_identical_summaries():
    # 헤드라인은 제각각이지만 검토자가 요약을 한 문장으로 통일한 경우.
    summary = "(주)파랑은행은 9월 28일 미국 뉴욕멜론은행과 국경 간 디지털자산 결제 및 송금서비스 업무협약을 체결했다."
    records = [
        _issue("1", "KB파랑은행, 美 뉴욕멜론은행과 국경간 디지털 결제·송금 협력 MOU 체결", summary),
        _issue("2", "[금융권 소식] KB파랑은행ㆍ신한금융ㆍBNK경남은행(9.29)", summary),
        _issue("3", "[금융 Pick] KB파랑은행, 카카오·BNY와 디지털금융 전선 확대", summary),
    ]
    result = cluster_similar_issues(records)
    assert len(result) == 1
    assert len(result[0]["related_articles"]) == 2


def test_cluster_ignores_shared_dates_between_different_events():
    # 2026-10-01 실측: 같은 은행의 다른 사건이 "28일"·"29일"이 겹친다는
    # 이유로 묶였다.
    records = [
        _issue("1", "KB파랑은행, BNY와 디지털자산 결제 협력", "KB파랑은행은 28일 BNY와 국경간 결제 MOU를 맺었다. 협약식은 29일 열렸다."),
        _issue("2", "KB파랑은행, 아인병원 주거래은행 맡는다", "KB파랑은행은 29일 아인의료재단과 주거래은행 협약을 28일 체결했다고 밝혔다."),
    ]
    assert len(cluster_similar_issues(records)) == 2


def test_event_numbers_keep_amounts_and_drop_dates():
    from dedup import _event_numbers

    assert _event_numbers("9월 28일 2026년 10시 1500억원 30bp 97.7% 2028") == {"1500", "30", "97.7"}


def _dedup_brute_force(articles, threshold):
    """후보 거르기 없이 모든 그룹과 차례로 비교하던 원래 방식(비교 기준)."""
    from dedup import _pick_representative, _similar
    from text_utils import normalize_title

    groups, norms, exact = [], [], {}
    for article in articles:
        norm = normalize_title(article.get("title", ""))
        placed = exact.get(norm)
        if placed is None and norm:
            placed = next(
                (i for i, existing in enumerate(norms) if existing and _similar(norm, existing, threshold)),
                None,
            )
        if placed is not None:
            groups[placed].append(article)
        else:
            exact.setdefault(norm, len(groups))
            groups.append([article])
            norms.append(norm)
    return [_pick_representative(g) for g in groups]


def test_dedup_matches_brute_force_on_random_titles():
    import random

    rng = random.Random(7)
    words = ["삼성", "은행", "매각", "인수", "지분", "3천억", "단독", "신임", "대표", "선임", "증자", "MOU", "…", " "]
    for _ in range(200):
        articles = []
        for i in range(rng.randint(0, 40)):
            if articles and rng.random() < 0.4:
                # 앞 기사 제목을 조금 바꾼 거의 같은 제목
                title = list(rng.choice(articles)["title"])
                if title:
                    title[rng.randrange(len(title))] = rng.choice("가나다 1")
                title = "".join(title)
            else:
                title = "".join(rng.choice(words) for _ in range(rng.randint(0, 8)))
            articles.append({"title": title, "pubDate": f"Wed, 16 Sep 2026 0{i % 10}:00:00 +0900", "originallink": ""})
        for threshold in (0.6, 0.82, 0.95):
            got = dedup_articles(articles, threshold).kept
            want = _dedup_brute_force(articles, threshold)
            assert [id(a) for a in got] == [id(a) for a in want]
