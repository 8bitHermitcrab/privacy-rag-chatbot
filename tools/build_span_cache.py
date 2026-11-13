"""외부 탐지기 예측 파일 → 검색 비교용 위치 캐시 (starter/span_cache.py 입력)

    python tools/build_span_cache.py \
        --source data/holdout_labeled.jsonl data/holdout_retrieval_queries.jsonl data/queries30.jsonl \
        --pred outputs/pred_holdout_labeled_v6_mask.jsonl outputs/pred_holdout_retrieval_queries_v6_mask.jsonl \
               outputs/pred_queries30_v6_mask.jsonl \
        --out outputs/spans_v6.jsonl

- source: 원문 파일 (jsonl, "id"+"text" 또는 "qid"+"query")
- pred  : 예측 파일 (jsonl, "id" 또는 "qid" + "entities": [{"label","start","end"}]) — eval_predictions.py 형식과 같음
- id 로 원문과 예측을 이어 붙이고, 출력에는 원문 대신 sha256 만 씁니다.
- 라벨 이름이 달라도 NAME·PHONE 등은 팀 라벨(person, phone_number …)로 바꿉니다.
"""
import argparse
import hashlib
import json
import sys

LABELS7 = {"person", "phone_number", "email", "address", "resident_registration_number", "account_number", "card_number"}
ALIAS = {"NAME": "person", "PER": "person", "PERSON": "person", "PHONE": "phone_number", "TEL": "phone_number",
         "EMAIL": "email", "ADDRESS": "address", "ADDR": "address", "LOC_ADDRESS": "address",
         "RRN": "resident_registration_number", "SSN": "resident_registration_number",
         "ACCOUNT": "account_number", "ACCOUNT_NUMBER": "account_number", "BANK_ACCOUNT": "account_number",
         "CARD": "card_number", "CARD_NUMBER": "card_number", "CREDIT_CARD": "card_number"}


def rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def key(d):
    return str(d.get("id") if d.get("id") is not None else d.get("qid"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", nargs="+", required=True)
    ap.add_argument("--pred", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    texts = {}
    for p in a.source:
        for d in rows(p):
            t = d.get("text") if d.get("text") is not None else d.get("query")
            if t is not None:
                texts[key(d)] = t
    preds = {key(d): d for p in a.pred for d in rows(p)}

    missing = [k for k in texts if k not in preds]
    if missing:
        sys.exit(f"예측이 없는 원문 {len(missing)}개 (예: {missing[:5]}). 외부 탐지기로 이 파일들도 돌렸는지 확인하세요.")
    unknown, n_ent = set(), 0
    with open(a.out, "w", encoding="utf-8") as f:
        seen = set()
        for k, t in texts.items():
            ents = []
            for e in preds[k].get("entities", []):
                lab = ALIAS.get(e["label"], ALIAS.get(str(e["label"]).upper(), e["label"]))
                if lab not in LABELS7:
                    unknown.add(e["label"])
                    continue
                s, en = int(e["start"]), int(e["end"])
                if 0 <= s < en <= len(t):
                    ents.append({"label": lab, "start": s, "end": en})
            h = hashlib.sha256(t.encode("utf-8")).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            n_ent += len(ents)
            f.write(json.dumps({"sha": h, "entities": ents}, ensure_ascii=False) + "\n")
    print(f"원문 {len(texts)}개 (고유 {len(seen)}) · 위치 {n_ent}개 → {a.out}")
    if unknown:
        print(f"⚠ 팀 라벨이 아니라 버린 라벨: {sorted(unknown)}")


if __name__ == "__main__":
    main()
