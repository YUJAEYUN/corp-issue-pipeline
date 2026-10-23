#!/usr/bin/env bash
# seal.sh로 암호화한 파일을 푼다.
#
# 사용: ARTIFACT_PASSPHRASE=... unseal.sh <암호화 파일> [풀 위치, 기본 .]
# 로컬에서 진단용 아티팩트를 볼 때도 같은 명령을 쓴다.
set -euo pipefail
: "${ARTIFACT_PASSPHRASE:?ARTIFACT_PASSPHRASE 시크릿이 필요하다}"
in="$1"
dest="${2:-.}"
mkdir -p "$dest"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:ARTIFACT_PASSPHRASE -in "$in" \
  | tar -xzf - -C "$dest"
echo "복호화: $(basename "$in") -> $dest"
