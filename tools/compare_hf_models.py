"""Hugging Face 한국어 개인정보·NER 모델을 기준 모델(KLUE-RoBERTa-small)과 같은 조건으로 비교

    pip install transformers torch              # CPU 버전이면 충분
    python -m tools.compare_hf_models --data data/pii_synth_finance_gen_hard_dev.jsonl
    python -m tools.compare_hf_models --data data/dev.jsonl --models koelectra-pii,e5-pii --threads 4

같은 데이터 · 같은 채점(eval_detect_boost.score) · CPU 처리 시간으로 다음을 비교합니다.
  A. 모델 단독     : 모델 라벨을 우리 8종으로 바꾼 결과만
  B. 우리 파이프라인 + 모델 (합집합) : 지금 detect() 결과에 모델이 찾은 것 중 겹치지 않는 것을 더함
     → "이 모델을 앙상블에 넣으면 노출이 얼마나 줄고 오탐이 얼마나 느나"
처음 보는 라벨은 '대응 안 된 라벨'로 출력하니, 보고서의 라벨 대응표를 만들 때 확인하세요.
⚠ 모델 선정·임계값 결정은 dev 로만. holdout 은 최종 확인에 한 번만.
"""
import argparse
import collections
import os
import json
import time

from starter import pii_pipeline as P
from starter import hybrid_detector as H
from starter.detect_boost import _resolve
from starter.hf_detector import HFDetector, decode, merge_adjacent  # noqa: F401  (공통 래퍼, 테스트용 재노출)
from tools.eval_detect_boost import score

# ---------------------------------------------------------------- 후보 모델 (이름 → HF id, 라벨 대응)
# 대응 값이 None 이면 버림(개인정보 아님 또는 우리 8종에 없음). 대응표에 없는 라벨은 '대응 안 됨'으로 집계.
MODELS = {
    # .env 의 PII_HYBRID_MODEL(학습한 모델 폴더)이 있으면 그 모델로 비교
    "koelectra-pii": (os.getenv("PII_HYBRID_MODEL", "atonlee/koelectra-ko-pii-ner"), {
        "NAME": "person", "PHONE": "phone_number", "EMAIL": "email", "ADDRESS": "address", "ZIPCODE": "address",
        "RRN": "resident_registration_number", "ALIEN_ID": "resident_registration_number",
        "CARD_NUMBER": "card_number", "ACCOUNT_NUMBER": "account_number", "ORGANIZATION": "organization",
        # 우리 8종에 없음
        "PASSPORT": None, "DRIVER_LICENSE": None, "IPIN": None, "CARD_EXPIRY": None, "CVC": None, "SECRET": None,
        "BIRTHDATE": None, "USER_ID": None, "GENERIC_ID": None, "VEHICLE_NUMBER": None, "IP_ADDRESS": None,
        "URL": None, "DEPARTMENT": None, "POSITION": None, "ATTRIBUTE": None, "PLACE": None, "DATE": None,
        "TIME": None, "QUANTITY": None}),
    "e5-pii": ("FrameByFrame/korean-pii-e5-base", {
        "PRIVATE_PERSON": "person", "PRIVATE_ADDRESS": "address", "PRIVATE_PHONE": "phone_number",
        "PRIVATE_EMAIL": "email", "ACCOUNT_NUMBER": "account_number",
        "PRIVATE_DATE": None, "PRIVATE_URL": None, "PERSONAL_HANDLE": None, "IP_ADDRESS": None}),
    "openmed-pii": ("OpenMed/OpenMed-PII-Korean-NomicMed-Large-395M-v1", {
        "FIRSTNAME": "person", "LASTNAME": "person", "MIDDLENAME": "person", "PHONE": "phone_number",
        "EMAIL": "email", "BANKACCOUNT": "account_number", "ACCOUNTNUMBER": "account_number",
        "CREDITCARD": "card_number", "SSN": "resident_registration_number", "ORGANIZATION": "organization",
        "STREET": "address", "CITY": "address", "STATE": "address", "COUNTY": "address", "ZIPCODE": "address",
        "BUILDINGNUMBER": "address", "SECONDARYADDRESS": "address"}),
}
KEEP = set(P.LABELS7)

# ---------------------------------------------------------------- 비교표
LABELS8 = ["person", "phone_number", "email", "address", "resident_registration_number",
           "account_number", "card_number", "organization"]


