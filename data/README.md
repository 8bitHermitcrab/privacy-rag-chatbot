# data/

이 폴더의 데이터 파일은 **레포에 올리지 않습니다** (`.gitignore` 의 `data/*`, 이 README 만 예외).

> ⚠ 모든 파일은 합성 데이터지만 **실제 개인정보·실제 문서는 절대 넣지 마세요.**
> 원문(가명처리 전)은 화면 캡처·외부 공유를 하지 않습니다.

## 1. 데이터

| 파일 | 역할 | 쓰는 곳 |
|---|---|---|
| `pii_synth_finance_dev.jsonl` | **학습용** (RoBERTa·koelectra 학습에 사용) | `tools/train_ner.py --train` |
| `pii_synth_finance_test.jsonl` | **공식 최종 평가** (설정 고르기·규칙 수정에 쓰지 않음) | `tools/compare_hf_models.py` |
| `holdout_labeled.jsonl` | 적재·검색 평가용 문서 187건. ⚠ 탐지 규칙을 이 파일을 보고 만들었으므로 **탐지 성능은 참고용** | `starter/ingest.py`, 검색 평가 |
| `holdout_persons_gold.jsonl` | holdout 인물 정답 (토큰 일관성 채점) | `eval/check_token_consistency.py` |
| `dev_persons_gold.jsonl` | dev 인물 정답 | 토큰 일관성 채점 |
| `holdout_retrieval_queries.jsonl` | holdout 검색 질의 | `starter/run_queries.py` |
| `holdout_retrieval_queries_gold.jsonl` | 검색 질의 정답 문서 | `eval/eval_holdout_retrieval.py` |

일부 스크립트 예시에 나오는 `data/dev.jsonl` 은 pii_synth_finance_dev.jsonl 데이터를 뜻합니다. 파일 이름에 맞게 바꿔서 실행하세요.

## 2. 4팀에서 받은 증강 데이터 (모델이 찾기 어려운 케이스)

| 파일 | 역할 |
|---|---|
| `pii_synth_finance_gen_dev.jsonl` | 개선용 |
| `pii_synth_finance_gen_hard_dev.jsonl` | 개선용 (규칙·모델 비교) |
| `pii_synth_finance_gen_hard_eval.jsonl` | **설정 고르기용** (학습 중 검증 `--eval`, `tools/tune_hybrid.py --data`) |
| `pii_synth_finance_gen_holdout.jsonl` | 참고용 |
| `pii_synth_finance_gen_fresh.jsonl` | **새 템플릿 일반화 테스트** (학습·설정 고르기에 쓰지 않음) |
| `*_conv.jsonl` | 위 파일을 **제공된 데이터 라벨 기준**으로 고친 버전 (아래 참고) |

### 라벨 기준 차이 (`*_conv.jsonl`)
- 제공된 데이터는 성만 있는 이름(`김 과장`, `박 씨`)을 개인정보로 **라벨하지 않습니다**.
- 4팀 데이터는 이를 라벨합니다 (fresh 78건, hard_eval 37건).
- 우리 팀은 제공된 데이터를 기준으로 따릅니다 (`PII_SURNAME_TITLE_IS_PII=0`). 그래서 4팀 데이터로 평가할 때는 성만 있는 정답을 뺀 `*_conv.jsonl` 을 씁니다.

## 3. 우리가 만든 데이터

| 파일 | 만드는 법 | 역할 |
|---|---|---|
| `synth_train_10k.jsonl` | 아래 명령 | koelectra 추가 학습용 합성 문서 1만 건 |

```bash
python -m tools.gen_synth --n 10000 --out data/synth_train_10k.jsonl --seed 7 \
  --exclude-names-from data/holdout_labeled.jsonl data/pii_synth_finance_test.jsonl \
  data/pii_synth_finance_dev.jsonl data/pii_synth_finance_gen_hard_eval.jsonl \
  data/pii_synth_finance_gen_holdout.jsonl data/pii_synth_finance_gen_fresh.jsonl
```
`--exclude-names-from` 으로 평가 데이터에 나오는 이름은 합성 데이터에 쓰지 않습니다 (평가 오염 방지). 같은 `--seed` 면 같은 파일이 다시 만들어집니다.

## 4. 팀 검색 질의

| 파일 | 역할 |
|---|---|
| `queries_team/*.csv` | 팀원별 질의 (`tools/draft_queries.py` 로 만들고 직접 수정) |
| `queries30.jsonl` / `queries30.csv` | 팀 질의 합본 (`tools/merge_queries.py`) |
| `queries_team/queries36.jsonl` | 질의 36개 평가용 |

## 데이터 사용 규칙 요약
- **학습**: dev + `synth_train_10k.jsonl` 만.
- **설정 고르기**: `gen_hard_eval`.
- **최종 평가**: test (공식), `gen_fresh_conv` (일반화).
- holdout 은 학습·검증(`--eval`)에 쓰지 않습니다 (`train_ner.py` 가 막아 둠).

## 폴더 구조 예시
```
data/
├── README.md
├── pii_synth_finance_dev.jsonl
├── pii_synth_finance_test.jsonl
├── holdout_labeled.jsonl
├── holdout_persons_gold.jsonl
├── dev_persons_gold.jsonl
├── holdout_retrieval_queries.jsonl
├── holdout_retrieval_queries_gold.jsonl
├── pii_synth_finance_gen_*.jsonl
├── pii_synth_finance_gen_*_conv.jsonl
├── synth_train_10k.jsonl
├── queries30.jsonl
└── queries_team/
```
