import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generate_vba import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    DEFAULT_TEMPLATE_PATH,
    generate,
    load_config,
    render,
)


def test_default_config_has_six_teams():
    config = load_config(DEFAULT_CONFIG_PATH)
    assert config["team_numbers"] == [1, 2, 3, 4, 5, 6]


def test_check_column_ranges_match_spec():
    # 5.1.1절: 1~5팀 DS:EA, 6팀 FE:FM
    config = load_config(DEFAULT_CONFIG_PATH)
    ranges = config["check_column_ranges"]
    for team in ["1", "2", "3", "4", "5"]:
        assert ranges[team] == ["DS", "EA"]
    assert ranges["6"] == ["FE", "FM"]


def test_render_contains_sheet_matching_logic():
    config = load_config(DEFAULT_CONFIG_PATH)
    template = DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = render(template, config)

    # 시트명 공백 무시 매칭 (5.1.1절)
    assert 'Replace(ws.Name, " ", "")' in rendered
    # 법인명 위치: D열, 4행부터 2행 간격
    assert 'ws.Cells(r, "D").Value' in rendered
    assert "r = 4" in rendered
    assert "r = r + 2" in rendered
    # 확인 열 범위 케이스
    assert 'colStart = "DS"' in rendered
    assert 'colEnd = "EA"' in rendered
    assert 'colStart = "FE"' in rendered
    assert 'colEnd = "FM"' in rendered
    # 결과는 즉시 창(Debug.Print)에 텍스트로 출력한다. 즉시 창의
    # 200줄 한계(실측)를 넘기지 않도록 회사 하나당 한 줄이 아니라 팀당
    # 한 줄로 모아 찍는다.
    assert "Debug.Print" in rendered
    assert "Open " not in rendered
    assert 'Debug.Print teamNum & "팀 (" & matchedCount & "건) - " & matchedList' in rendered
    # 실내부망 실행 결과에서 발견된 소계 라벨("수익") 제외 로직
    assert 'IsExcludedLabel(corpName)' in rendered
    assert '"수익"' in rendered
    # 수식 오류(#N/A 등) 셀에 Trim을 걸면 런타임 오류 13(형식 불일치)이
    # 나므로, 법인명 셀을 읽을 때 IsError로 먼저 걸러야 한다
    assert "If IsError(rawValue) Then" in rendered
    # 시트를 못 찾은 팀은 조용히 넘기지 말고 즉시 창에 알려준다
    assert '팀 시트를 찾을 수 없습니다' in rendered


def test_generate_writes_output_file(tmp_path):
    output_path = tmp_path / "ExtractCompaniesByTeam.bas"
    result_path = generate(output_path=output_path)
    assert result_path == output_path
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "Sub ExtractCompaniesByTeamToFile()" in content
    assert "{{" not in content  # 모든 플레이스홀더가 치환되었는지 확인
