from summarize import summarize


def test_normal_description_is_used_as_is():
    result = summarize("삼성전자가 유상증자를 통해 1조원을 조달한다고 밝혔다.")
    assert result.needs_llm_polish is False
    assert "1조원" in result.summary


def test_short_description_flagged_for_polish():
    result = summarize("발표함.")
    assert result.needs_llm_polish is True


def test_description_is_html_cleaned():
    result = summarize("<b>삼성전자</b>가 오늘 발표한 내용은 다음과 같다&quot;공시&quot;")
    assert "<b>" not in result.summary
    assert "&quot;" not in result.summary


def test_description_starting_with_quote_flagged_for_polish():
    # 화자·주어가 잘려나가고 인용문 조각부터 시작하는 스니펫(길이는 충분해도
    # 문맥을 알 수 없는 경우가 많다).
    result = summarize(
        "“환헤지형과 환노출형을 나눠 담고 분할 매수하는 것이 환율 방향성에 대한 의존도를 낮추는 방법”이라고 조언했다."
    )
    assert result.needs_llm_polish is True


def test_description_just_under_threshold_flagged_for_polish():
    result = summarize("가" * 29)
    assert result.needs_llm_polish is True


def test_description_at_threshold_not_flagged():
    result = summarize("가" * 30)
    assert result.needs_llm_polish is False


def test_description_with_unclosed_quote_flagged_for_polish():
    # 실측 사례: 인용부호를 열고 문장이 끝나버려 인용 내용이 잘려나간 경우.
    result = summarize(
        "타래자동차그룹은 단기 차환보다 조달비용 상승이 변수다. 타래차 관계자는 "
        '"최근 미국 장기금리...'
    )
    assert result.needs_llm_polish is True


def test_description_with_balanced_quotes_not_flagged():
    result = summarize('타래차 관계자는 "장기금리가 올라 조달비용 부담이 커졌다"고 말했다.')
    assert result.needs_llm_polish is False


def test_description_with_unclosed_paren_flagged_for_polish():
    result = summarize(
        "하늘헤지자산운용(주가 이번 분기 환헤지형 ETF 자금 유입 규모를 크게 늘렸다고 밝혔다"
    )
    assert result.needs_llm_polish is True


def test_long_description_is_flagged_for_polish_but_not_cut():
    long_description = "삼성전자가 대규모 유상증자를 통해 반도체 설비 투자 자금을 조달한다고 밝혔으며 이는 업계 최대 규모로 평가된다는 분석이 나왔다"
    result = summarize(long_description)
    assert result.needs_llm_polish is True
    # 잘라내지 않는다 — 실제 압축 요약은 llm_review에서 원문 보고 새로 쓴다.
    assert result.summary == long_description
