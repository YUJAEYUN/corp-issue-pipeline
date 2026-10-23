import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm_review"))

import chunk_batch


def _setup(tmp_path, n_amb, n_new):
    output_dir = tmp_path / "output"
    month = output_dir / "2026년 10월"
    month.mkdir(parents=True)
    batch = {
        "run_date": "2026-10-01",
        "ambiguous_existing_corp_issues": [{"article_id": f"a{i}"} for i in range(n_amb)],
        "new_corp_candidates": [{"article_id": f"n{i}"} for i in range(n_new)],
        "summary_polish_needed": [],
    }
    (month / "llm_review_batch_2026-10-01.json").write_text(json.dumps(batch), encoding="utf-8")
    return output_dir, tmp_path / "chunks"


def _judge_all(chunk_paths, verdict="reject"):
    for path in chunk_paths:
        chunk = json.loads(path.read_text(encoding="utf-8"))
        result = {
            key: [{"article_id": item["article_id"], "verdict": verdict} for item in chunk[key]]
            for key in chunk_batch.LIST_KEYS
        }
        path.with_name(path.stem + ".result.json").write_text(json.dumps(result), encoding="utf-8")


def test_split_and_join_round_trip_keeps_batch_order(tmp_path):
    output_dir, chunk_dir = _setup(tmp_path, n_amb=5, n_new=4)
    paths = chunk_batch.split("2026-10-01", output_dir, chunk_dir, size=4)

    # 9건을 4건씩: 애매 4 / 애매 1 + 신규 3 / 신규 1
    assert [p.name for p in paths] == ["chunk_01.json", "chunk_02.json", "chunk_03.json"]
    second = json.loads(paths[1].read_text(encoding="utf-8"))
    assert [i["article_id"] for i in second["ambiguous_existing_corp_issues"]] == ["a4"]
    assert [i["article_id"] for i in second["new_corp_candidates"]] == ["n0", "n1", "n2"]
    assert chunk_batch.chunk_list(["2026-10-01"], chunk_dir) == [
        {"date": "2026-10-01", "chunk": "01"},
        {"date": "2026-10-01", "chunk": "02"},
        {"date": "2026-10-01", "chunk": "03"},
    ]

    _judge_all(paths)
    result_path = chunk_batch.join("2026-10-01", output_dir, chunk_dir)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert [v["article_id"] for v in result["ambiguous_existing_corp_issues"]] == [f"a{i}" for i in range(5)]
    assert [v["article_id"] for v in result["new_corp_candidates"]] == [f"n{i}" for i in range(4)]


def test_join_fails_when_a_chunk_has_no_result(tmp_path):
    output_dir, chunk_dir = _setup(tmp_path, n_amb=5, n_new=0)
    paths = chunk_batch.split("2026-10-01", output_dir, chunk_dir, size=2)
    _judge_all(paths[:-1])

    with pytest.raises(chunk_batch.ChunkError, match="chunk_03.json: 판정 파일 없음"):
        chunk_batch.join("2026-10-01", output_dir, chunk_dir)
    assert not (output_dir / "2026년 10월" / "llm_review_result_2026-10-01.json").exists()


def test_join_fails_when_verdicts_are_missing_or_reordered(tmp_path):
    output_dir, chunk_dir = _setup(tmp_path, n_amb=3, n_new=0)
    [path] = chunk_batch.split("2026-10-01", output_dir, chunk_dir, size=10)
    result_path = path.with_name(path.stem + ".result.json")

    result_path.write_text(json.dumps({"ambiguous_existing_corp_issues": [{"article_id": "a0"}]}), encoding="utf-8")
    with pytest.raises(chunk_batch.ChunkError, match="판정 1건 / 조각 3건"):
        chunk_batch.join("2026-10-01", output_dir, chunk_dir)

    reordered = [{"article_id": a} for a in ("a1", "a0", "a2")]
    result_path.write_text(json.dumps({"ambiguous_existing_corp_issues": reordered}), encoding="utf-8")
    with pytest.raises(chunk_batch.ChunkError, match="순서 불일치"):
        chunk_batch.join("2026-10-01", output_dir, chunk_dir)


def test_empty_batch_still_makes_one_chunk(tmp_path):
    output_dir, chunk_dir = _setup(tmp_path, n_amb=0, n_new=0)
    paths = chunk_batch.split("2026-10-01", output_dir, chunk_dir)
    assert len(paths) == 1
    _judge_all(paths)
    chunk_batch.join("2026-10-01", output_dir, chunk_dir)


def test_check_results_requires_result_matching_batch(tmp_path):
    output_dir, chunk_dir = _setup(tmp_path, n_amb=2, n_new=1)
    with pytest.raises(chunk_batch.ChunkError, match="판정 결과 파일 없음"):
        chunk_batch.check_results(["2026-10-01"], output_dir)

    paths = chunk_batch.split("2026-10-01", output_dir, chunk_dir)
    _judge_all(paths)
    chunk_batch.verify_chunk("2026-10-01", "01", chunk_dir)
    chunk_batch.join("2026-10-01", output_dir, chunk_dir)
    chunk_batch.check_results(["2026-10-01"], output_dir)
