"""탐지기 결과 파일 비교 채점 — 어떤 탐지기든 같은 정답·같은 채점 기준으로 비교

1) 각 탐지기가 데이터 파일마다 예측 파일을 만든다 (공통 형식, 한 줄에 문서 하나, 원문 기준 글자 위치):
    {"id": "D0001", "entities": [{"label": "person", "start": 10, "end": 13}, ...]}
   - id 는 정답 파일의 id 그대로, start/end 는 '가명처리 전 원문' 기준 (end 는 포함하지 않음)
   - label 은 우리 7종 (person, phone_number, email, address, resident_registration_number,
     account_number, card_number). NAME·PHONE 같은 다른 이름은 아래 ALIAS 로 자동 변환
   - token 필드가 있으면 그대로 두어도 됨 (ingest 의 outputs/pseudo_*.jsonl 형식과 같음)
   - 처리 시간을 비교하려면 문서마다 "ms": 12.3 을 넣거나 --ms 이름=값 으로 전달

2) 채점 (여러 탐지기를 한 표로):
    python -m tools.eval_predictions --gold data/pii_synth_finance_test.jsonl \\
        --pred 하이브리드=outputs/pred_test_hybrid.jsonl 팀원=outputs/pred_test_teammate.jsonl

   우리 탐지기 예측 파일 만들기 (현재 .env 설정으로 detect() 실행):
    python -m tools.eval_predictions --gold data/pii_synth_finance_test.jsonl --dump-ours outputs/pred_test_hybrid.jsonl

지표 (모두 정답 7종 기준)
  마스킹 재현율 : 정답 개인정보 중 예측과 조금이라도 겹친 비율 (1 - 노출/정답)
  노출          : 예측과 전혀 겹치지 않은 정답 = 원문 그대로 외부로 나가는 개수  ← 가장 중요
  글자 노출률   : 정답 글자 중 가려지지 않은 글자 비율 (경계가 짧아 일부만 가린 경우까지 반영)
  정확 일치     : 위치와 라벨이 정답과 똑같은 비율
  경계·라벨     : 겹치긴 했지만 위치나 라벨이 다른 개수
  오탐          : 어떤 정답과도 겹치지 않은 예측 개수 (문서당 평균도 표시)
"""
import argparse
import collections
import json
import time

from starter import pii_pipeline as P

