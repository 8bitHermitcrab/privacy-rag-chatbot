# 설치·실행 가이드 (feature/detect-merge)

`feature/detect-merge` 브랜치를 처음 받아서 챗봇을 띄우기까지의 순서입니다.
탐지는 **하이브리드(KLUE-RoBERTa + KoELECTRA) + V6(AegisRAG step1) 통합**으로 동작하고, V6를 쓸 수 없으면 자동으로 하이브리드 단독으로 바뀝니다.

명령은 macOS·Linux 기준입니다. Windows는 [Windows에서 설치할 때](#windows에서-설치할-때)를 참고하세요.

> **Python 3.12로 통일합니다.** 탐지기(하이브리드 + V6)와 DB·관리자 테스트를 모두 3.12에서 확인했습니다.
> 레포의 `.python-version`(pyenv용)과 `install.sh`의 버전 확인이 3.12가 아니면 설치를 멈춥니다.

---

## 0. 미리 준비할 것

| 항목 | 받는 곳 | 비고 |
|---|---|---|
| **Python 3.12**, Git, Docker Desktop | 각자 설치 | 3.13·3.14 아님. Docker는 켜 둔 상태로 진행 |
| `models/roberta_small_v1`, `models/koelectra_ft5` | 팀 내부 공유 | 저장소에 없음 (`.gitignore`) |
| `data/holdout_labeled.jsonl` 등 데이터 | 팀 내부 공유 | 저장소에 없음. 강사 제공 데이터라 외부 공유 금지 |
| `HMAC_KEY` | 팀 내부 공유 | 팀 전원이 같은 값이어야 토큰이 서로 맞음 |
| 임베딩·LLM API 키 | 팀 내부 공유 | |
| `HF_TOKEN` | 각자 Hugging Face에서 발급 | V6 모델 다운로드용. 다른 사람과 공유하지 않음 |

---

## 1. 브랜치 받기

```bash
git clone -b feature/detect-merge https://github.com/8bitHermitcrab/privacy-rag-chatbot.git
cd privacy-rag-chatbot
```

## 2. 가상환경 만들기

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python --version
```

`Python 3.12.x`가 나오고 프롬프트 앞에 `(.venv)`가 보이면 됩니다. 이후 명령은 모두 이 상태에서 실행합니다.
pyenv를 쓰면 레포 폴더에서 `.python-version`을 읽어 3.12가 자동으로 잡힙니다 (없으면 `pyenv install 3.12`).

> 다른 버전으로 만든 `.venv`가 있으면 폴더를 지우고 다시 만드세요. 이미 만든 가상환경의 Python 버전은 바꿀 수 없습니다.
> `.venv`와 `.env`는 다른 파일입니다. `.env`는 지우지 마세요.

## 3. 패키지 설치

```bash
sh install.sh
```

`install.sh`는 Python 3.12인지 먼저 확인한 뒤 두 가지를 설치합니다.

1. `requirements.txt` (우리 코드 + `onnx`·`onnxruntime`)
2. V6 탐지기 패키지 (`AegisRAG` 커밋 `644ab9d`의 `step1_pii_detector`)를 `--no-deps`로

설치 중 아래와 같은 경고가 나오는 것은 **정상**입니다.

```
step1-pii-detector 0.1.0 requires transformers<5, ... which is incompatible.
step1-pii-detector 0.1.0 requires numpy<2.0, ... which is incompatible.
step1-pii-detector 0.1.0 requires onnx<1.20, ... which is incompatible.
```

V6 패키지가 요구하는 버전 상한이 우리 환경과 달라 `--no-deps`로 설치하기 때문입니다.
아래 버전 조합에서 V6 결과가 원래 환경과 같음(blind 171/171건 동일)을 확인했습니다.

| 패키지 | 버전 |
|---|---|
| numpy | 2.5.4 |
| torch | 2.11.0 |
| transformers | 5.20.2 |
| onnx | 1.23.2 |
| onnxruntime | 1.32.0 |

## 4. 모델과 데이터 넣기

저장소 최상단에 아래 구조로 넣습니다.

```
privacy-rag-chatbot/
├── models/
│   ├── roberta_small_v1/
│   └── koelectra_ft5/
└── data/
    └── holdout_labeled.jsonl
```

V6 모델(A.X, Veil)은 넣을 필요 없습니다. 처음 실행할 때 Hugging Face에서 자동으로 내려받습니다.

## 5. `.env` 만들기

```bash
cp .env.example .env
```

`.env`를 열어 아래 항목을 채웁니다.

| 항목 | 값 |
|---|---|
| `POSTGRES_PASSWORD`, `RAG_PASSWORD`, `VAULT_PASSWORD`, `MIGRATION_PASSWORD` | 각자 정한 값 (내 컴퓨터 DB용, **영문·숫자**) |
| `EMBED_PROVIDER` | `api` |
| `EMBED_API_KEY`, `LLM_API_KEY` | 팀 공유 키 |
| `HMAC_KEY` | 팀 공유 값 |
| `HF_TOKEN` | 본인 토큰 (맨 아래에 한 줄 추가) |

이미 들어 있는지 확인할 값:

```
PII_V6=inproc
PII_BOOST_PERSON=0
PII_BOOST_ADDRESS=1
EMBED_DIM=1024
SEARCH_MODE=v3
SEARCH_REGION=1
```

> `.env`는 절대 커밋하지 않습니다. `HF_TOKEN`은 `.env.example`에도 넣지 않습니다.

## 6. DB 켜기

```bash
docker compose up -d
docker compose ps
```

상태가 `healthy`가 되면 다음으로 넘어갑니다.

## 6-1. DB 구조 준비 (처음 한 번)

```bash
python -m starter.migrate --prepare
python -m starter.migrate
python -m starter.business_admin --prepare
```

1. DB 전용 계정(`migration_user` 등)과 빠진 필수 구조를 준비하고 버전을 등록합니다.
2. 미적용 DB 버전 변경을 적용합니다 (현재 `0002`).
3. 업무 관리자용 계정(`auth_user`)과 첫 관리자 `businessadmin`을 만듭니다.
   초기 비밀번호는 `~/.local/share/rag-server-admin/business-initial-login.txt`에 있습니다 (Git 밖). 첫 로그인 후 변경하고 파일은 지우세요.

세 명령 모두 기존 청크·토큰·계정 비밀번호를 지우거나 초기화하지 않습니다.
이미 준비된 DB에서 다시 실행해도 됩니다. 서버를 켤 때도 2번은 자동으로 실행됩니다.

> **DB를 지우지 마세요.** `docker compose down -v`는 업무 계정·권한 데이터까지 모두 삭제합니다.

## 7. 테스트

```bash
python -m pytest tests -q
python -m unittest starter.test_business_admin server_admin.test_admin -q
```

첫 줄은 `28 passed`, 둘째 줄은 `OK`가 나오면 설치가 끝난 것입니다.
둘째 줄은 6-1단계를 마친 뒤에 실행해야 합니다 (`business-runtime.json` 필요).

## 8. 문서 적재

```bash
python -m starter.ingest --data data/holdout_labeled.jsonl --reset
```

- 처음 한 번은 V6 모델을 내려받느라 오래 걸립니다.
- 끝날 때 `탐지 경로:`에 `merge`가 보이면 통합 탐지가 정상 동작한 것입니다.
- `[v6] 사용할 수 없음(...) → 60초 동안 하이브리드 단독으로 동작`이 보이면 V6 없이 적재된 것입니다. `HF_TOKEN`과 3단계 설치를 확인하세요.
- 탐지기 한쪽만 잡은 구간은 `outputs/review_queue_holdout_labeled.jsonl`에 **위치만** 기록됩니다 (원문 없음).
- 주의: 지금은 `rag.document_info`에 이미 등록된 문서 ID만 적재할 수 있습니다 (`chunks.doc_id` 외래키).
  holdout 문서는 6-1단계에서 자동 등록됩니다. 새 데이터셋 적재는 `ingest.py` 수정 후 가능합니다.

## 8-1. 인물사전 만들기 (동명이인·같은 사람 연결)

적재는 이름 하나를 토큰 하나로만 바꿉니다(`<PERSON_xxxxxxxx>`, 동명이인 구분 없음).
같은 사람인지, 동명이인인지는 적재 뒤 인물사전(`vault.person`)이 판단합니다.

```bash
# 미리보기 (DB 변경 없음). 마지막에 나오는 plan 값을 복사합니다.
python -m starter.person_dictionary --sensitivity balanced
# 반영
python -m starter.person_dictionary --sensitivity balanced --apply --plan <직전_plan_값>
```

- 서버 관리자 화면(`http://127.0.0.1:9000`)의 **인물사전** 메뉴에서도 같은 작업을 할 수 있습니다.
- 보류된 항목은 업무 관리자 화면의 인물 검토에서 판정합니다.
- 8단계를 다시 실행(재적재)했다면 이 단계도 다시 실행하세요. 인물사전은 문서를 다시 가명처리해 DB 본문과 대조합니다.

## 9. 챗봇 실행

```bash
uvicorn server:app --port 8000
```

브라우저에서 http://127.0.0.1:8000/login 으로 로그인합니다.

서버를 켤 때 DB 버전 확인 → V6 모델 사전 로딩 순서로 진행되며, 처음 몇 초~몇십 초는 느릴 수 있습니다.

> 웹 챗봇은 로그인한 직원에게 **확정된 담당·팀 문서만** 검색합니다.
> 직원·팀·직책 정책과 문서 담당자를 등록하기 전에는 "검색 가능한 확정 문서가 없습니다"(403)가 나오는 것이 정상입니다.
> 권한 없이 탐지·검색만 확인하려면 터미널 명령을 쓰세요.

터미널에서 바로 확인하려면:

```bash
python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"
```

## 10. 웹 챗봇 시험 계정 만들기 (로컬 전용)

웹 챗봇은 확정된 직원에게 배정된 문서만 검색합니다. 관리자 화면에는 아직 문서 배정 메뉴가 없으므로,
로컬에서 시험할 때는 아래 스크립트로 시험용 사번 계정과 문서 권한을 한 번에 만듭니다.

```bash
python -m tools.dev_chat_account --create
```

```
시험 계정 준비 완료
  아이디: DEV-0001
  임시 비밀번호: (실행할 때마다 새로 발급)
  팀: DEV_TEAM / 직책: 사원 / 검색 범위: TEAM
  이번에 확정한 문서: 187건, 시험팀 문서 합계: 187건
  주소: http://127.0.0.1:8000/chat/login
```

1. http://127.0.0.1:8000/chat/login 에서 `DEV-0001`과 임시 비밀번호로 로그인합니다.
   이미 다른 계정(예: `businessadmin`)으로 로그인돼 있으면 **다른 계정으로 로그인**을 누릅니다.
2. 새 비밀번호(14자 이상)로 바꾸면 챗봇 화면으로 이동합니다.
3. 질문하면 시험팀에 배정된 문서에서 검색합니다.

스크립트가 하는 일 (한 트랜잭션, `.env`의 DB 관리자 계정 사용)

- 시험팀 `DEV_TEAM`, 사번 `DEV-0001`(직책: 사원, 확정) 생성
- `DEV_TEAM`·사원 검색 정책 `TEAM`(팀 문서 전체) 등록
- 아이디 = 사번인 `STAFF` 계정 생성, 임시 비밀번호 발급
- 담당자·팀이 비어 있는 `PENDING` 문서를 시험팀으로 확정 (이미 배정된 문서는 그대로)

| 옵션 | 뜻 |
|---|---|
| `--emp T-1001` | 다른 사번으로 만들기 (아이디도 같은 값) |
| `--scope OWN` | 본인 담당 문서만 검색 |
| `--undo` | 되돌리기 (삭제하지 않고 비활성화: 계정 RETIRED, 문서 PENDING, 정책 NONE) |

임시 비밀번호를 잃어버렸으면 `--create`를 다시 실행하면 새로 발급됩니다.

> **주의**: 실제 담당 기준 없이 모든 미배정 문서를 시험팀에 몰아 주는 **로컬 시험 전용** 도구입니다.
> 공유·운영 DB에서는 실행하지 마세요. 임시 비밀번호는 다른 사람과 공유하지 마세요.

---

## 동작 방식 요약

```
질문 → 탐지(하이브리드 + V6 통합) → 가명처리(이름 1개 = 토큰 1개) → 지역 토큰
     → 질문 임베딩 → 검색(v3: 토큰 일치 우선, 의미 검색으로 채움) → LLM 답변 → 화면
```

- 탐지 통합 규칙: 둘 다 잡은 구간은 **V6 경계 + 하이브리드 라벨**, 한쪽만 잡은 구간도 포함하고 검수 대기열에 기록
- V6 실패 시: 60초(`PII_V6_RETRY_S`) 동안 하이브리드 단독, 이후 다시 시도
- 외부 API(임베딩·LLM)에는 **가명처리된 글만** 전송
- 화면의 토큰(`<PERSON_xxxxxxxx>`)은 복원하지 않음 (복원·권한·답변 재검사는 종합 단계)

---

## 자주 막히는 곳

| 증상 | 해결 |
|---|---|
| `Connection refused` (DB) | Docker Desktop 실행 후 `docker compose up -d` |
| `HMAC_KEY 가 비어 있습니다` | `.env`의 `HMAC_KEY` 확인 |
| `HF_TOKEN is required` / `[v6] 사용할 수 없음` | `.env`에 `HF_TOKEN` 추가, `sh install.sh` 다시 실행 |
| `DB 계정 인증 실패` (migrate) | `.env`의 `POSTGRES_PASSWORD`·`MIGRATION_PASSWORD` 확인. 6-1단계를 `--prepare`부터 실행 |
| `business-runtime.json` 없음 | 6-1단계 3번(`business_admin --prepare`) 실행 |
| `Python 3.12가 필요합니다` | `.venv`를 지우고 Python 3.12로 다시 만들기 (2단계) |
| `EMBED_DIM` 변경 | DB를 지우지 말고 DB 담당자와 재임베딩 계획을 먼저 정하기 |
| 다른 팀원과 토큰이 다름 | `HMAC_KEY`가 팀 공유 값과 같은지 확인 |
| API 429 (호출 한도) | 20~30초 뒤 다시 시도. 적재는 `EMBED_SLEEP`(기본 2초)로 조절 |

## Windows에서 설치할 때

Python 3.12를 python.org에서 설치한 뒤 가상환경을 만듭니다 (PowerShell).

```
py -3.12 -m venv .venv
.venv\Scripts\activate
python --version
```

`sh install.sh` 대신 아래 두 줄을 차례로 실행합니다.

```
pip install -r requirements.txt
pip install --no-deps "git+https://github.com/kohjunseo1/AegisRAG.git@644ab9d56cd24d5386c739712488299448940d26#subdirectory=step1_pii_detector"
```

Windows에서는 `시스템_시작.cmd`로 DB 준비·관리자 실행을 한 번에 할 수도 있습니다 (README 참고). 이 파일도 `.venv`의 Python을 그대로 씁니다.

## 커밋하면 안 되는 것

`.env`, `data/`, `models/`, `outputs/`는 `.gitignore`에 들어 있습니다. `git add .` 전에 `git status`로 한 번 확인하세요.

> 데이터는 Git으로 공유하지 않습니다. 브랜치를 받기(pull·merge) 전에 `cp -R data ~/data_backup`으로 백업해 두세요.
