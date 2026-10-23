"""LLM 검토 배치를 조각으로 나누고, 조각별 판정을 다시 하나로 합친다.

2026-10-01 재수집 배치(애매 1,684 + 신규 432)는 1차 검토가 한 번에 처리하지
못하고 판정 결과 없이 끝났다. 검토 워크플로우는 배치를 조각(기본 100건)으로
나눠 조각마다 Claude를 따로 돌리고, 조각 판정을 합쳐 평소의
llm_review_result_<날짜>.json을 만든 뒤 기존 병합 절차를 그대로 탄다.

  split: llm_review_batch_<날짜>.json -> <chunk_dir>/<날짜>/chunk_NN.json
  join:  <chunk_dir>/<날짜>/chunk_NN.result.json -> llm_review_result_<날짜>.json

조각 판정 파일은 조각 파일과 같은 구조(ambiguous_existing_corp_issues,
new_corp_candidates)에 판정을 같은 순서로 담는다. join은 조각마다 항목 수와
article_id가 배치와 맞는지 확인하고, 하나라도 어긋나면 실패한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from date_utils import month_folder_name  # noqa: E402

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
DEFAULT_CHUNK_DIR = Path(__file__).resolve().parents[2] / ".llm_chunks"
DEFAULT_CHUNK_SIZE = 100
LIST_KEYS = ("ambiguous_existing_corp_issues", "new_corp_candidates")
# 판정에 필요한 필드만 조각에 싣는다. 링크·발행일까지 다 실으면 100건이
# 150KB를 넘어 검토자가 한 번에 읽지 못한다.
JUDGE_FIELDS = (
    "article_id",
    "matched_corp",
    "also_matched_corps",
    "needs_llm_verdict",
    "title",
    "summary",
    "description",
    "matched_keywords",
    "keyword_tag",
    "candidate_sales_signal",
)


def _for_judge(item: dict) -> dict:
    return {key: item[key] for key in JUDGE_FIELDS if item.get(key) not in (None, [], "")}


class ChunkError(RuntimeError):
    pass


def _batch_path(output_dir: Path, run_date: str) -> Path:
    return output_dir / month_folder_name(run_date) / f"llm_review_batch_{run_date}.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")


def split(
    run_date: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    chunk_dir: Path = DEFAULT_CHUNK_DIR,
    size: int = DEFAULT_CHUNK_SIZE,
) -> list[Path]:
    """배치의 두 목록을 이어 붙여 size건씩 자른다. 배치가 비어도 조각 하나는 만든다."""
    batch = _read(_batch_path(output_dir, run_date))
    entries = [(key, item) for key in LIST_KEYS for item in batch.get(key, [])]
    paths: list[Path] = []
    for number, start in enumerate(range(0, max(len(entries), 1), size), 1):
        piece = entries[start : start + size]
        chunk = {"run_date": run_date, "chunk": number}
        for key in LIST_KEYS:
            chunk[key] = [_for_judge(item) for item_key, item in piece if item_key == key]
        path = chunk_dir / run_date / f"chunk_{number:02d}.json"
        _write(path, chunk, indent=1)
        paths.append(path)
    return paths


def chunk_list(run_dates: list[str], chunk_dir: Path = DEFAULT_CHUNK_DIR) -> list[dict]:
    """워크플로우 matrix에 넣을 [{"date", "chunk"}] 목록."""
    return [
        {"date": run_date, "chunk": path.stem.removeprefix("chunk_")}
        for run_date in run_dates
        for path in sorted((chunk_dir / run_date).glob("chunk_[0-9][0-9].json"))
    ]


def _chunk_problems(chunk_path: Path) -> list[str]:
    """조각 하나의 판정 파일이 있고, 항목 수와 article_id 순서가 맞는지."""
    result_path = chunk_path.with_name(chunk_path.stem + ".result.json")
    if not result_path.exists():
        return [f"{chunk_path.name}: 판정 파일 없음"]
    chunk, result = _read(chunk_path), _read(result_path)
    problems = []
    for key in LIST_KEYS:
        expected, got = chunk.get(key, []), result.get(key, [])
        if len(expected) != len(got):
            problems.append(f"{chunk_path.name} {key}: 판정 {len(got)}건 / 조각 {len(expected)}건")
            continue
        for item, verdict in zip(expected, got):
            if item["article_id"] != verdict.get("article_id"):
                problems.append(f"{chunk_path.name} {key}: article_id 순서 불일치 {item['article_id']}")
                break
    return problems


def verify_chunk(run_date: str, chunk: str, chunk_dir: Path = DEFAULT_CHUNK_DIR) -> None:
    problems = _chunk_problems(chunk_dir / run_date / f"chunk_{chunk}.json")
    if problems:
        raise ChunkError("; ".join(problems))


def check_results(run_dates: list[str], output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
    """날짜마다 판정 결과 파일이 있고 배치 항목 수와 맞는지. Claude 단계는 결과를
    못 만들어도 "성공"으로 끝나서, 2026-10-01에 빈 엑셀이 main에 커밋됐다."""
    problems = []
    for run_date in run_dates:
        result_path = output_dir / month_folder_name(run_date) / f"llm_review_result_{run_date}.json"
        if not result_path.exists():
            problems.append(f"{run_date}: 판정 결과 파일 없음")
            continue
        batch, result = _read(_batch_path(output_dir, run_date)), _read(result_path)
        for key in LIST_KEYS:
            if len(batch.get(key, [])) != len(result.get(key, [])):
                problems.append(
                    f"{run_date} {key}: 판정 {len(result.get(key, []))}건 / 배치 {len(batch.get(key, []))}건"
                )
    if problems:
        raise ChunkError("; ".join(problems))


def join(
    run_date: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    chunk_dir: Path = DEFAULT_CHUNK_DIR,
) -> Path:
    batch = _read(_batch_path(output_dir, run_date))
    joined: dict[str, list] = {key: [] for key in LIST_KEYS}
    chunk_paths = sorted((chunk_dir / run_date).glob("chunk_[0-9][0-9].json"))
    if not chunk_paths:
        raise ChunkError(f"{run_date}: 조각 파일이 없다")

    problems: list[str] = []
    for chunk_path in chunk_paths:
        chunk_problems = _chunk_problems(chunk_path)
        problems.extend(chunk_problems)
        if not chunk_problems:
            result = _read(chunk_path.with_name(chunk_path.stem + ".result.json"))
            for key in LIST_KEYS:
                joined[key].extend(result.get(key, []))
    if problems:
        raise ChunkError("; ".join(problems))

    for key in LIST_KEYS:
        if len(joined[key]) != len(batch.get(key, [])):
            raise ChunkError(f"{run_date} {key}: 합친 판정 {len(joined[key])}건 / 배치 {len(batch.get(key, []))}건")

    result_path = output_dir / month_folder_name(run_date) / f"llm_review_result_{run_date}.json"
    _write(result_path, {"run_date": run_date, **joined, "summary_polish_needed": []})
    return result_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("split", "join", "list", "check", "verify-chunk"):
        command = sub.add_parser(name)
        command.add_argument("--dates", nargs="+", required=True)
        command.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        command.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNK_DIR)
        if name == "split":
            command.add_argument("--size", type=int, default=DEFAULT_CHUNK_SIZE)
        if name == "verify-chunk":
            command.add_argument("--chunk", required=True)
    args = parser.parse_args()

    try:
        if args.command == "split":
            for run_date in args.dates:
                paths = split(run_date, args.output_dir, args.chunk_dir, args.size)
                print(f"{run_date}: 조각 {len(paths)}개", file=sys.stderr)
        elif args.command == "list":
            print(json.dumps(chunk_list(args.dates, args.chunk_dir), ensure_ascii=False))
        elif args.command == "check":
            check_results(args.dates, args.output_dir)
            print("판정 결과 확인 완료")
        elif args.command == "verify-chunk":
            for run_date in args.dates:
                verify_chunk(run_date, args.chunk, args.chunk_dir)
            print("조각 판정 확인 완료")
        else:
            for run_date in args.dates:
                print(f"판정 합침: {join(run_date, args.output_dir, args.chunk_dir)}")
    except ChunkError as exc:
        print(f"::error title=LLM 검토 미완료::{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
