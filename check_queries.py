"""검색 실패 질의 확인 (오류 분석용)

    python check_queries.py outputs/run_v3.jsonl                      # 실패(X) 질의만, 모든 유형
    python check_queries.py outputs/run_v3.jsonl role_agent hybrid_status   # 특정 유형만
    python check_queries.py outputs/run_v3.jsonl --all               # 성공 질의도 함께

질의마다 보여주는 것
  원 질의 / 가명처리된 질의 / 질의 토큰 (비어 있으면 탐지가 질의 속 개인정보를 놓친 것)
  정답 문서 / 실제 상위 5개 / 혼동 문서(동명이인 등)가 상위 5개에 들어왔는지

※ holdout 질의를 직접 보는 스크립트입니다. 원인 분석·보고서 작성에만 쓰고,
  이 결과를 보고 탐지 규칙이나 검색 설정을 고치지 마세요 (holdout 과적합).
"""
import json
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
show_all = "--all" in sys.argv
run_path = args[0] if args else "outputs/run_v3.jsonl"
types = set(args[1:])

gold = {g["qid"]: g for g in map(json.loads, open("data/holdout_retrieval_queries_gold.jsonl", encoding="utf-8"))}
qs = {q["qid"]: q for q in map(json.loads, open("data/holdout_retrieval_queries.jsonl", encoding="utf-8"))}

n_shown = n_fail = 0
for r in map(json.loads, open(run_path, encoding="utf-8")):
    g = gold[r["qid"]]
    if types and g["type"] not in types:
        continue
    top5 = r["ranked_doc_ids"][:5]
    rel = set(g["relevant_doc_ids"])
    hit = bool(rel & set(top5))
    n_fail += not hit
    if hit and not show_all:
        continue
    n_shown += 1
    conf = set(g.get("confusable_doc_ids", [])) & set(top5)
    print(f'{r["qid"]} [{g["type"]}] {"O" if hit else "X"}')
    print("  원 질의  :", qs[r["qid"]]["query"])
    print("  가명처리 :", r["masked_query"])
    print("  질의 토큰:", r["query_tokens"] or "없음 ← 질의 탐지 누락 또는 개인정보 없는 질의")
    print("  정답     :", sorted(rel))
    print("  상위 5   :", [f"{d}{'✓' if d in rel else ''}" for d in top5])
    if conf:
        print("  혼동 문서:", sorted(conf), "← 상위 5개에 들어옴")
    print()

print(f"표시 {n_shown}개 / 실패(Hit@5 기준) {n_fail}개  ({run_path})")
