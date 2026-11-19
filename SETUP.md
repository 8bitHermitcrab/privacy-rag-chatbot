# 설치·실행 가이드 (feature/detect-merge)

`feature/detect-merge` 브랜치를 처음 받아서 챗봇을 띄우기까지의 순서입니다.
탐지는 **하이브리드(KLUE-RoBERTa + KoELECTRA) + V6(AegisRAG step1) 통합**으로 동작하고, V6를 쓸 수 없으면 자동으로 하이브리드 단독으로 바뀝니다.

명령은 macOS·Linux 기준입니다. Windows는 [Windows에서 설치할 때](#windows에서-설치할-때)를 참고하세요.

---

## 0. 미리 준비할 것

| 항목 | 받는 곳 | 비고 |
|---|---|---|
| Python 3.12, Git, Docker Desktop | 각자 설치 | Docker는 켜 둔 상태로 진행 |
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
```

프롬프트 앞에 `(.venv)`가 보이면 됩니다. 이후 명령은 모두 이 상태에서 실행합니다.

## 3. 패키지 설치

```bash
sh install.sh
```

`install.sh`는 두 가지를 설치합니다.

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
| `POSTGRES_PASSWORD`, `RAG_PASSWORD`, `VAULT_PASSWORD` | 각자 정한 값 (내 컴퓨터 DB용) |
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
처음 켤 때 `init/`의 스크립트가 검색용(`rag`)과 매핑용(`vault`) 스키마·계정을 따로 만듭니다.

## 7. 테스트

```bash
python -m pytest tests -q
```

`28 passed`가 나오면 설치가 끝난 것입니다.

## 8. 문서 적재

```bash
python -m starter.ingest --data data/holdout_labeled.jsonl --reset
```

- 처음 한 번은 V6 모델을 내려받느라 오래 걸립니다.
- 끝날 때 `탐지 경로:`에 `merge`가 보이면 통합 탐지가 정상 동작한 것입니다.
- `[v6] 사용할 수 없음(...) → 60초 동안 하이브리드 단독으로 동작`이 보이면 V6 없이 적재된 것입니다. `HF_TOKEN`과 3단계 설치를 확인하세요.
- 탐지기 한쪽만 잡은 구간은 `outputs/review_queue_holdout_labeled.jsonl`에 **위치만** 기록됩니다 (원문 없음).

## 9. 챗봇 실행

```bash
uvicorn server:app --port 8000
```

브라우저에서 엽니다.

- 어흥이 버전: http://127.0.0.1:8000
- 심플 버전: http://127.0.0.1:8000/simple

서버를 켤 때 V6 모델을 미리 불러오므로 처음 몇십 초는 느릴 수 있습니다.

터미널에서 바로 확인하려면:

```bash
python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"
```

---

## 동작 방식 요약

```
질문 → 탐지(하이브리드 + V6 통합) → 가명처리·동명이인 처리 → 지역 토큰
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
| `EMBED_DIM`을 바꾼 뒤 적재 오류 | `docker compose down -v` → `docker compose up -d` → 8단계부터 |
| 다른 팀원과 토큰이 다름 | `HMAC_KEY`가 팀 공유 값과 같은지 확인 |
| API 429 (호출 한도) | 20~30초 뒤 다시 시도. 적재는 `EMBED_SLEEP`(기본 2초)로 조절 |

## Windows에서 설치할 때

`sh install.sh` 대신 아래 두 줄을 차례로 실행합니다 (PowerShell, 가상환경은 `.venv\Scripts\activate`).

```
pip install -r requirements.txt
pip install --no-deps "git+https://github.com/kohjunseo1/AegisRAG.git@644ab9d56cd24d5386c739712488299448940d26#subdirectory=step1_pii_detector"
```

## 커밋하면 안 되는 것

`.env`, `data/`, `models/`, `outputs/`는 `.gitignore`에 들어 있습니다. `git add .` 전에 `git status`로 한 번 확인하세요.
