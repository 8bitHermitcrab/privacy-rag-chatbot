# 실전 2 산출물

| 산출물 | 파일 |
| --- | --- |
| ① 챗봇 데모 | `server.py`, `web/index.html`(어흥이), `web/simple.html`, `app.py`(Streamlit), `starter/chat.py` |
| ② 가명처리 모듈 | `starter/pii_pipeline.py`, `starter/rules_detector.py`, 모델 `models/roberta_small_v1/`(레포 밖) |
| ③ 토큰 일관성 채점 결과 | `deliverables/token_consistency.md` |
| ④ 검색 평가 결과 | `deliverables/retrieval_eval_v3.md`, `deliverables/retrieval_versions.md`, `deliverables/run_v3_recheck.jsonl`, `deliverables/queries30_eval_v3.md` |
| ⑤ 대화 시연 예시 | `deliverables/demo_hcx005.md`, `deliverables/demo_hcx007.md` |

## 이번에 만든 파일

- retrieval_eval_v3.md — Hit@1 0.773, Hit@5 0.920, MRR 0.832, Confuse@10 0.538
- run_v3_recheck.jsonl
- retrieval_versions.md — 결과 파일 8개 비교
- token_consistency.md — 원문 노출 30/912 (3.3%), 쪼개짐 7명, 합쳐짐 4개 토큰
- demo_hcx007.md
- demo_hcx005.md — 없음: 노션 '챗봇 데모 시연 기록'의 HCX-005 내용을 붙여 넣어 만드세요

## 재현

```
docker compose up -d
python -m starter.ingest --data data/holdout_labeled.jsonl --reset
python -m starter.run_queries --out outputs/run_v3_recheck.jsonl   # SEARCH_MODE=v3
python tools/eval_queries.py --queries data/queries30.csv        # 팀이 정한 질의 30개
python tools/make_deliverables.py
uvicorn server:app --port 8000
```

.env(API 키·HMAC 키)와 모델 폴더는 레포에 없습니다. 팀 비공개 채널로 받으세요.
