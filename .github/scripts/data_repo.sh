#!/usr/bin/env bash
# 비공개 데이터 저장소와 작업 폴더 사이에서 데이터를 옮긴다.
#
# 이 저장소(공개)에는 코드만 있다. 법인·팀 매핑 CSV와 수집·검토 결과 JSON,
# 엑셀은 비공개 데이터 저장소에 있다. 워크플로는 데이터 저장소를 .data/에
# 체크아웃한 뒤 이 스크립트로 데이터를 코드 옆 원래 경로에 복사해 쓰고,
# 끝나면 결과를 .data/로 되돌려 커밋한다. 그래서 코드와 Claude 프롬프트는
# 데이터가 같은 저장소에 있던 때와 같은 경로를 그대로 쓴다.
#
# 사용:
#   data_repo.sh branch   실행 브랜치에 맞는 데이터 브랜치로 바꾼다.
#                         main이면 main, 아니면 trial/<브랜치>(없으면 main에서 만든다).
#   data_repo.sh pull     .data/의 데이터를 작업 폴더로 복사한다.
#   data_repo.sh push <커밋 메시지 파일> <git pathspec>...
#                         작업 폴더의 데이터를 .data/로 복사하고, pathspec에
#                         맞는 파일만 커밋해 데이터 저장소에 푸시한다.
#
# 환경 변수:
#   DATA_DIR      데이터 저장소 체크아웃 위치 (기본 .data)
#   CODE_BRANCH   실행 브랜치 (기본 GITHUB_REF_NAME)
set -euo pipefail

DATA_DIR="${DATA_DIR:-.data}"
CODE_BRANCH="${CODE_BRANCH:-${GITHUB_REF_NAME:-main}}"
# 데이터 저장소에서 가져오고 되돌리는 경로. 공개 저장소 .gitignore와 같다.
DATA_PATHS=(
  data/corp_team_mapping/corp_team_mapping.csv
  module2_news_pipeline/output
  outputs
)

data_branch() {
  if [ "$CODE_BRANCH" = "main" ]; then echo main; else echo "trial/$CODE_BRANCH"; fi
}

cmd_branch() {
  local b
  b=$(data_branch)
  [ "$b" = "main" ] && return 0
  if git -C "$DATA_DIR" fetch -q origin "$b" 2>/dev/null; then
    git -C "$DATA_DIR" checkout -q -B "$b" FETCH_HEAD
  else
    git -C "$DATA_DIR" checkout -q -b "$b"
  fi
  echo "데이터 브랜치: $b"
}

cmd_pull() {
  local p
  for p in "${DATA_PATHS[@]}"; do
    [ -e "$DATA_DIR/$p" ] || continue
    mkdir -p "$(dirname "$p")"
    if [ -d "$DATA_DIR/$p" ]; then
      mkdir -p "$p"
      cp -a "$DATA_DIR/$p/." "$p/"
    else
      cp "$DATA_DIR/$p" "$p"
    fi
  done
  test -s data/corp_team_mapping/corp_team_mapping.csv \
    || { echo "::error::데이터 저장소에 법인 매핑 CSV가 없습니다." >&2; exit 1; }
  echo "데이터를 작업 폴더로 복사했습니다."
}

cmd_push() {
  local msg_file
  msg_file=$(realpath "$1")
  shift
  local p b
  for p in "${DATA_PATHS[@]:1}"; do
    [ -d "$p" ] || continue
    mkdir -p "$DATA_DIR/$p"
    cp -a "$p/." "$DATA_DIR/$p/"
  done
  b=$(data_branch)
  git -C "$DATA_DIR" config user.name "${COMMIT_NAME:-github-actions[bot]}"
  git -C "$DATA_DIR" config user.email "${COMMIT_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
  # 아직 파일이 없는 pathspec은 git add가 실패하므로, 바뀐 게 있는 것만 넣는다.
  # 데이터 저장소의 .gitignore가 이 경로를 무시하더라도 넣도록 -f를 쓴다.
  for p in "$@"; do
    if [ -n "$(git -C "$DATA_DIR" status --porcelain --ignored -- "$p")" ]; then
      git -C "$DATA_DIR" add -A -f -- "$p"
    fi
  done
  if git -C "$DATA_DIR" diff --cached --quiet; then
    echo "바뀐 파일이 없어 커밋할 게 없습니다."
    return 0
  fi
  echo "커밋할 파일 $(git -C "$DATA_DIR" diff --cached --name-only | wc -l)개"
  git -C "$DATA_DIR" commit -q -F "$msg_file"
  # 다른 워크플로도 데이터 저장소에 커밋하므로 push 전에 따라잡고,
  # 경합하면 재시도한다.
  local i
  for i in 1 2 3 4 5; do
    if git -C "$DATA_DIR" ls-remote --exit-code -q origin "refs/heads/$b" >/dev/null 2>&1; then
      git -C "$DATA_DIR" pull -q --rebase origin "$b" || { git -C "$DATA_DIR" rebase --abort 2>/dev/null || true; }
    fi
    if git -C "$DATA_DIR" push -q origin "HEAD:refs/heads/$b"; then
      echo "데이터 저장소 $b 브랜치에 푸시했습니다."
      return 0
    fi
    echo "push 실패, 재시도 ($i/5)"
    sleep $((RANDOM % 10 + 1))
  done
  echo "5회 재시도 후에도 push 실패" >&2
  exit 1
}

case "${1:-}" in
  branch) cmd_branch ;;
  pull) cmd_pull ;;
  push) shift; cmd_push "$@" ;;
  *) echo "사용: $0 branch|pull|push <메시지 파일> <pathspec>..." >&2; exit 2 ;;
esac
