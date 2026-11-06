"""앙상블 임계값 고르기 (dev 로만!)

    python -m tools.tune_ensemble --data data/pii_synth_finance_gen_hard_dev.jsonl
    python -m tools.tune_ensemble --data data/dev.jsonl --tta none          # 증강 효과 비교

모델은 문서마다 한 번만 돌리고(글자별 확률 저장), 임계값 조합만 바꿔 가며 채점합니다.
출력: 조합별 이름 노출·경계·오탐. '노출이 가장 적으면서 오탐이 허용 범위' 인 줄을 고른 뒤
      .env 에 PII_TAU_SPAN / PII_TAU_CAND 로 적고, test 로 한 번만 확인하세요.
"""
import argparse
import json
import os
import time

from starter import model_ensemble as E
from starter import pii_pipeline as P
from starter.detect_boost import enhance
from tools.eval_detect_boost import score

SPAN_GRID = [1.01, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2]     # 1.01 = 모델 구간 추가 끔
CAND_GRID = [1.01, 0.5, 0.3, 0.2, 0.15, 0.1, 0.05]     # 1.01 = 후보 검증 끔


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--tta", default="shift,join", help="shift,join | shift | join | none")
    ap.add_argument("--max-fp-increase", type=int, default=20, help="기준 대비 이름 오탐 증가 허용치 (추천 줄 고르기용)")
    a = ap.parse_args()
    E.TTA = [] if a.tta == "none" else a.tta.split(",")
    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]

    P.ENSEMBLE, P.BOOST_ADDRESS, P.BOOST_PERSON = False, False, False
    t0 = time.perf_counter()
    base = [P.detect(d["text"]) for d in docs]
    t1 = time.perf_counter()
    probs = [E.person_probs(d["text"]) for d in docs]
    t2 = time.perf_counter()
    n = len(docs)
    print(f"모델 기본 {1000 * (t1 - t0) / n:.1f} ms/문서, 앙상블 확률(TTA={E.TTA or '없음'}) 추가 {1000 * (t2 - t1) / n:.1f} ms/문서")

    rows = []
    for ts in SPAN_GRID:
        for tc in CAND_GRID:
            preds = [P._drop_overlaps(enhance(d["text"], E.ensemble_persons(d["text"], p, b, ts, tc)))
                     for d, p, b in zip(docs, probs, base)]
            lab, misses = score(docs, preds)
            c = lab["person"]
            rows.append((ts, tc, c["노출"], c["경계·라벨"], c["오탐"], dict(misses)))

    ref = next(r for r in rows if r[0] > 1 and r[1] > 1)   # 앙상블 끔 (규칙 보강만)
    print(f"\n기준(규칙 보강만): 이름 노출 {ref[2]}, 경계 {ref[3]}, 오탐 {ref[4]}\n")
    print("| TAU_SPAN | TAU_CAND | 노출 | 경계·라벨 | 오탐 | 미탐 유형 |")
    print("|---|---|---|---|---|---|")
    for ts, tc, ex, bd, fp, ms in sorted(rows, key=lambda r: (r[2], r[4])):
        f = lambda x: "끔" if x > 1 else x
        print(f"| {f(ts)} | {f(tc)} | {ex} | {bd} | {fp} | {ms} |")
    ok = [r for r in rows if r[4] <= ref[4] + a.max_fp_increase]
    best = min(ok, key=lambda r: (r[2], r[4]))
    print(f"\n추천 (오탐 +{a.max_fp_increase} 이내에서 노출 최소): PII_TAU_SPAN={best[0]} PII_TAU_CAND={best[1]} "
          f"→ 노출 {ref[2]}→{best[2]}, 오탐 {ref[4]}→{best[4]}")


if __name__ == "__main__":
    main()
