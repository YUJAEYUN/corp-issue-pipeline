"""LLM 리뷰 결과를 최종 이슈 파일에 병합한다.

입력:
  output/issues_<date>.json            (pipeline.py 산출물, 1차 확정본)
  output/llm_review_batch_<date>.json  (pipeline.py 산출물, 판단 대기 목록)
  output/llm_review_result_<date>.json (Claude Code가 판단한 결과)

출력:
  output/issues_<date>.json를 덮어써서 최종본으로 만든다.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE2_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = MODULE2_ROOT / "output"
DEFAULT_CORP_CSV_PATH = REPO_ROOT / "data" / "corp_team_mapping" / "corp_team_mapping.csv"

sys.path.insert(0, str(MODULE2_ROOT))
from date_utils import month_folder_name  # noqa: E402
from dedup import cluster_similar_issues  # noqa: E402


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _teams_for_corp(corp_csv_path: Path, corp_name: str) -> list[str]:
    teams = []
    with open(corp_csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["corp_name"] == corp_name:
                teams.append(row["team"])
    return sorted(teams, key=int)


def merge(run_date: str, output_dir: Path = DEFAULT_OUTPUT_DIR, corp_csv_path: Path = DEFAULT_CORP_CSV_PATH) -> Path:
    # pipeline.py가 "2026년 9월"처럼 월 단위 폴더에 쓰므로 같은 폴더를 찾는다.
    output_dir = output_dir / month_folder_name(run_date)
    issues_path = output_dir / f"issues_{run_date}.json"
    batch_path = output_dir / f"llm_review_batch_{run_date}.json"
    result_path = output_dir / f"llm_review_result_{run_date}.json"

    issues_data = _load_json(issues_path)
    batch_data = _load_json(batch_path)
    result_data = _load_json(result_path)

    issues_by_corp: dict[str, list[dict]] = issues_data["issues_by_corp"]

    # 한 기사에 여러 법인이 언급되면 article_id가 배치 안에서 중복될 수 있으므로,
    # article_id만으로 딕셔너리를 만들지 않고 배치·결과 리스트를 같은 순서로 짝지어 매칭한다.
    ambiguous_batch = batch_data.get("ambiguous_existing_corp_issues", [])
    ambiguous_verdicts = result_data.get("ambiguous_existing_corp_issues", [])
    if len(ambiguous_batch) != len(ambiguous_verdicts):
        raise ValueError(
            "ambiguous_existing_corp_issues 배치와 결과의 항목 수가 다릅니다: "
            f"batch={len(ambiguous_batch)} result={len(ambiguous_verdicts)}"
        )

    new_candidates_batch = batch_data.get("new_corp_candidates", [])
    new_candidates_verdicts = result_data.get("new_corp_candidates", [])
    if len(new_candidates_batch) != len(new_candidates_verdicts):
        raise ValueError(
            "new_corp_candidates 배치와 결과의 항목 수가 다릅니다: "
            f"batch={len(new_candidates_batch)} result={len(new_candidates_verdicts)}"
        )

    # 1) 애매했던 기존 법인 기사 판단 반영
    for original, verdict in zip(ambiguous_batch, ambiguous_verdicts):
        if original["article_id"] != verdict["article_id"] or verdict.get("verdict") != "pass":
            continue
        original = dict(original)
        original["keyword_tag"] = verdict.get("sales_signal_category", original.get("keyword_tag"))
        if verdict.get("keywords"):
            # 필터에 걸린 단어가 사건과 다를 때(BNY MOU 기사에 "은행장") 검토자가
            # 사건을 나타내는 키워드로 바꿔 준다. 엑셀 키워드 열에 그대로 나간다.
            original["matched_keywords"] = verdict["keywords"]
        original.pop("needs_llm_verdict", None)
        original.pop("also_matched_corps", None)
        # 같은 기사가 여러 법인에 걸려 한 건으로 묶여 온 경우, 검토자가 corps에
        # 실제 당사자 법인을 적는다. 없으면 대표 법인 하나만 채택한다.
        corps = verdict.get("corps") or [original["matched_corp"]]
        for corp in dict.fromkeys(corps):
            record = dict(original)
            if corp != original["matched_corp"]:
                record["matched_corp"] = corp
                record["team"] = _teams_for_corp(corp_csv_path, corp)
            issues_by_corp.setdefault(corp, []).append(record)

    # 2) 신규 법인 후보 판단 반영
    for original, verdict in zip(new_candidates_batch, new_candidates_verdicts):
        if original["article_id"] != verdict["article_id"] or verdict.get("verdict") != "new_lead":
            continue

        if verdict.get("is_actually_known") and verdict.get("matched_corp"):
            corp_name = verdict["matched_corp"]
            corp_status = "기존"
            team = _teams_for_corp(corp_csv_path, corp_name)
        else:
            corp_name = verdict.get("identified_corp_name") or "미상"
            corp_status = "신규"
            team = []

        issue = {
            "article_id": original["article_id"],
            "title": original["title"],
            "summary": original["description"],
            "originallink": original["originallink"],
            "link": original["link"],
            "pub_date": original["pub_date"],
            "matched_keywords": verdict.get("keywords") or original.get("matched_keywords", []),
            "matched_corp": corp_name,
            "corp_status": corp_status,
            "team": team,
            "keyword_tag": verdict.get("sales_signal_category", original.get("candidate_sales_signal")),
            "needs_llm_polish": False,
            "needs_llm_verdict": False,
        }
        issues_by_corp.setdefault(corp_name, []).append(issue)

    # 3) 요약 다듬기 반영. summary_polish_needed는 판정에서 제외한 기사를 뺄 수 있는
    # 선택적 목록이라 배치·결과가 1:1로 대응하지 않으므로, matched_corp로 항목을 구분한다.
    for polish in result_data.get("summary_polish_needed", []):
        corp_name = polish.get("matched_corp")
        target_records = issues_by_corp.get(corp_name, []) if corp_name else [
            record for records in issues_by_corp.values() for record in records
        ]
        for record in target_records:
            if record["article_id"] == polish["article_id"]:
                record["summary"] = polish["polished_summary"]
                record["needs_llm_polish"] = False

    for corp_name, corp_issues in issues_by_corp.items():
        issues_by_corp[corp_name] = cluster_similar_issues(corp_issues)

    issues_data["issues_by_corp"] = issues_by_corp
    issues_path.write_text(json.dumps(issues_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return issues_path


def recluster(run_date: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    """병합 후 요약을 손으로 다시 쓴 이슈 파일의 중복만 다시 묶는다.
    병합 때는 헤드라인이 달라 안 묶였던 기사도 요약을 통일하면 묶인다.
    """
    issues_path = output_dir / month_folder_name(run_date) / f"issues_{run_date}.json"
    issues_data = _load_json(issues_path)
    issues_data["issues_by_corp"] = {
        corp_name: cluster_similar_issues(corp_issues)
        for corp_name, corp_issues in issues_data["issues_by_corp"].items()
    }
    issues_path.write_text(json.dumps(issues_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return issues_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--corp-csv", type=Path, default=DEFAULT_CORP_CSV_PATH)
    parser.add_argument(
        "--recluster",
        action="store_true",
        help="검토 결과를 다시 병합하지 않고, 요약 재작성 후 중복 기사만 다시 묶는다",
    )
    args = parser.parse_args()

    if args.recluster:
        path = recluster(args.date, args.output_dir)
        print(f"중복 재정리 완료: {path}")
        return

    path = merge(args.date, args.output_dir, args.corp_csv)
    print(f"병합 완료: {path}")


if __name__ == "__main__":
    main()
