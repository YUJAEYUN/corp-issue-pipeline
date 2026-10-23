---
description: 특정 날짜의 뉴스 배치 검토와 이슈 트래커 엑셀 생성을 GitHub Actions로 실행한다
argument-hint: [날짜 YYYY-MM-DD, 여러 개면 공백 구분. 생략하면 아직 검토 안 된 날짜]
---

날짜: $ARGUMENTS

검토와 엑셀 생성은 GitHub Actions의 **모듈2 - LLM 배치 검토 (Claude Code)** 워크플로가 한다. 이 세션에서 배치를 손으로 판정하지 않는다. 저장소 루트 `AGENTS.md`의 "엑셀 만들어줘" 절차를 따른다.

1. 날짜가 있으면 그 날짜의 `llm_review_result_<날짜>.json`과 `outputs/<YYYY년 M월>/이슈트래커(YYMMDD).xlsx`가 데이터 저장소(`YUJAEYUN/corp-issue`, 비공개) main에 이미 있는지 확인한다. 이미 있으면 위치를 알려 주고, 다시 검토할지 사용자에게 묻는다.
2. 검토 워크플로를 실행한다. 날짜가 없으면 `dates`를 비워 둔다. 그러면 워크플로가 아직 검토 안 된 날짜를 고른다.
   ```bash
   gh api -X POST repos/YUJAEYUN/corp-issue-pipeline/actions/workflows/llm-review.yml/dispatches \
     -f ref=main -f 'inputs[dates]=$ARGUMENTS'
   ```
3. 실행이 끝날 때까지 상태를 확인한다. 성공하면 데이터 저장소 main을 받아 생성된 엑셀을 사용자에게 보내고, 그 검토 커밋 메시지의 보고서(애매/신규/최종 건수, 제거한 오매칭)를 요약해 알린다. 실패하면 실패한 job과 로그의 원인을 알린다.
