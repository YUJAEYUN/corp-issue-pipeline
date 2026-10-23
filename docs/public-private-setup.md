# 공개 코드 저장소와 비공개 데이터 저장소

GitHub Actions 사용 시간을 아끼려고 코드를 공개 저장소로 옮겼다. 공개 저장소의 Actions는 시간 제한 없이 무료다. 관리 법인 목록과 결과물은 공개하면 안 되므로 비공개 저장소에 남긴다.

| 저장소 | 공개 | 들어 있는 것 |
|---|---|---|
| `YUJAEYUN/corp-issue-pipeline` (코드 저장소) | 공개 | 코드, 테스트, 판단 기준(`prompt_template.md`), 워크플로 |
| `YUJAEYUN/corp-issue` (데이터 저장소) | 비공개 | 법인·팀 매핑 CSV, 배치·검토 결과 JSON, 엑셀, 이전 이력 |

## 실행 흐름

1. 워크플로는 코드 저장소에서 돈다.
2. 데이터가 필요한 job은 `DATA_REPO_TOKEN`으로 데이터 저장소를 `.data/`에 받고, `data_repo.sh pull`로 원래 경로에 복사한다. 코드와 Claude 프롬프트는 경로를 바꿀 필요가 없다.
3. 데이터를 받은 직후 `mask_corp_names.py`가 CSV의 법인명, 검색어, 별칭을 로그 가리기(`::add-mask::`)에 등록한다. 그 job의 이후 로그에서는 이 이름이 `***`로 나온다.
4. job 사이에 넘기는 파일과 Claude 세션 기록은 `seal.sh`로 암호화해 아티팩트로 올린다. 공개 저장소의 아티팩트는 로그인한 누구나 받을 수 있다.
5. Claude가 기사(외부 입력)를 읽는 judge·review job은 데이터 저장소 토큰을 받지 않는다. 암호화 아티팩트로만 데이터를 받는다.
6. publish는 결과를 `data_repo.sh push`로 데이터 저장소에 커밋한다. 검토 보고서는 그 커밋 메시지에 남는다. Step Summary에는 법인명이 없는 내용만 쓴다.

브랜치에서 시험 실행하면 결과는 데이터 저장소의 `trial/<브랜치>`에 커밋된다.

## 처음 설정

### 1. 코드 저장소 만들기

GitHub에서 빈 공개 저장소 `corp-issue-pipeline`을 만든다(README 추가하지 않음). 코드는 실제 법인명이 없는 상태로 첫 커밋부터 올린다. 지금 저장소의 이력에는 법인 목록이 있으므로 이 저장소를 공개로 바꾸지 않는다.

### 2. 데이터 저장소 토큰 만들기

GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**

- Repository access: **Only select repositories** → `YUJAEYUN/corp-issue`
- Permissions → Repository permissions → **Contents: Read and write**
- Expiration: 최대 1년. 만료되면 수집이 멈추므로 만료일을 달력에 적어 둔다.

### 3. 코드 저장소에 시크릿 넣기

코드 저장소 **Settings → Secrets and variables → Actions**

| 이름 | 종류 | 값 |
|---|---|---|
| `DATA_REPO_TOKEN` | Repository secret | 2단계에서 만든 토큰 |
| `ARTIFACT_PASSPHRASE` | Repository secret | 아무 긴 임의 문자열. 예: `openssl rand -base64 32` 출력 |
| `CLAUDE_CODE_OAUTH_TOKEN` | Repository secret | 기존 저장소와 같은 값(`claude setup-token`) |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | `NAVER` environment secret | 기존 저장소와 같은 값. **Settings → Environments → New environment**에서 `NAVER`를 만들고 그 안에 넣는다 |

`ARTIFACT_PASSPHRASE`는 진단용 세션 기록을 풀 때도 쓰므로 비밀번호 관리자에 따로 적어 둔다.

선택 사항(Variables 탭):

| 이름 | 값 |
|---|---|
| `DATA_REPO` | 데이터 저장소 이름. 기본 `YUJAEYUN/corp-issue` |
| `COMMIT_NAME`, `COMMIT_EMAIL` | 데이터 저장소 커밋 작성자. 비우면 `github-actions[bot]` |

### 4. 코드 저장소 Actions 설정

**Settings → Actions → General**

- Fork pull request workflows from outside collaborators: **Require approval for all external contributors**
- Workflow permissions: **Read repository contents and packages permissions**

포크 PR에서는 시크릿이 전달되지 않는다. PR에서 도는 워크플로는 `ci.yml`(테스트) 하나이고, 시크릿을 쓰지 않는다.

### 5. 데이터 저장소의 Actions 끄기

데이터 저장소 **Settings → Actions → General → Disable actions**. 끄지 않으면 예약 수집이 두 번 돌고 비공개 저장소의 사용 시간을 계속 쓴다.

### 6. 첫 실행

코드 저장소 Actions에서 **모듈2 - 뉴스 수집 파이프라인**을 수동으로 실행한다. 성공하면 검토가 이어서 돈다. 데이터 저장소에 수집·검토 커밋이 생기는지, 공개 로그와 Step Summary에 법인명이 없는지(`***`로 가려졌는지) 확인한다.

## 진단용 세션 기록 풀기

```bash
ARTIFACT_PASSPHRASE='<시크릿 값>' bash .github/scripts/unseal.sh claude-session-judge.enc 세션기록
```

## 공개 전 확인

코드 저장소에 올리기 전에 실제 법인명이 남았는지 검사한다.

```bash
python3 .github/scripts/check_no_corp_names.py --csv <데이터 저장소>/data/corp_team_mapping/corp_team_mapping.csv
```

## 한계

- 로그 가리기는 CSV에 있는 이름과 그 검색어·별칭만 가린다. 신규 후보 법인(관리 목록 밖)의 이름은 가리지 않는다.
- 공개 저장소의 예약 실행은 저장소 활동이 60일 없으면 꺼진다. 수집 워크플로가 실행할 때마다 자신을 다시 켜지만(keepalive), 꺼졌다면 Actions 탭에서 다시 켠다.
- 수집 키워드, 판단 기준, 필터 규칙은 공개된다. 법인 목록과 팀 배정, 결과물만 비공개다.
