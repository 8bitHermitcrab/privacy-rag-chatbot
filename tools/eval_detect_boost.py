"""탐지 보강(detect_boost) 전후 비교 채점

    # 모델 포함 (Colab, models/roberta_small_v1 필요)
    python -m tools.eval_detect_boost --data data/dev.jsonl
    # 모델 없이 규칙 탐지기만 바탕으로
    python -m tools.eval_detect_boost --data data/dev.jsonl --no-model

같은 문서에 대해 보강 끄기 / 주소만 / 이름만 / 둘 다 를 차례로 돌려 표로 비교합니다.
  - 노출(미탐) : 정답 구간과 전혀 겹치지 않음 = 원문이 그대로 남음 (주 지표)
  - 경계 오류  : 겹치지만 시작·끝이 다름
  - 오탐       : 어떤 정답과도 겹치지 않는 예측
  - 이름 미탐 유형: 외자 / 띄어 쓴 외자 / 세 글자 / 성 없이 이름만 / 그 외
⚠ 규칙을 고칠 때는 dev 로만 돌리세요. holdout 은 최종 확인용으로 한 번만.
"""
import argparse
import collections
import json
import time

from starter import pii_pipeline as P

# (이름, 주소 보강, 이름 보강, 모델 앙상블)
VARIANTS = [("보강 없음", False, False, False), ("규칙 보강", True, True, False),
            ("앙상블만", False, False, True), ("규칙 보강 + 앙상블", True, True, True)]


def miss_type(g):
    t = g["text"]
    core = t.replace(" ", "")
    if g.get("form") == "given":
        return "성 없이 이름만"
    if " " in t:
        return "띄어 쓴 외자" if len(core) == 2 else "띄어 쓴 이름"
    if len(core) == 2:
        return "외자"
    if len(core) == 3:
        return "세 글자"
    return "그 외"


def score(docs, preds):
    lab = collections.defaultdict(lambda: collections.Counter())
    misses = collections.Counter()
    for d, pr in zip(docs, preds):
        golds = [g for g in d["entities"] if g["label"] in P.LABELS7]
        for g in golds:
            c = lab[g["label"]]
            c["gold"] += 1
            hit = [p for p in pr if p["start"] < g["end"] and g["start"] < p["end"]]
            if not hit:
                c["노출"] += 1
                if g["label"] == "person":
                    misses[miss_type(g)] += 1
            elif any(p["start"] == g["start"] and p["end"] == g["end"] and p["label"] == g["label"] for p in hit):
                c["정확"] += 1
            else:
                c["경계·라벨"] += 1
        for p in pr:
            if not any(p["start"] < g["end"] and g["start"] < p["end"] for g in golds):
                lab[p["label"]]["오탐"] += 1
    return lab, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--labels", default="person,address")
    ap.add_argument("--dump", help="오류 사례 CSV 경로 (예: outputs/errors_dev.csv)")
    a = ap.parse_args()
    docs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    show = a.labels.split(",")

    variants = VARIANTS if not a.no_model else [v for v in VARIANTS if not v[3]]
    for name, addr, pers, ens in variants:
        P.BOOST_ADDRESS, P.BOOST_PERSON, P.ENSEMBLE = addr, pers, ens
        t0 = time.perf_counter()
        preds = [P.detect(d["text"], use_model=not a.no_model) for d in docs]
        ms = (time.perf_counter() - t0) * 1000 / len(docs)
        lab, misses = score(docs, preds)
        tot = sum(c["gold"] for c in lab.values())
        exposed = sum(c["노출"] for c in lab.values())
        print(f"\n## {name}  ({ms:.1f} ms/문서)  전체 노출 {exposed}/{tot}  오탐 {sum(c['오탐'] for c in lab.values())}")
        print("| 라벨 | 정답 | 정확 | 경계·라벨 | 노출 | 오탐 |")
        print("|---|---|---|---|---|---|")
        for l in show:
            c = lab[l]
            print(f"| {l} | {c['gold']} | {c['정확']} | {c['경계·라벨']} | {c['노출']} | {c['오탐']} |")
        print("이름 미탐 유형:", dict(misses) or "없음")
    if a.dump:
        dump(docs, preds, a.dump)
        print(f"\n마지막 설정의 이름·주소 오류 사례: {a.dump}")


def dump(docs, preds, path):
    """dev 오류 분석용: 노출·경계 오류·오탐을 앞뒤 문맥과 함께 CSV 로 (holdout 에는 쓰지 말 것)"""
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "종류", "라벨", "정답", "예측", "유형", "문맥"])
        for d, pr in zip(docs, preds):
            t = d["text"]
            golds = [g for g in d["entities"] if g["label"] in ("person", "address")]
            ctx = lambda s, e: t[max(0, s - 15):s] + "【" + t[s:e] + "】" + t[e:e + 15]
            for g in golds:
                hit = [p for p in pr if p["start"] < g["end"] and g["start"] < p["end"]]
                if not hit:
                    w.writerow([d["id"], "노출", g["label"], g["text"], "", miss_type(g) if g["label"] == "person" else "",
                                ctx(g["start"], g["end"]).replace("\n", "⏎")])
                elif not any(p["start"] == g["start"] and p["end"] == g["end"] for p in hit):
                    p = hit[0]
                    kind = "넓게" if p["start"] <= g["start"] and p["end"] >= g["end"] else \
                           "좁게" if p["start"] >= g["start"] and p["end"] <= g["end"] else "어긋남"
                    w.writerow([d["id"], "경계·라벨", g["label"], g["text"], t[p["start"]:p["end"]],
                                kind + ("" if p["label"] == g["label"] else f"/라벨:{p['label']}"),
                                ctx(min(p["start"], g["start"]), max(p["end"], g["end"])).replace("\n", "⏎")])
            for p in pr:
                if p["label"] in ("person", "address") and not any(
                        p["start"] < g["end"] and g["start"] < p["end"] for g in d["entities"]):
                    w.writerow([d["id"], "오탐", p["label"], "", t[p["start"]:p["end"]], "",
                                ctx(p["start"], p["end"]).replace("\n", "⏎")])


if __name__ == "__main__":
    main()
