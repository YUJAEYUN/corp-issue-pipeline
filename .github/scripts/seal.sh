#!/usr/bin/env bash
# 파일과 폴더를 묶어 암호화한다. 공개 저장소의 아티팩트는 로그인한 누구나
# 받을 수 있으므로, 법인명이 든 파일은 이걸로 감싼 뒤에만 올린다.
#
# 사용: ARTIFACT_PASSPHRASE=... seal.sh <출력 파일> [-C <기준 폴더>] <경로>...
# 경로는 기준 폴더(기본 현재 폴더)에서 본 상대 경로로 묶인다. 없는 경로는
# 건너뛴다. 풀 때는 unseal.sh를 쓴다.
set -euo pipefail
: "${ARTIFACT_PASSPHRASE:?ARTIFACT_PASSPHRASE 시크릿이 필요하다}"
out="$1"
shift
dir=.
if [ "${1:-}" = "-C" ]; then
  dir="$2"
  shift 2
fi
paths=()
for p in "$@"; do
  [ -e "$dir/$p" ] && paths+=("$p")
done
if [ "${#paths[@]}" -eq 0 ]; then
  echo "암호화할 파일이 없다: $*" >&2
  exit 1
fi
tar -C "$dir" -czf - "${paths[@]}" \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:ARTIFACT_PASSPHRASE -out "$out"
echo "암호화: $(basename "$out") ($(du -h "$out" | cut -f1), 경로 ${#paths[@]}개)"
