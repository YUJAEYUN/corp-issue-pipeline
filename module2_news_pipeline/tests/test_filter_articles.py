from filter_articles import filter_article

KEYWORD_GROUPS = {
    "자금조달": ["유상증자", "회사채", "전환사채"],
    "기업이벤트": ["M&A", "지분 매각"],
}
GROUP_TO_SIGNAL = {
    "자금조달": "조달 니즈 발생",
    "기업이벤트": "기업 이벤트",
}


def test_no_keyword_hit_is_rejected():
    article = {"title": "오늘의 날씨", "description": "맑음"}
    result = filter_article(article, KEYWORD_GROUPS, GROUP_TO_SIGNAL)
    assert result.verdict == "reject"
    assert result.matched_keywords == []


def test_single_weak_hit_is_ambiguous_by_default():
    article = {"title": "A사, 유상증자 검토설 일축", "description": "루머에 불과하다고 밝혔다"}
    result = filter_article(article, KEYWORD_GROUPS, GROUP_TO_SIGNAL, weak_match_keyword_count=1)
    assert result.verdict == "ambiguous"
    assert result.matched_keywords == ["유상증자"]


def test_multiple_hits_pass():
    article = {
        "title": "A사, 유상증자 이어 회사채 발행",
        "description": "전환사채 발행도 함께 검토",
    }
    result = filter_article(article, KEYWORD_GROUPS, GROUP_TO_SIGNAL, weak_match_keyword_count=1)
    assert result.verdict == "pass"
    assert result.keyword_group == "자금조달"
    assert result.sales_signal_category == "조달 니즈 발생"
    assert set(result.matched_keywords) == {"유상증자", "회사채", "전환사채"}


def test_keyword_embedded_in_own_corp_name_is_excluded():
    # "하늘헤지자산운용"을 검색해서 나온 기사인데, 실제 본문은 그 회사와
    # 무관하고 회사명 자체에 "헤지"/"자산운용"이 들어있을 뿐인 경우.
    article = {
        "title": "코스피200 ETF, 뉴욕증시 재도전",
        "description": "하늘헤지자산운용 관계자는 언급하지 않았다. 환헤지형 상품도 있다.",
    }
    groups = {"운용전략": ["자산운용", "리밸런싱"], "알파딜": ["헤지"]}
    signal = {"운용전략": "운용·헤지 전략", "알파딜": "알파딜/구조화"}

    without_exclusion = filter_article(article, groups, signal, weak_match_keyword_count=1)
    assert without_exclusion.verdict == "pass"  # 회사명 안의 "헤지"+"자산운용"으로 오탐

    with_exclusion = filter_article(
        article, groups, signal, weak_match_keyword_count=1, exclude_names=["하늘헤지자산운용"]
    )
    # "환헤지형"의 "헤지"는 남아있지만 "자산운용"은 회사명과 함께 지워져 1개만 남는다
    assert with_exclusion.verdict == "ambiguous"
    assert with_exclusion.matched_keywords == ["헤지"]


def test_best_group_picked_by_hit_count():
    article = {
        "title": "A사 M&A 추진, 지분 매각도 검토",
        "description": "유상증자 가능성은 낮다고 언급",
    }
    result = filter_article(article, KEYWORD_GROUPS, GROUP_TO_SIGNAL, weak_match_keyword_count=1)
    assert result.verdict == "pass"
    assert result.keyword_group == "기업이벤트"
    assert result.sales_signal_category == "기업 이벤트"


def test_always_review_group_hit_is_ambiguous_even_with_many_keywords():
    groups = {"조달니즈": ["유상증자"], "인사변동": ["은행장", "연임", "임추위"]}
    signals = {"조달니즈": "조달 니즈 발생", "인사변동": "인사 변동"}
    article = {"title": "가나은행장 연임 확정", "description": "임추위가 현 은행장의 연임을 추천했다"}

    assert filter_article(article, groups, signals).verdict == "pass"
    result = filter_article(article, groups, signals, always_review_groups={"인사변동"})
    assert result.verdict == "ambiguous"
    assert result.sales_signal_category == "인사 변동"


def test_always_review_group_does_not_affect_articles_without_its_keywords():
    groups = {"조달니즈": ["유상증자", "회사채"], "인사변동": ["은행장"]}
    signals = {"조달니즈": "조달 니즈 발생", "인사변동": "인사 변동"}
    article = {"title": "가나전자 유상증자 이어 회사채 발행", "description": ""}

    result = filter_article(article, groups, signals, always_review_groups={"인사변동"})
    assert result.verdict == "pass"


def test_noise_reason_flags_photo_and_analyst_only_mentions():
    from filter_articles import noise_reason

    assert noise_reason({"title": "[포토]조병규, 강렬한 눈빛", "description": ""}, ["가온대학교"]) == "사진 기사"
    analyst = {"title": "환율, 1350원대로", "description": "김유미 누리증권 연구원은 “지표가 부진했다”"}
    assert noise_reason(analyst, ["누리증권"]) == "애널리스트 인용"
    # 법인 자신의 사건이면 연구원 언급이 있어도 거르지 않는다
    own = {"title": "누리증권, 발행어음 5배 키워", "description": "김유미 누리증권 연구원은 말했다"}
    assert noise_reason(own, ["누리증권"]) is None
