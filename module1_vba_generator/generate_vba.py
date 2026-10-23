"""모듈 1 — VBA 코드 생성 모듈.

법인 뉴스 이슈 트래커 스펙(docs/spec.md) 5.1절 기준으로, 팀별 법인 리스트
엑셀에서 실행할 VBA 코드를 생성한다. 이 스크립트 자체는 엑셀 데이터를
읽지 않는다 — 산출물은 VBA 코드(.bas) 파일이며, 실제 추출은 담당자가
업무망에서 해당 코드를 실행함으로써 이루어진다 (망분리 제약, 1.4절/4절).

사용법:
    python generate_vba.py [--config config.json] [--output output/ExtractCompaniesByTeam.bas]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_TEMPLATE_PATH = Path(__file__).parent / "templates" / "extract_companies.bas.tmpl"
DEFAULT_OUTPUT_PATH = Path(__file__).parent / "output" / "ExtractCompaniesByTeam.bas"


def load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_check_column_cases(config: dict) -> str:
    lines = []
    for team, (col_start, col_end) in sorted(
        config["check_column_ranges"].items(), key=lambda kv: int(kv[0])
    ):
        lines.append(f'        Case {team}')
        lines.append(f'            colStart = "{col_start}"')
        lines.append(f'            colEnd = "{col_end}"')
    return "\n".join(lines)


def build_excluded_labels_array(config: dict) -> str:
    labels = config.get("excluded_labels", [])
    return ", ".join(f'"{label}"' for label in labels)


def render(template: str, config: dict) -> str:
    team_numbers = config["team_numbers"]
    excluded_labels = config.get("excluded_labels", [])
    replacements = {
        "{{OUTPUT_FILE_NAME}}": config["output_file_name"],
        "{{TEAM_MIN}}": str(min(team_numbers)),
        "{{TEAM_MAX}}": str(max(team_numbers)),
        "{{CORP_NAME_COLUMN}}": config["corp_name_column"],
        "{{FIRST_CORP_ROW}}": str(config["first_corp_row"]),
        "{{ROW_STEP}}": str(config["row_step"]),
        "{{CHECK_COLUMN_CASES}}": build_check_column_cases(config),
        "{{EXCLUDED_LABELS_ARRAY}}": build_excluded_labels_array(config),
        "{{EXCLUDED_LABELS_COMMENT}}": ", ".join(excluded_labels) or "(없음)",
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def generate(
    config_path: Path = DEFAULT_CONFIG_PATH,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> Path:
    config = load_config(config_path)
    template = template_path.read_text(encoding="utf-8")
    rendered = render(template, config)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 클래식 VBA 편집기는 CRLF를 기대한다. LF만 있으면 Import는 되지만
    # 이후 Export 등에서 줄바꿈이 깨지는 사례가 있어 처음부터 CRLF로 쓴다.
    output_path.write_text(rendered, encoding="utf-8", newline="\r\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    output_path = generate(args.config, args.template, args.output)
    print(f"VBA 코드 생성 완료: {output_path}")


if __name__ == "__main__":
    main()