LABELS = list(P.LABELS7)
ALIAS = {"NAME": "person", "PER": "person", "PERSON": "person", "PHONE": "phone_number", "TEL": "phone_number",
         "EMAIL": "email", "ADDRESS": "address", "ADDR": "address", "LOC_ADDRESS": "address",
         "RRN": "resident_registration_number", "SSN": "resident_registration_number",
         "ACCOUNT": "account_number", "ACCOUNT_NUMBER": "account_number", "BANK_ACCOUNT": "account_number",
         "CARD": "card_number", "CARD_NUMBER": "card_number", "CREDIT_CARD": "card_number"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def norm_label(l):
    return l if l in LABELS else ALIAS.get(str(l).upper().removeprefix("B-").removeprefix("I-"))


def score(golds, preds):
    """golds: 정답 문서 목록, preds: id → 예측 목록 → 지표 dict"""
    c = collections.Counter()
    per = collections.defaultdict(collections.Counter)
    for d in golds:
        text = d["text"]
        gs = [g for g in d["entities"] if g["label"] in LABELS]
        ps = [p for p in preds.get(d["id"], []) if p["label"] in LABELS]
        covered = [False] * len(text)
        for p in ps:
            for i in range(max(p["start"], 0), min(p["end"], len(text))):
                covered[i] = True
        for g in gs:
            c["gold"] += 1
            per[g["label"]]["gold"] += 1
            c["gold_chars"] += g["end"] - g["start"]
            c["leak_chars"] += sum(1 for i in range(g["start"], g["end"]) if not covered[i])
            hit = [p for p in ps if p["start"] < g["end"] and g["start"] < p["end"]]
            if not hit:
                c["노출"] += 1
                per[g["label"]]["노출"] += 1
            elif any(p["start"] == g["start"] and p["end"] == g["end"] and p["label"] == g["label"] for p in hit):
                c["정확"] += 1
            else:
                c["경계·라벨"] += 1
        for p in ps:
            if not any(p["start"] < g["end"] and g["start"] < p["end"] for g in gs):
                c["오탐"] += 1
    n = max(len(golds), 1)
    return {"재현율": 1 - c["노출"] / max(c["gold"], 1), "노출": c["노출"], "정답": c["gold"],
            "글자노출률": c["leak_chars"] / max(c["gold_chars"], 1), "정확": c["정확"] / max(c["gold"], 1),
            "경계": c["경계·라벨"], "오탐": c["오탐"], "오탐/문서": c["오탐"] / n,
            "라벨별": {l: f"{per[l]['노출']}/{per[l]['gold']}" for l in LABELS if per[l]["gold"]}}


def read_pred(path):
    preds, ms, bad = {}, [], collections.Counter()
    for d in load(path):
        ents = []
        for e in d.get("entities", []):
            lab = norm_label(e.get("label"))
            if lab is None:
                bad[e.get("label")] += 1
                continue
            ents.append({"label": lab, "start": int(e["start"]), "end": int(e["end"])})
        preds[d["id"]] = ents
        if "ms" in d:
            ms.append(float(d["ms"]))
    return preds, (sum(ms) / len(ms) if ms else None), bad


def dump_ours(golds, path):
    t0 = time.perf_counter()
    with open(path, "w", encoding="utf-8") as f:
        for d in golds:
            s = time.perf_counter()
            sp = P.detect(d["text"])
            f.write(json.dumps({"id": d["id"], "ms": round(1000 * (time.perf_counter() - s), 1),
                                "entities": [{k: x[k] for k in ("label", "start", "end")} for x in sp]},
                               ensure_ascii=False) + "\n")
    print(f"저장: {path}  ({1000 * (time.perf_counter() - t0) / max(len(golds), 1):.1f} ms/문서)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, help="정답 jsonl (text + entities)")
    ap.add_argument("--pred", nargs="*", default=[], help="이름=예측파일 (여러 개)")
    ap.add_argument("--ms", nargs="*", default=[], help="이름=ms/문서 (예측 파일에 ms 가 없을 때)")
    ap.add_argument("--dump-ours", help="현재 .env 설정의 우리 탐지기로 예측 파일을 만들 경로")
    a = ap.parse_args()
    golds = load(a.gold)
    if a.dump_ours:
        dump_ours(golds, a.dump_ours)
        if not a.pred:
            return
    ms_arg = dict(x.split("=", 1) for x in a.ms)
    rows = []
    for item in a.pred:
        name, path = item.split("=", 1)
        preds, ms, bad = read_pred(path)
        missing = [d["id"] for d in golds if d["id"] not in preds]
        if missing:
            print(f"⚠ {name}: 예측 파일에 없는 문서 {len(missing)}건 (예측 없음으로 채점) 예: {missing[:3]}")
        if bad:
            print(f"⚠ {name}: 7종으로 바꾸지 못한 라벨 (채점 제외): {dict(bad)}")
        r = score(golds, preds)
        r["name"], r["ms"] = name, ms if ms is not None else (float(ms_arg[name]) if name in ms_arg else None)
        rows.append(r)

    print(f"\n## {a.gold} — 문서 {len(golds)}건, 정답 개인정보 {rows[0]['정답'] if rows else '-'}개")
    print("| 탐지기 | 마스킹 재현율 | 노출 | 글자 노출률 | 정확 일치 | 경계·라벨 | 오탐 | 오탐/문서 | ms/문서 | 라벨별 노출 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        ms = f"{r['ms']:.1f}" if r["ms"] is not None else "-"
        leak = {k: v for k, v in r["라벨별"].items() if not v.startswith("0/")}
        print(f"| {r['name']} | {r['재현율']:.3f} | {r['노출']} | {r['글자노출률']:.1%} | {r['정확']:.3f} | {r['경계']} | "
              f"{r['오탐']} | {r['오탐/문서']:.2f} | {ms} | {leak or '-'} |")


if __name__ == "__main__":
    main()