def row(name, docs, preds, ms):
    lab, misses = score(docs, preds)
    tot = sum(lab[l]["gold"] for l in LABELS8)
    ex = sum(lab[l]["노출"] for l in LABELS8)
    fp = sum(lab[l]["오탐"] for l in LABELS8)
    per = " | ".join(f"{lab[l]['노출']}/{lab[l]['gold']}" for l in LABELS8)
    print(f"| {name} | {ms:.1f} | {1 - ex / max(tot, 1):.3f} | {ex} | {lab['person']['경계·라벨']} | {fp} | {per} |")
    return misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--union-labels", default="all;person",
                    help="합집합에 더할 모델 라벨. ';' 로 여러 설정 비교 (예: 'all;person;person,phone_number')")
    ap.add_argument("--hybrid-tau", default="0.9,0.8,0.7", help="하이브리드 채택 확신도 임계값 후보 (근거 없는 후보용)")
    ap.add_argument("--hybrid-drop", action="store_true", help="'미동의 제거'(base 이름 검증) 설정도 비교")
    ap.add_argument("--review-out", help="첫 τ 의 검수 대기열을 jsonl 로 저장 (dev 에서만)")
    ap.add_argument("--threads", type=int, help="CPU 스레드 수 고정 (서버 사양에 맞춰 비교)")
    a = ap.parse_args()
    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]

    print("| 설정 | ms/문서 | 마스킹 재현율 | 노출 | 이름 경계·라벨 | 오탐 | "
          + " | ".join(f"{l} 노출" for l in LABELS8) + " |")
    print("|" + "---|" * (6 + len(LABELS8)))

    t0 = time.perf_counter()
    base = [P.detect(d["text"]) for d in docs]
    base_ms = (time.perf_counter() - t0) * 1000 / len(docs)
    miss_log = {"현재 파이프라인": row("현재 파이프라인 (KLUE-small+규칙+보강)", docs, base, base_ms)}

    notes = []
    for key in a.models.split(","):
        model_id, mapping = MODELS[key]
        try:
            det = HFDetector(model_id, mapping, a.threads)
        except Exception as e:   # 로드 실패(권한·custom code·ONNX 전용 등)는 건너뜀
            notes.append(f"{key}: 로드 실패 — {type(e).__name__}: {e}")
            continue
        det.predict(docs[0]["text"])          # 첫 실행 준비 시간 제외
        t0 = time.perf_counter()
        alone = [det.predict(d["text"]) for d in docs]
        ms = (time.perf_counter() - t0) * 1000 / len(docs)
        miss_log[key] = row(f"{key} 단독 ({det.n_params / 1e6:.0f}M)", docs, alone, ms)
        for labs in a.union_labels.split(";"):
            keep = set(labs.split(",")) if labs != "all" else KEEP
            union = [P._drop_overlaps(_resolve(b + [m for m in al if m["label"] in keep and not any(
                m["start"] < x["end"] and x["start"] < m["end"] for x in b)])) for b, al in zip(base, alone)]
            miss_log[f"{key} 합집합({labs})"] = row(f"현재 + {key} ({labs}만)", docs, union, base_ms + ms)
        if key == "koelectra-pii":       # 하이브리드: 넓게 잡고 근거·필터로 거름 (hybrid_detector.combine)
            kiwi = getattr(P, "_kiwi", None)
            for tau in [float(x) for x in a.hybrid_tau.split(",")]:
                for drop in ([False, True] if a.hybrid_drop else [False]):
                    t0 = time.perf_counter()
                    res = [H.combine(d["text"], b, al, kiwi=kiwi, tau_accept=tau, tau_review=0.5, drop_unagreed=drop)
                           for d, b, al in zip(docs, base, alone)]
                    hms = (time.perf_counter() - t0) * 1000 / len(docs)
                    tag = f"하이브리드 τ={tau}" + (" +미동의 제거" if drop else "")
                    miss_log[tag] = row(f"현재 + {key} ({tag})", docs, [r[0] for r in res], base_ms + ms + hms)
                    n_rev = sum(len(r[1]) for r in res)
                    notes.append(f"{tag}: 검수 대기열 {n_rev}건 (문서당 {n_rev / len(docs):.2f})")
                    if a.review_out and tau == float(a.hybrid_tau.split(",")[0]) and not drop:
                        with open(a.review_out, "w", encoding="utf-8") as f:
                            for d, (_, rv) in zip(docs, res):
                                for r in rv:
                                    t = d["text"]
                                    gold = any(r["start"] < g["end"] and g["start"] < r["end"] for g in d["entities"])
                                    f.write(json.dumps({"doc_id": d["id"], "text": t[r["start"]:r["end"]],
                                                        "context": t[max(0, r["start"] - 15):r["end"] + 15],
                                                        "정답과 겹침": gold, **r}, ensure_ascii=False) + "\n")
        fps = collections.Counter()
        for d, pr in zip(docs, alone):
            for m in pr:
                if not any(m["start"] < g["end"] and g["start"] < m["end"] for g in d["entities"]):
                    fps[(m["label"], d["text"][m["start"]:m["end"]])] += 1
        if fps:
            notes.append(f"{key} 단독 오탐 상위: " + ", ".join(f"{l}:'{w}'×{c}" for (l, w), c in fps.most_common(12)))
        if det.unmapped:
            notes.append(f"{key}: 대응 안 된 라벨 {dict(det.unmapped)} → MODELS 대응표에 추가 후 다시 실행")

    print("\n이름 미탐 유형")
    for k, v in miss_log.items():
        print(f"- {k}: {dict(v)}")
    for n in notes:
        print("※", n)


if __name__ == "__main__":
    main()
