#!/usr/bin/env bash
# Claude Code CLI를 무인으로 한 번 돌리고, 결과가 성공인지 확인한다.
#
# 세션 출력(JSON)과 오류는 LOG_DIR에 파일로만 남긴다. 공개 로그에는 턴 수와
# 결과 종류만 찍는다. 세션 기록에는 기사와 법인명이 들어 있으므로, 워크플로는
# 이 폴더를 seal.sh로 암호화한 뒤에만 아티팩트로 올린다.
#
# 사용: run_claude.sh <이름> <최대 턴> <제한 시간(분)>
#   PROMPT                   프롬프트
#   CLAUDE_MODEL             모델
#   LOG_DIR                  세션 기록을 남길 폴더
#   CLAUDE_CODE_OAUTH_TOKEN  Claude 구독 토큰
set -uo pipefail

name="$1"
max_turns="$2"
minutes="$3"
: "${PROMPT:?PROMPT가 필요하다}"
: "${CLAUDE_MODEL:?CLAUDE_MODEL이 필요하다}"
: "${LOG_DIR:?LOG_DIR가 필요하다}"
mkdir -p "$LOG_DIR"

ALLOWED_TOOLS="Read,Write,Edit,Glob,Grep,Bash(python3:*),Bash(python:*),Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(find:*),Bash(jq:*)"

start=$SECONDS
timeout "${minutes}m" claude -p "$PROMPT" \
  --model "$CLAUDE_MODEL" \
  --max-turns "$max_turns" \
  --allowedTools "$ALLOWED_TOOLS" \
  --output-format json \
  > "$LOG_DIR/${name}.json" 2> "$LOG_DIR/${name}.err"
code=$?
took=$((SECONDS - start))

subtype=$(jq -r '.subtype // "없음"' "$LOG_DIR/${name}.json" 2>/dev/null || echo "없음")
# `.is_error // true`는 쓰지 않는다. jq의 //는 false도 비었다고 보고 true로 바꿔서
# 성공한 실행까지 실패로 처리한다.
is_error=$(jq -r 'if .is_error == false then "false" else "true" end' "$LOG_DIR/${name}.json" 2>/dev/null || echo true)
turns=$(jq -r '.num_turns // "?"' "$LOG_DIR/${name}.json" 2>/dev/null || echo "?")
denials=$(jq -r '.permission_denials | length? // 0' "$LOG_DIR/${name}.json" 2>/dev/null || echo "?")

echo "${name}: ${took}초, 종료 코드 ${code}, 결과 ${subtype}, 턴 ${turns}, 거부된 명령 ${denials}개"
if [ "$code" -ne 0 ] || [ "$subtype" != "success" ] || [ "$is_error" != "false" ]; then
  [ "$code" -eq 124 ] && echo "제한 시간 ${minutes}분을 넘겼다."
  # 사용 한도 같은 사유는 세션 결과에 있다. 법인명은 mask_corp_names.py가 가린다.
  jq -r '"사유: \(.result // "" | tostring | .[0:300])"' "$LOG_DIR/${name}.json" 2>/dev/null || true
  tail -n 3 "$LOG_DIR/${name}.err" 2>/dev/null || true
  echo "::error title=Claude 실행 실패::${name} (${subtype})"
  exit 1
fi
