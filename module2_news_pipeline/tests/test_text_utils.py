from text_utils import clean_text, normalize_title


def test_clean_text_strips_tags_and_entities():
    raw = "삼성전자, <b>유상증자</b> 결정&quot;공시&quot;"
    assert clean_text(raw) == '삼성전자, 유상증자 결정"공시"'


def test_normalize_title_ignores_punctuation_and_case():
    a = normalize_title("<b>ABC</b> 그룹, 회사채 발행!")
    b = normalize_title("ABC 그룹 회사채 발행")
    assert a == b


def test_normalize_title_differs_for_different_content():
    assert normalize_title("삼성전자 유상증자") != normalize_title("LG전자 유상증자")
