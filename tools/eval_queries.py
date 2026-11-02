"""팀이 정한 검색 질의(30개)의 평가 결과 만들기 → deliverables/queries30_eval_<mode>.md

    python tools/eval_queries.py --queries data/queries30.csv               # SEARCH_MODE(기본 v3)
    python tools/eval_queries.py --queries data/queries30.jsonl --mode v1   # 비교용

입력 파일 (CSV 또는 JSONL, 한 줄에 질의 하나)
  CSV  : qid,query,type,relevant_doc_ids,confusable_doc_ids   (문서 ID 여러 개는 ; 로 구분, type·confusable 은 선택)
  JSONL: {"qid", "query", "type"?, "relevant_doc_ids": [...], "confusable_doc_ids"?: [...]}
- 검색은 starter.search.search 그대로 (질의 가명처리 → 임베딩 API → v3 검색). 처음 보는 질의는 임베딩 API를 부름
- 결과: deliverables/run_queries30_<mode>.jsonl (질의별 순위), deliverables/queries30_eval_<mode>.md
- 보고서에는 질의 원문 대신 가명처리된 질의만 씀 (질의 쪽 미탐이면 원문이 남을 수 있으니 확인)
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics import evaluate, load_jsonl, table  # noqa: E402


def _ids(cell):
    if isinstance(cell, list):
        return cell
    return [x.strip() for x in (cell or "").replace(",", ";").split(";") if x.strip()]


def load_queries(path):
    if path.endswith(".csv"):
        import csv
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        rows = load_jsonl(path)
    out = []
    for r in rows:
        if not (r.get("qid") and r.get("query")):
            continue
        out.append({"qid": str(r["qid"]).strip(), "query": str(r["query"]).strip(), "type": (r.get("type") or "-").strip(),
                    "relevant_doc_ids": _ids(r.get("relevant_doc_ids")), "confusable_doc_ids": _ids(r.get("confusable_doc_ids")),
                    "neutral_doc_ids": _ids(r.get("neutral_doc_ids"))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="data/queries30.jsonl")
    ap.add_argument("--mode", default=os.getenv("SEARCH_MODE", "v3"))
    ap.add_argument("-k", type=int, default=10)
    a = ap.parse_args()

    from starter import db
    from starter.search import search

    qs = load_queries(os.path.join(ROOT, a.queries))
    no_gold = [q["qid"] for q in qs if not q["relevant_doc_ids"]]
    if no_gold:
        sys.exit(f"정답 문서(relevant_doc_ids)가 없는 질의: {', '.join(no_gold)}")
    dup = sorted({q["qid"] for q in qs if sum(x["qid"] == q["qid"] for x in qs) > 1})
    if dup:
        sys.exit(f"qid 중복: {', '.join(dup)}")
    docs_path = os.path.join(ROOT, "data", "holdout_labeled.jsonl")
    if os.path.exists(docs_path):
        known = {d.get("id") for d in load_jsonl(docs_path)}
        bad = [f"{q['qid']}:{d}" for q in qs for d in q["relevant_doc_ids"] + q["confusable_doc_ids"] if d not in known]
        if bad:
            sys.exit(f"DB에 없는 문서 ID: {', '.join(bad)}")
    out_dir = os.path.join(ROOT, "deliverables")
    os.makedirs(out_dir, exist_ok=True)
    run_path = os.path.join(out_dir, f"run_queries30_{a.mode}.jsonl")
    runs, t0 = [], time.time()
    conn = db.rag_conn()
    try:
        with open(run_path, "w", encoding="utf-8") as f:
            for i, q in enumerate(qs, 1):
                masked, toks, docs = search(q["query"], a.k, conn=conn, mode=a.mode)
                r = {"qid": q["qid"], "masked_query": masked, "query_tokens": toks,
                     "ranked_doc_ids": [d["doc_id"] for d in docs]}
                runs.append(r)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                print(f"  {i}/{len(qs)} {q['qid']}", end="\r")
    finally:
        conn.close()
    sec = (time.time() - t0) / max(len(qs), 1)

    gold = {q["qid"]: q for q in qs}
    overall, by_type, per_q = evaluate(runs, gold)
    masked = {r["qid"]: r for r in runs}
    L = [f"# 검색 질의 {len(qs)}개 평가 결과 — {a.mode}", "",
         f"질의 파일: `{a.queries}` · 검색 상위 {a.k}개 · 질의당 {sec * 1000:.0f} ms", "",
         "## 전체", "", table([(a.mode, overall)], "모드"), "",
         "## 유형별", "", table(list(by_type.items()), "유형"), "",
         "## 질의별", "",
         "| qid | 유형 | 가명처리된 질의 | 질의 토큰 | 정답 수 | 첫 정답 순위 | Hit@5 |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    for s in sorted(per_q, key=lambda s: s["qid"]):
        m = masked[s["qid"]]
        L.append(f"| {s['qid']} | {s['type']} | {m['masked_query'].replace('|', '/')} | {len(m['query_tokens'])} | "
                 f"{s['n_rel']} | {s['first'] or '없음'} | {'O' if s['hit5'] else 'X'} |")
    no_tok = [s["qid"] for s in per_q if s["n_tokens"] == 0]
    L += ["", f"질의 토큰이 없는 질의 {len(no_tok)}개 (개인정보가 없는 질의이거나 질의 쪽 미탐): {', '.join(no_tok) or '없음'}"]
    rep = os.path.join(out_dir, f"queries30_eval_{a.mode}.md")
    open(rep, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n{rep}\n  Hit@1 {overall['hit1']:.3f} · Hit@5 {overall['hit5']:.3f} · MRR {overall['mrr']:.3f}"
          f" · Confuse@10 {overall['conf'] if overall['conf'] is None else round(overall['conf'], 3)}")


if __name__ == "__main__":
    main()
