import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm_review"))

import merge_llm_results as merge_mod


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_merge_applies_pass_verdict_and_new_lead_and_polish(tmp_path):
    output_dir = tmp_path / "output"
    # pipeline.py가 실제로 쓰는 위치("2026년 9월" 같은 월별 폴더)에 맞춰
    # 입력 fixture를 둔다 — merge()가 output_dir 밑에서 이 폴더를 찾는다.
    month_dir = output_dir / "2026년 9월"
    month_dir.mkdir(parents=True)

    corp_csv = tmp_path / "corp_team_mapping.csv"
    with open(corp_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["corp_name", "team"])
        writer.writeheader()
        writer.writerow({"corp_name": "가나다전자(주)", "team": "1"})
        writer.writerow({"corp_name": "라마바전자(주)", "team": "4"})

    issues_path = month_dir / "issues_2026-09-16.json"
    _write_json(
        issues_path,
        {
            "run_date": "2026-09-16",
            "issues_by_corp": {
                "가나다전자(주)": [
                    {
                        "article_id": "keep-1",
                        "title": "기존 이슈",
                        "summary": "부실한 요약",
                        "originallink": "http://x",
                        "link": "http://x",
                        "pub_date": "2026-09-16",
                        "matched_keywords": ["유상증자"],
                        "matched_corp": "가나다전자(주)",
                        "corp_status": "기존",
                        "team": ["1"],
                        "keyword_tag": "조달 니즈 발생",
                        "needs_llm_polish": True,
                        "needs_llm_verdict": False,
                    }
                ]
            },
        },
    )

    batch_path = month_dir / "llm_review_batch_2026-09-16.json"
    _write_json(
        batch_path,
        {
            "run_date": "2026-09-16",
            "ambiguous_existing_corp_issues": [
                {
                    "article_id": "amb-1",
                    "title": "애매 이슈",
                    "summary": "애매",
                    "originallink": "http://y",
                    "link": "http://y",
                    "pub_date": "2026-09-16",
                    "matched_keywords": ["리밸런싱"],
                    "matched_corp": "가나다전자(주)",
                    "corp_status": "기존",
                    "team": ["1"],
                    "keyword_tag": "운용·헤지 전략",
                    "needs_llm_polish": False,
                    "needs_llm_verdict": True,
                    "also_matched_corps": ["라마바전자(주)"],
                }
            ],
            "new_corp_candidates": [
                {
                    "article_id": "new-1",
                    "title": "마바사그룹, 신규 이슈",
                    "description": "설명",
                    "originallink": "http://z",
                    "link": "http://z",
                    "pub_date": "2026-09-16",
                    "matched_keywords": ["유상증자"],
                    "candidate_sales_signal": "조달 니즈 발생",
                }
            ],
            "summary_polish_needed": [
                {
                    "article_id": "keep-1",
                    "title": "기존 이슈",
                    "summary": "부실한 요약",
                    "originallink": "http://x",
                    "link": "http://x",
                    "pub_date": "2026-09-16",
                    "matched_keywords": ["유상증자"],
                    "matched_corp": "가나다전자(주)",
                    "corp_status": "기존",
                    "team": ["1"],
                    "keyword_tag": "조달 니즈 발생",
                }
            ],
        },
    )

    result_path = month_dir / "llm_review_result_2026-09-16.json"
    _write_json(
        result_path,
        {
            "run_date": "2026-09-16",
            "ambiguous_existing_corp_issues": [
                {
                    "article_id": "amb-1",
                    "verdict": "pass",
                    "sales_signal_category": "운용·헤지 전략",
                    # 필터 단어("리밸런싱")가 사건과 달라 검토자가 바꾼 경우
                    "keywords": ["MOU", "디지털자산"],
                    # 같은 기사로 묶여 온 두 법인 모두 당사자
                    "corps": ["가나다전자(주)", "라마바전자(주)"],
                }
            ],
            "new_corp_candidates": [
                {
                    "article_id": "new-1",
                    "identified_corp_name": "마바사그룹",
                    "is_actually_known": False,
                    "matched_corp": None,
                    "verdict": "new_lead",
                    "sales_signal_category": "조달 니즈 발생",
                }
            ],
            "summary_polish_needed": [
                {"article_id": "keep-1", "matched_corp": "가나다전자(주)", "polished_summary": "다듬어진 요약"}
            ],
        },
    )

    result = merge_mod.merge("2026-09-16", output_dir=output_dir, corp_csv_path=corp_csv)
    merged = json.loads(result.read_text(encoding="utf-8"))
    by_corp = merged["issues_by_corp"]

    existing_articles = {a["article_id"] for a in by_corp["가나다전자(주)"]}
    assert existing_articles == {"keep-1", "amb-1"}

    polished = next(a for a in by_corp["가나다전자(주)"] if a["article_id"] == "keep-1")
    assert polished["summary"] == "다듬어진 요약"
    assert polished["needs_llm_polish"] is False

    assert "마바사그룹" in by_corp
    new_issue = by_corp["마바사그룹"][0]
    assert new_issue["corp_status"] == "신규"
    assert new_issue["team"] == []
    assert new_issue["keyword_tag"] == "조달 니즈 발생"
    # keywords가 없으면 필터에 걸린 단어를 그대로 둔다
    assert new_issue["matched_keywords"] == ["유상증자"]

    amb = next(a for a in by_corp["가나다전자(주)"] if a["article_id"] == "amb-1")
    assert amb["matched_keywords"] == ["MOU", "디지털자산"]
    assert "also_matched_corps" not in amb
    other = by_corp["라마바전자(주)"][0]
    assert other["article_id"] == "amb-1"
    assert other["team"] == ["4"]


