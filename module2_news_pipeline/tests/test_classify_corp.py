import csv

import pytest

from classify_corp import CorpDirectory, compute_alias, compute_base_name, compute_search_name


def test_compute_alias_applies_prefix_substitution():
    prefix_map = [["케이비", "KB"], ["에스케이", "SK"]]
    assert compute_alias("케이비사랑증권 주식회사", prefix_map) == "KB사랑증권 주식회사"
    assert compute_alias("에스케이새봄 주식회사", prefix_map) == "SK새봄 주식회사"


def test_compute_alias_returns_empty_when_no_prefix_matches():
    assert compute_alias("삼성전자(주)", [["케이비", "KB"]]) == ""


def test_compute_base_name_strips_fof_suffix():
    assert compute_base_name("마루생명보험(주)_FOF", "_FOF") == "마루생명보험(주)"
    assert compute_base_name("삼성전자(주)", "_FOF") == "삼성전자(주)"


@pytest.fixture
def corp_csv(tmp_path):
    path = tmp_path / "corp_team_mapping.csv"
    rows = [
        {"corp_name": "삼성전자(주)", "team": "1"},
        {"corp_name": "한국바다금융(주)", "team": "1"},
        {"corp_name": "한국바다금융(주)", "team": "2"},
        {"corp_name": "케이비사랑증권 주식회사", "team": "4"},
        {"corp_name": "마루생명보험(주)_FOF", "team": "3"},
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["corp_name", "team"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_multi_team_corp_returns_all_teams(corp_csv):
    directory = CorpDirectory.load(corp_csv)
    match = directory.find_in_text("한국바다금융(주)이 오늘 발표했다")
    assert match is not None
    assert match.corp_name == "한국바다금융(주)"
    assert match.teams == ["1", "2"]


def test_alias_match_resolves_to_canonical_corp(corp_csv):
    directory = CorpDirectory.load(corp_csv)
    match = directory.find_in_text("KB사랑증권 주식회사가 자금을 조달했다")
    assert match is not None
    assert match.corp_name == "케이비사랑증권 주식회사"
    assert match.teams == ["4"]


def test_no_match_returns_none(corp_csv):
    directory = CorpDirectory.load(corp_csv)
    assert directory.find_in_text("전혀 관련 없는 어떤 회사 얘기") is None


def test_is_known_and_teams_for(corp_csv):
    directory = CorpDirectory.load(corp_csv)
    assert directory.is_known("삼성전자(주)") is True
    assert directory.is_known("존재하지않는회사") is False
    assert directory.teams_for("한국바다금융(주)") == ["1", "2"]


@pytest.mark.parametrize(
    "corp_name, expected",
    [
        ("가람투자증권주식회사", "가람투자증권"),
        ("(주) 다온투자증권", "다온투자증권"),
        ("주식회사 자람금융지주", "자람금융지주"),
        ("다솜생명보험(주)_FOF", "다솜생명보험"),
        ("(사)차오름공제회", "차오름공제회"),
        ("카라제일차 주식회사", "카라제일차"),
        ("타래자동차", "타래자동차"),
    ],
)
def test_compute_search_name_strips_legal_markers(corp_name, expected):
    assert compute_search_name(corp_name) == expected


def test_directory_matches_news_spelling_without_legal_marker(tmp_path):
    path = tmp_path / "corp.csv"
    path.write_text(
        "corp_name,team\n가람투자증권주식회사,3\n(주)가람,2\n", encoding="utf-8"
    )
    directory = CorpDirectory.load(path)

    match = directory.find_in_text("가람투자증권, 5000억원 유상증자 결의")
    assert match is not None and match.corp_name == "가람투자증권주식회사"
    # "가람"는 2자라 매칭 키로 쓰지 않는다. 계열사 기사를 그룹사로 오인하지 않는다.
    assert directory.find_in_text("가람생명, 애큐온 인수") is None
