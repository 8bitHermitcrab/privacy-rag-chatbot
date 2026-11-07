"""하이브리드 설정 고르기 — 규칙은 그대로 두고 '비율'(임계값·옵션)만 조절

    # 1) 고르기: hard_dev(개선용)에서 τ·옵션 조합 비교 → 추천 설정
    python -m tools.tune_hybrid --data data/pii_synth_finance_gen_hard_dev.jsonl
    # 2) 확인: 고른 설정이 다른 데이터에서도 통하는지 (고르는 데 쓰지 않은 데이터로)
    python -m tools.tune_hybrid --data data/pii_synth_finance_gen_hard_dev.jsonl --confirm data/holdout_labeled.jsonl

모델은 문서마다 한 번씩만 돌리고(RoBERTa 파이프라인 + koelectra), 조합만 바꿔 다시 채점하므로 빠릅니다.
조절하는 것
  - TAU_ACCEPT : 근거 없는 koelectra 이름 후보를 받아들이는 확신도 기준 (낮출수록 미탐↓ 오탐↑)
  - DROP_UNAGREED : RoBERTa 에만 있고 koelectra 가 동의하지 않는 이름을 버릴지 (켜면 오탐↓ 미탐 위험↑)
  - NUM_UNION : koelectra 의 번호류(전화·이메일·주민·계좌·카드)도 합칠지 (겹치지 않는 것만, 형식 검증 통과 시)
추천 기준 : 오탐 증가가 --max-fp-increase 이내인 조합 중 '전체 노출'이 가장 적은 것 (같으면 오탐 적은 것)
"""
import argparse
import json
import time

from starter import hybrid_detector as H
from starter import pii_pipeline as P
from tools.eval_detect_boost import score

LABELS = list(P.LABELS7)
TAUS = [0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]

def run_all(docs):
    """문서마다 base(하이브리드 끈 파이프라인)·koelectra 결과를 한 번만 계산"""
    old = P.HYBRID
    P.HYBRID = False
    t0 = time.perf_counter()
    base = [P.detect(d["text"]) for d in docs]
    t1 = time.perf_counter()
    det = H.koelectra()
    det.predict(docs[0]["text"])
    t2 = time.perf_counter()
    ks = [det.predict(d["text"]) for d in docs]
    t3 = time.perf_counter()
    P.HYBRID = old
    n = len(docs)
    return base, ks, 1000 * (t1 - t0) / n, 1000 * (t3 - t2) / n


def evaluate(docs, base, ks, tau, drop, nums, filt="strict"):
    kiwi = getattr(P, "_kiwi", None)
    preds, n_rev = [], 0
    for d, b, k in zip(docs, base, ks):
        sp, rev = H.combine(d["text"], b, k, kiwi=kiwi, tau_accept=tau, tau_review=min(tau, H.TAU_REVIEW),
                            drop_unagreed=drop, num_union=nums, name_filter=filt)
        preds.append(sp)
        n_rev += len(rev)
    lab, misses = score(docs, preds)
    ex = sum(lab[l]["노출"] for l in LABELS)
    fp = sum(lab[l]["오탐"] for l in LABELS)
    gold = sum(lab[l]["gold"] for l in LABELS)
    return {"tau": tau, "drop": drop, "nums": nums, "filt": filt, "노출": ex, "이름노출": lab["person"]["노출"],
            "이름경계": lab["person"]["경계·라벨"], "오탐": fp, "검수": n_rev,
            "재현율": 1 - ex / max(gold, 1), "라벨별": {l: lab[l]["노출"] for l in LABELS if lab[l]["노출"]},
            "미탐유형": dict(misses)}


def table(rows, title):
    print(f"\n## {title}")
    print("| τ | 미동의 제거 | 번호 합치기 | 이름 필터 | 마스킹 재현율 | 노출 | 이름 노출 | 이름 경계 | 오탐 | 검수 | 라벨별 노출 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['tau']} | {'켬' if r['drop'] else '끔'} | {'켬' if r['nums'] else '끔'} | {r['filt']} | {r['재현율']:.3f} | "
              f"{r['노출']} | {r['이름노출']} | {r['이름경계']} | {r['오탐']} | {r['검수']} | {r['라벨별'] or '-'} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="설정을 고를 데이터 (개선용)")
    ap.add_argument("--confirm", help="고른 설정을 확인할 다른 데이터")
    ap.add_argument("--max-fp-increase", type=int, default=30, help="지금 설정(τ=0.9) 대비 허용할 오탐 증가")
    a = ap.parse_args()

    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    base, ks, ms_b, ms_k = run_all(docs)
    print(f"처리 시간: 파이프라인 {ms_b:.1f} ms/문서 + koelectra {ms_k:.1f} ms/문서")
    rows = [evaluate(docs, base, ks, t, drop, nums, filt) for t in TAUS for drop in (False, True) for nums in (False, True)
            for filt in ("strict", "loose", "none")]
    ref = next(r for r in rows if r["tau"] == 0.9 and r["drop"] and not r["nums"] and r["filt"] == "strict")   # 기존 설정
    rows.sort(key=lambda r: (r["노출"], r["오탐"]))
    table(rows[:15], f"{a.data} — 노출 적은 순 상위 15개 (지금 설정: 노출 {ref['노출']}, 오탐 {ref['오탐']})")
    ok = [r for r in rows if r["오탐"] <= ref["오탐"] + a.max_fp_increase]
    best = min(ok, key=lambda r: (r["노출"], r["오탐"]))
    print(f"\n추천 (오탐 +{a.max_fp_increase} 이내에서 노출 최소): τ={best['tau']}, 미동의 제거 {'켬' if best['drop'] else '끔'}, "
          f"번호 합치기 {'켬' if best['nums'] else '끔'}, 이름 필터 {best['filt']} → 노출 {ref['노출']}→{best['노출']}, "
          f"오탐 {ref['오탐']}→{best['오탐']}")
    print("  남은 이름 미탐 유형:", best["미탐유형"])
    print("\n.env 에 적을 값:")
    print(f"  PII_HYBRID_TAU_ACCEPT={best['tau']}\n  PII_HYBRID_DROP_UNAGREED={int(best['drop'])}\n"
          f"  PII_HYBRID_NUM_UNION={int(best['nums'])}\n  PII_HYBRID_NAME_FILTER={best['filt']}")

    if a.confirm:
        cdocs = [json.loads(l) for l in open(a.confirm, encoding="utf-8") if l.strip()]
        cb, ck, _, _ = run_all(cdocs)
        cref = evaluate(cdocs, cb, ck, 0.9, True, False, "strict")
        cbest = evaluate(cdocs, cb, ck, best["tau"], best["drop"], best["nums"], best["filt"])
        table([cref, cbest], f"{a.confirm} 에서 확인 (위: 지금 설정, 아래: 추천 설정)")


if __name__ == "__main__":
    main()