def test_merge_handles_one_article_mentioning_multiple_corps(tmp_path):
    # 여러 법인을 한꺼번에 다루는 공시 요약 기사처럼, 같은 article_id가
    # 배치 안에 법인별로 여러 번 등장할 때도 각 판단이 올바른 법인에만 붙어야 한다.
    output_dir = tmp_path / "output"
    month_dir = output_dir / "2026년 9월"
    month_dir.mkdir(parents=True)

    corp_csv = tmp_path / "corp_team_mapping.csv"
    with open(corp_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["corp_name", "team"])
        writer.writeheader()

    issues_path = month_dir / "issues_2026-09-17.json"
    _write_json(issues_path, {"run_date": "2026-09-17", "issues_by_corp": {}})

    def _ambiguous_entry(matched_corp, keyword_tag):
        return {
            "article_id": "shared-1",
            "title": "[주요공시] 여러 법인 공시 모음",
            "summary": f"{matched_corp} 관련 공시 내용",
            "originallink": "http://shared",
            "link": "http://shared",
            "pub_date": "2026-09-17",
            "matched_keywords": ["공시"],
            "matched_corp": matched_corp,
            "corp_status": "기존",
            "team": ["1"],
            "keyword_tag": keyword_tag,
            "needs_llm_polish": False,
            "needs_llm_verdict": True,
        }

    batch_path = month_dir / "llm_review_batch_2026-09-17.json"
    _write_json(
        batch_path,
        {
            "run_date": "2026-09-17",
            "ambiguous_existing_corp_issues": [
                _ambiguous_entry("가나다전자(주)", "조달 니즈 발생"),
                _ambiguous_entry("마바사그룹", "여유자금 활용 제안(펀드/랩/신탁)"),
            ],
            "new_corp_candidates": [],
            "summary_polish_needed": [],
        },
    )

    result_path = month_dir / "llm_review_result_2026-09-17.json"
    _write_json(
        result_path,
        {
            "run_date": "2026-09-17",
            # 첫 번째(가나다전자)는 통과, 두 번째(마바사그룹)는 반려한다.
            "ambiguous_existing_corp_issues": [
                {"article_id": "shared-1", "verdict": "pass", "sales_signal_category": "조달 니즈 발생"},
                {"article_id": "shared-1", "verdict": "reject", "sales_signal_category": None},
            ],
            "new_corp_candidates": [],
            "summary_polish_needed": [],
        },
    )

    result = merge_mod.merge("2026-09-17", output_dir=output_dir, corp_csv_path=corp_csv)
    merged = json.loads(result.read_text(encoding="utf-8"))
    by_corp = merged["issues_by_corp"]

    assert "가나다전자(주)" in by_corp
    assert by_corp["가나다전자(주)"][0]["summary"] == "가나다전자(주) 관련 공시 내용"
    assert "마바사그룹" not in by_corp


