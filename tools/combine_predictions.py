"""두 탐지기 예측 파일 합치기 → 통합 방식별로 같은 채점(eval_predictions)·검색 비교(build_span_cache)에 넣기

    python -m tools.combine_predictions --source data/blind/blind_eval.jsonl \
        --a outputs/pred_blind_hyb_v1.jsonl --b outputs/pred_blind_eval_v6_mask.jsonl \
        --out-prefix outputs/pred_blind_comb

만드는 파일 (형식은 eval_predictions.py 와 같음, 원문 없음)
  <prefix>_union.jsonl  합집합  — 둘 중 하나라도 잡으면 가림. 겹치면 넓은 범위, 라벨은 A 우선
  <prefix>_afill.jsonl  A 기본 + B 보충 — A 결과 그대로 + A와 겹치지 않는 B 구간만 추가
  <prefix>_bfill.jsonl  B 기본 + A 보충 — 반대
  <prefix>_bfill_alab.jsonl  B 경계 + A 라벨 + 서로 보충 — B 구간과 겹치는 A 구간이 있으면 라벨만 A 것으로
  <prefix>_agree.jsonl  교집합  — 둘 다 잡은 곳만 (참고용: 통합하면 안 되는 방식의 하한선)
옵션
  --team-filter : 결과에 팀 기준 필터(hybrid_detector.post_filter: 성+직급·성만·일반어 제거) 적용
화면 출력: 문서당 '불일치 구간'(한쪽만 잡은 구간) 수 = 사람이 검수할 양의 추정치
"""
import argparse
import collections
import json

LABELS7 = {"person", "phone_number", "email", "address", "resident_registration_number", "account_number", "card_number"}
ALIAS = {"NAME": "person", "PER": "person", "PERSON": "person", "PHONE": "phone_number", "TEL": "phone_number",
         "EMAIL": "email", "ADDRESS": "address", "ADDR": "address", "RRN": "resident_registration_number",
         "ACCOUNT": "account_number", "CARD": "card_number"}


def rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def key(d):
    return str(d["id"] if d.get("id") is not None else d.get("qid"))


def load_pred(path):
    out = {}
    for d in rows(path):
        ents = []
        for e in d.get("entities", []):
            lab = e["label"] if e["label"] in LABELS7 else ALIAS.get(str(e["label"]).upper())
            if lab:
                ents.append({"label": lab, "start": int(e["start"]), "end": int(e["end"])})
        out[key(d)] = (ents, d.get("ms"))
    return out


def ov(x, y):
    return x["start"] < y["end"] and y["start"] < x["end"]


def union(a, b):
    """겹치는 구간끼리 묶어 가장 넓은 범위로. 라벨은 묶음 안의 A 라벨(없으면 B) 중 가장 긴 구간의 것"""
    items = sorted([(s, 0) for s in a] + [(s, 1) for s in b], key=lambda t: (t[0]["start"], -t[0]["end"]))
    groups = []
    for s, src in items:
        if groups and s["start"] < groups[-1]["end"]:
            g = groups[-1]
            g["end"] = max(g["end"], s["end"])
            g["members"].append((s, src))
        else:
            groups.append({"start": s["start"], "end": s["end"], "members": [(s, src)]})
    out = []
    for g in groups:
        pick = [m for m, src in g["members"] if src == 0] or [m for m, _ in g["members"]]
        lab = max(pick, key=lambda m: m["end"] - m["start"])["label"]
        out.append({"label": lab, "start": g["start"], "end": g["end"]})
    return out


def fill(base, extra):
    return sorted(base + [e for e in extra if not any(ov(e, x) for x in base)], key=lambda s: s["start"])


def relabel(base, ref):
    """base 구간은 그대로, 겹치는 ref 구간이 있으면 라벨만 ref 의 것(가장 많이 겹친 것)으로"""
    out = []
    for x in base:
        hits = [y for y in ref if ov(x, y)]
        if hits:
            y = max(hits, key=lambda y: min(x["end"], y["end"]) - max(x["start"], y["start"]))
            x = {**x, "label": y["label"]}
        out.append(x)
    return out


def agree(a, b):
    return [x for x in a if any(ov(x, y) for y in b)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", nargs="+", required=True, help="원문 파일 (id+text 또는 qid+query)")
    ap.add_argument("--a", required=True, help="탐지기 A 예측 (예: 하이브리드)")
    ap.add_argument("--b", required=True, help="탐지기 B 예측 (예: V6 mask)")
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--team-filter", action="store_true")
    a = ap.parse_args()

    texts = {}
    for p in a.source:
        for d in rows(p):
            texts[key(d)] = d["text"] if "text" in d else d["query"]
    A, B = load_pred(a.a), load_pred(a.b)
    miss = [k for k in texts if k not in A or k not in B]
    if miss:
        raise SystemExit(f"두 예측 파일 중 하나에 없는 문서 {len(miss)}개 (예: {miss[:5]}) — 같은 원문으로 다시 만드세요")

    post_filter = None
    if a.team_filter:
        from starter.hybrid_detector import post_filter
    ways = {"union": union, "afill": fill, "bfill": lambda x, y: fill(y, x),
            "bfill_alab": lambda x, y: fill(relabel(y, x), x), "agree": agree}
    files = {w: open(f"{a.out_prefix}_{w}.jsonl", "w", encoding="utf-8") for w in ways}
    only_a = only_b = 0
    for k, text in texts.items():
        (ea, ma), (eb, mb) = A[k], B[k]
        only_a += sum(not any(ov(x, y) for y in eb) for x in ea)
        only_b += sum(not any(ov(y, x) for x in ea) for y in eb)
        ms = round(ma + mb, 1) if ma is not None and mb is not None else None
        for w, fn in ways.items():
            sp = fn(ea, eb)
            if post_filter:
                sp, _ = post_filter(text, sp)
            row = {"id": k, "entities": sp} | ({"ms": ms} if ms is not None else {})
            files[w].write(json.dumps(row, ensure_ascii=False) + "\n")
    for f in files.values():
        f.close()
    n = max(len(texts), 1)
    print(f"문서 {len(texts)}개 → {a.out_prefix}_{{union,afill,bfill,bfill_alab,agree}}.jsonl"
          f"{' (팀 기준 필터 적용)' if post_filter else ''}")
    print(f"불일치 구간: A만 {only_a}개, B만 {only_b}개 → 문서당 {(only_a + only_b) / n:.2f}개 (검수 대상 추정)")


if __name__ == "__main__":
    main()
