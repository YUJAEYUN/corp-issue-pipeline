# 모듈 2: 뉴스 수집과 선별

관리 법인과 영업 키워드로 네이버 뉴스를 검색하고, 영업에 쓸 만한 기사만 골라 이슈 JSON으로 만든다. 판단이 애매한 기사는 LLM 검토 목록으로 따로 모은다.

- **이중 검색** · 관리 법인은 법인명으로, 신규 후보는 영업 키워드로
- **자동 선별** · 키워드 수와 법인명 일치 여부로 통과·검토·제외 판정
- **중복 통합** · 제목 유사도와 기사 속 수치로 같은 사건의 기사를 하나로
- **요약 보강** · 설명이 부족한 기사는 원문 메타 설명으로 보완
- **매일 자동 실행** · GitHub Actions로 주말·공휴일 포함 매일 01:15 KST 수집

법인과 팀의 기준 데이터는 [`corp_team_mapping.csv`](../data/corp_team_mapping/corp_team_mapping.csv)다.

## 1. 설치와 인증

저장소 루트에서 의존성을 설치한다.

```bash
python3 -m pip install -r module2_news_pipeline/requirements.txt
```

실행 환경에 아래 두 환경변수를 설정한다.

- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

코드는 `.env` 파일을 자동으로 읽지 않는다. 인증정보는 저장소에 커밋하지 않는다.

[`config.json`](config.json)의 `naver_api.variant`는 기본적으로 `hub`를 사용한다. 발급받은 인증 방식이 다르면 `legacy`로 바꾼다. HTTP 401 또는 403 오류가 나면 환경변수, 발급 서비스, `variant`, 엔드포인트가 서로 맞는지 확인한다.

## 2. 실행

### 전일 뉴스 수집

```bash
python3 module2_news_pipeline/pipeline.py
```

한국 시간 기준 전일 뉴스를 수집한다.

### 기간을 직접 지정

```bash
python3 module2_news_pipeline/pipeline.py \
  --date 2026-09-17 \
  --date-from 2026-09-10 \
  --date-to 2026-09-16
```

| 옵션 | 용도 | 기본값 |
|---|---|---|
| `--date` | 결과 파일명과 `run_date` | 실행 환경의 오늘 날짜 |
| `--date-from` | 수집 시작일, 해당 날짜 포함 | 한국 시간 기준 어제 |
| `--date-to` | 수집 종료일, 해당 날짜 포함 | 한국 시간 기준 어제 |
| `--config` | 설정 JSON 경로 | 이 모듈의 `config.json` |
| `--corp-csv` | 법인·팀 CSV 경로 | `data/corp_team_mapping/corp_team_mapping.csv` |
| `--output-dir` | 결과 폴더(이 밑에 월별 하위 폴더가 생김) | 이 모듈의 `output/` |

`--date-from`과 `--date-to`는 함께 사용해야 하며 종료일은 시작일보다 빠를 수 없다. `--date`만 바꿔도 검색 기간은 바뀌지 않는다.

> **주의:** 같은 `--date`로 다시 실행하면 기존 이슈 파일과 검토 배치를 덮어쓴다. 이전 배치에 작성한 검토 결과는 새 배치에 자동으로 이어지지 않는다.

## 3. 결과 확인

실행이 끝나면 `module2_news_pipeline/output/<YYYY년 M월>/`에 다음 파일이 생긴다. `run_date`가 속한 달 기준으로 폴더를 만들며(예: 2026-09-17 실행 결과는 `output/2026년 9월/`), 파일이 한 폴더에 계속 쌓이지 않게 나눈다.

| 파일 | 확인할 내용 |
|---|---|
| `issues_<날짜>.json` | 자동 선별을 통과한 기사와 수집 범위 |
| `llm_review_batch_<날짜>.json` | LLM이 판정할 기존 법인 기사, 신규 후보, 요약 |

검토 배치는 [LLM 검토](llm_review/README.md) 워크플로가 이어서 자동으로 처리한다.

`issues` 파일의 `coverage_warnings`에 값이 있으면 일부 검색에서 기간 전체를 수집하지 못했을 수 있다(네이버 검색 상한 start 1000). 최종 엑셀에는 표시되지 않으므로 필요하면 이 파일에서 확인한다.

## GitHub Actions로 실행

워크플로우 파일은 [`.github/workflows/module2-pipeline.yml`](../.github/workflows/module2-pipeline.yml)이다.

최초 한 번만 저장소의 `NAVER` environment에 `NAVER_CLIENT_ID`와 `NAVER_CLIENT_SECRET`을 등록한다.

### 자동 실행

주말·공휴일을 포함해 매일 01:15 KST에 자동으로 돌며(실제 시작은 GitHub 예약 지연으로 보통 05~08시), 매번 전일 뉴스를 수집한다. 수집이 성공하면 [LLM 검토](llm_review/README.md) 워크플로가 이어서 돌아 엑셀까지 만든다. 주말·공휴일치는 날마다 검토하고, 연휴 다음 첫 영업일에 연휴 합본이 함께 만들어진다.

결과 커밋은 `YUJAEYUN` 계정 이메일로 남아 GitHub 프로필에 귀속된다. 계정 이메일이 바뀌면 워크플로우의 `결과 커밋 & 푸시` 단계에서 `git config user.email`도 같이 바꾼다.

### 수동 실행

1. GitHub Actions에서 **모듈2 - 뉴스 수집 파이프라인**을 연다.
2. **Run workflow**를 선택한다.
3. 기간을 지정하려면 `date_from`과 `date_to`를 모두 입력한다.
4. 실행이 끝나면 커밋된 `module2_news_pipeline/output/` 파일을 확인한다.

수집이 성공하면 LLM 검토와 엑셀 생성은 이어서 자동으로 돈다.

## 선별 방식

- **기존 거래 법인:** 법인명으로 검색한다.
- **신규 법인 후보:** 영업 키워드로 검색한다.
- **자동 선별:** 키워드 수와 법인명 확인 여부를 기준으로 통과·검토·제외한다.
- **중복 제거:** 제목 유사도와 기사에 포함된 수치를 비교해 같은 사건의 기사를 묶는다. 원문 링크는 `related_articles`에 보존한다.
- **요약 보강:** 설명이 부족한 기사만 원문 페이지의 `og:description`을 확인한다. 충분한 설명을 얻지 못하면 검토 배치로 보낸다.

키워드, 영업시그널 분류, 별칭 규칙, 제목 유사도 기준은 [`config.json`](config.json)에서 관리한다. 원문 설명 보강을 끄려면 `enable_meta_description_fallback`을 `false`로 설정한다. 전체 판정 규칙과 알려진 제약은 [상세 스펙](../docs/spec.md)을 참고한다.

## 검증

```bash
python3 -m pytest module2_news_pipeline/tests/ -v
```

고정된 응답을 사용해 수집 기간, 필터, 중복 제거, 법인 분류, 요약, 검토 결과 병합을 검사한다. 실제 API 인증과 실제 뉴스 선별 품질은 별도로 확인해야 한다.
