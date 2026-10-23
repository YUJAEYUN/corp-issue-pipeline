# LLM 검토와 결과 병합

모듈 2가 뽑은 JSON에서 애매한 항목을 LLM이 판정하고 다듬어, 최종 이슈 JSON 하나로 병합하는 단계다. 엑셀은 이 JSON으로 [모듈 3](../../module3_excel_generator/README.md)이 만든다.

- **애매한 기사 판정** · 동명 법인, 애널리스트 코멘트, 단순 행사 기사 등 반려 패턴 기반 검토
- **신규 법인 특정** · 기사 주체 법인과 영업시그널 확정
- **요약 표준화** · `[법인명] + 핵심 사실 + 수치` 형식으로 통일
- **결과 병합** · 판정 결과를 `issues_<날짜>.json`에 반영

## 실행 방법

검토는 GitHub Actions의 **모듈2 - LLM 배치 검토 (Claude Code)** 워크플로([`llm-review.yml`](../../.github/workflows/llm-review.yml))가 한다.

- **자동:** 뉴스 수집 워크플로가 성공하면 이어서 돈다. 아직 결과가 없는 날짜를 모두 검토하고, 엑셀까지 만들어 main에 커밋한다.
- **다시 실행:** Actions에서 **Run workflow**를 누르고 `dates`에 날짜를 넣는다(여러 개면 공백 구분, 비우면 아직 검토 안 된 날짜). Claude Code에서는 `/make-excel <날짜>`가 같은 일을 한다.
- **기준 시험:** main이 아닌 브랜치를 골라 돌리면 그 브랜치의 `prompt_template.md`로 검토하고 결과도 그 브랜치에 커밋한다. 기준을 바꾼 뒤 main과 비교할 때 쓴다.

Claude는 API 키가 아니라 구독(Pro/Max) OAuth 토큰으로 돈다. 로컬에서 `claude setup-token`으로 발급한 토큰을 저장소 시크릿 `CLAUDE_CODE_OAUTH_TOKEN`에 넣는다.

## 단계별로 하는 일

| 단계 | 하는 일 | 쓰는 것 |
|---|---|---|
| plan | 대상 날짜를 정하고 배치를 100건씩 조각으로 나눈다 | `chunk_batch.py split` |
| judge | 조각마다 Claude가 `ambiguous_existing_corp_issues`, `new_corp_candidates`를 판정한다(한 job 안에서 동시 4개, `judge_chunks.sh`) | `prompt_template.md` 1~2절, 4절 |
| review | 조각 판정을 합쳐 `llm_review_result_<날짜>.json`을 만들고 `issues_<날짜>.json`에 병합한다 | `chunk_batch.py join`·`check`, `merge_llm_results.py` |
| review (1차) | Claude가 사전 통과 항목 재확인, 여러 법인에 걸린 기사, 재보도·후속, 키워드 열을 확인하고 모든 요약을 다시 쓴 뒤 같은 사건을 묶는다 | `prompt_template.md` 3절, 4-1~4-5절, `merge_llm_results.py --recluster` |
| review (2차) | Claude가 처음 보는 눈으로 최종 이슈를 다시 점검한다. 수동 검토에서도 이 정리가 결과를 크게 바꿨다(2026-09-30: 37건 -> 23건) | `prompt_template.md` 4절 |
| publish | 판정 누락을 확인하고 테스트한 뒤 엑셀을 만들어 JSON과 함께 커밋한다. 연휴 다음 첫 영업일이면 합본도 만든다 | `chunk_batch.py check`, 모듈 3, `combine_dates.py --holiday-span` |

- 판정이 하나라도 빠지면 엑셀을 만들지 않고 실패로 끝난다. 2026-10-01 재수집 배치(2,100여 건)를 한 번에 판정하지 못하고 빈 엑셀이 커밋된 일 때문이다.
- 기사를 읽는 plan·judge·review는 읽기 권한만 가진다. 쓰기 권한은 publish에만 있다.
- 검토 보고서(날짜별 애매/신규/최종 건수, 제거한 오매칭, 묶은 중복)는 실행의 Step Summary와 커밋 메시지에 남는다.

판단 기준(반려 패턴, 요약 형식, 필드별 작성법)은 전부 [`prompt_template.md`](prompt_template.md)에 있다. 이 README는 "언제 무엇이 도는지"를, `prompt_template.md`는 "어떻게 판단하는지"를 담는다.

## 연휴 합본

주말·공휴일 배치도 날마다 따로 검토한다(Claude 구독의 5시간 사용 한도에 한꺼번에 몰리지 않게). 대신 연휴 다음 첫 영업일 실행의 publish 단계가 `combine_dates.py --holiday-span <날짜>`로 이미 검토된 쉬는 날 결과를 합쳐 연휴 합본 JSON과 엑셀(예: `이슈트래커(261009~261012).xlsx`)을 함께 커밋한다. 이 합본은 LLM을 쓰지 않는다. 법정공휴일은 `holidays` 패키지로 판단하고, 회사 휴무일처럼 따로 쉬는 날은 `config.json`의 `extra_non_business_days`에 적는다.

합본을 다시 만들어야 하면 날짜별 검토가 끝난 상태에서 스크립트만 돌린다.

```bash
python3 module2_news_pipeline/llm_review/combine_dates.py --dates 2026-10-09 2026-10-10 2026-10-11 2026-10-12
```

`issues_2026-10-09~2026-10-12.json`이 생기고 법인별 중복이 한 번 더 정리된다. 엑셀은 **이슈트래커 엑셀 생성** 워크플로나 모듈 3에 `--date 2026-10-09~2026-10-12`로 만든다.

## 스크립트를 직접 쓸 때 주의

- 이슈, 검토 배치, 검토 결과는 반드시 같은 수집 실행에서 만들어진 파일을 쓴다. `article_id`는 수집할 때마다 달라질 수 있다.
- `merge_llm_results.py`는 이슈 JSON을 덮어쓴다. 같은 결과를 두 번 병합하면 중복이 생기므로, 다시 병합하려면 병합 전 이슈 파일을 먼저 복원한다.
- 신규 후보가 실제로는 기존 법인이면 결과에 `verdict: "new_lead"`와 `is_actually_known: true`를 함께 쓴다.
