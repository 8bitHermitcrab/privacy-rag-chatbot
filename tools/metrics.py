"""검색 지표 계산 (make_deliverables.py, eval_queries.py 공용)
공식 채점 스크립트 eval/eval_holdout_retrieval.py 와 같은 정의:

- 순위에서 neutral_doc_ids(정답도 혼동도 아닌 문서)를 먼저 빼고 계산
Hit@k     : 상위 k개에 정답이 하나라도 있는 질의 비율
Recall@k  : 정답 문서 중 상위 k개에 들어온 비율의 평균
MRR       : 첫 정답 순위의 역수 평균 (없으면 0)
Confuse@10: 혼동 문서가 정의된 질의만, 상위 10개 중 혼동 문서가 차지한 비율의 평균 (낮을수록 좋음)
"""
import collections
import json


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def score_one(ranked, rel, conf, neutral=()):
    ranked = [d for d in ranked if d not in set(neutral)]
    top5, top10 = set(ranked[:5]), set(ranked[:10])
    rr = next((1 / (i + 1) for i, d in enumerate(ranked) if d in rel), 0.0)
    first = next((i + 1 for i, d in enumerate(ranked) if d in rel), None)
    return {
        "hit1": float(bool(ranked) and ranked[0] in rel),
        "hit5": float(bool(rel & top5)),
        "r5": len(rel & top5) / len(rel) if rel else 0.0,
        "r10": len(rel & top10) / len(rel) if rel else 0.0,
        "mrr": rr,
        "conf": (len(conf & top10) / max(len(ranked[:10]), 1) if conf else None),
        "first": first,
    }


def aggregate(rows):
    n = len(rows)
    if not n:
        return {}
    out = {k: sum(r[k] for r in rows) / n for k in ("hit1", "hit5", "r5", "r10", "mrr")}
    c = [r["conf"] for r in rows if r["conf"] is not None]
    out["conf"] = sum(c) / len(c) if c else None
    out["n"] = n
    return out


def evaluate(runs, gold):
    """runs: [{qid, ranked_doc_ids}], gold: {qid: {type, relevant_doc_ids, confusable_doc_ids}}"""
    per_q, by_type = [], collections.defaultdict(list)
    for r in runs:
        g = gold.get(r["qid"])
        if not g:
            continue
        s = score_one(r["ranked_doc_ids"], set(g["relevant_doc_ids"]), set(g.get("confusable_doc_ids", [])),
                      g.get("neutral_doc_ids", []))
        s.update(qid=r["qid"], type=g.get("type", "-"), n_rel=len(g["relevant_doc_ids"]),
                 n_tokens=len(r.get("query_tokens") or []))
        per_q.append(s)
        by_type[s["type"]].append(s)
    return aggregate(per_q), {t: aggregate(v) for t, v in sorted(by_type.items())}, per_q


def fmt(v):
    return "—" if v is None else f"{v:.3f}"


def table(rows, first_col):
    """rows: [(name, agg)] → markdown 표"""
    lines = [f"| {first_col} | n | Hit@1 | Hit@5 | R@5 | R@10 | MRR | Confuse@10 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, a in rows:
        lines.append(f"| {name} | {a['n']} | {fmt(a['hit1'])} | {fmt(a['hit5'])} | {fmt(a['r5'])} | "
                     f"{fmt(a['r10'])} | {fmt(a['mrr'])} | {fmt(a['conf'])} |")
    return "\n".join(lines)