def test_polish_only_updates_matched_corp_when_article_id_shared(tmp_path):
    # 같은 article_id를 가진 이슈가 두 법인에 각각 남아 있을 때
    # summary_polish_needed는 matched_corp가 일치하는 쪽만 고쳐야 한다.
    output_dir = tmp_path / "output"
    month_dir = output_dir / "2026년 9월"
    month_dir.mkdir(parents=True)

    corp_csv = tmp_path / "corp_team_mapping.csv"
    with open(corp_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["corp_name", "team"])
        writer.writeheader()

    def _record(matched_corp, summary):
        return {
            "article_id": "shared-2",
            "title": "[주요공시] 여러 법인 공시 모음",
            "summary": summary,
            "originallink": "http://shared2",
            "link": "http://shared2",
            "pub_date": "2026-09-18",
            "matched_keywords": ["공시"],
            "matched_corp": matched_corp,
            "corp_status": "기존",
            "team": ["1"],
            "keyword_tag": "조달 니즈 발생",
            "needs_llm_polish": True,
            "needs_llm_verdict": False,
        }

    issues_path = month_dir / "issues_2026-09-18.json"
    _write_json(
        issues_path,
        {
            "run_date": "2026-09-18",
            "issues_by_corp": {
                "가나다전자(주)": [_record("가나다전자(주)", "원래 요약 A")],
                "마바사그룹": [_record("마바사그룹", "원래 요약 B")],
            },
        },
    )

    batch_path = month_dir / "llm_review_batch_2026-09-18.json"
    _write_json(
        batch_path,
        {"run_date": "2026-09-18", "ambiguous_existing_corp_issues": [], "new_corp_candidates": [], "summary_polish_needed": []},
    )

    result_path = month_dir / "llm_review_result_2026-09-18.json"
    _write_json(
        result_path,
        {
            "run_date": "2026-09-18",
            "ambiguous_existing_corp_issues": [],
            "new_corp_candidates": [],
            "summary_polish_needed": [
                {"article_id": "shared-2", "matched_corp": "가나다전자(주)", "polished_summary": "다듬어진 A"}
            ],
        },
    )

    result = merge_mod.merge("2026-09-18", output_dir=output_dir, corp_csv_path=corp_csv)
    merged = json.loads(result.read_text(encoding="utf-8"))
    by_corp = merged["issues_by_corp"]

    assert by_corp["가나다전자(주)"][0]["summary"] == "다듬어진 A"
    assert by_corp["마바사그룹"][0]["summary"] == "원래 요약 B"


def test_recluster_groups_issues_after_summaries_are_rewritten(tmp_path):
    month_dir = tmp_path / "output" / "2026년 9월"
    month_dir.mkdir(parents=True)
    summary = "가나다전자는 라마바은행과 디지털 결제 업무협약을 체결했다."
    issues = [
        {"article_id": str(i), "title": title, "summary": summary, "link": f"http://x/{i}",
         "originallink": f"http://x/{i}", "pub_date": "2026-09-29"}
        for i, title in enumerate(["가나다전자, 라마바은행과 MOU", "[금융권 소식] 가나다전자 외", "가나다전자 '결제 동맹'"])
    ]
    issues_path = month_dir / "issues_2026-09-30.json"
    _write_json(issues_path, {"run_date": "2026-09-30", "issues_by_corp": {"가나다전자(주)": issues}})

    merge_mod.recluster("2026-09-30", tmp_path / "output")

    result = json.loads(issues_path.read_text(encoding="utf-8"))["issues_by_corp"]["가나다전자(주)"]
    assert len(result) == 1
    assert len(result[0]["related_articles"]) == 2
