"""팀원이 각자 만든 질의 10개를 검사하고 하나로 합치기 → data/queries30.jsonl

    python tools/merge_queries.py                          # data/queries_team/*.csv 를 모두 합침
    python tools/merge_queries.py --dir data/queries_team --out data/queries30.jsonl

팀원 파일: data/queries_team/<이름>.csv  (엑셀·구글시트에서 CSV UTF-8 로 저장)
  qid,query,type,relevant_doc_ids,confusable_doc_ids,memo
  A01,윤선영 고객 대출 상담 내역 알려줘,name_topic,H0012;H0045,H0101,카드론 상담 2건
  - qid      : 팀원별 접두어 + 번호 (A01~A10, B01~B10, C01~C10) — 겹치면 안 됨
  - query    : 실제 사용자가 할 법한 질문 (원문 그대로, 개인정보 포함 가능)
  - type     : 아래 TYPES 중 하나
  - relevant_doc_ids  : 정답 문서 ID, 여러 개면 ; 로 구분 (holdout 문서 H0001~H0187)
  - confusable_doc_ids: (선택) 헷갈리기 쉬운 오답 문서 — 동명이인 문서 등, ; 로 구분
  - memo     : (선택) 왜 이 문서가 정답인지 한 줄 — 검토용, 평가에는 안 씀

검사: 필수 칸, qid 중복, 질의 중복, 유형 이름, 문서 ID 존재 여부, 정답·오답 겹침
data/ 폴더는 개인정보가 있으므로 깃에 올리지 않습니다 (.gitignore 확인).
"""
import argparse
import collections
import csv
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TYPES = ["address_exact", "address_format_variant", "address_partial", "homonym_role", "hybrid_homonym_semantic",
         "hybrid_paraphrase", "hybrid_person_content", "hybrid_status", "hybrid_within_type", "name_topic",
         "name_variant", "person_all", "role_agent", "semantic_no_pii", "value_exact", "value_format_variant"]


def ids(cell):
    return [x.strip() for x in (cell or "").replace(",", ";").split(";") if x.strip()]


def read_file(path):
    if path.endswith(".jsonl"):
        return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{"qid": r.get("qid", "").strip(), "query": (r.get("query") or "").strip(), "type": (r.get("type") or "").strip(),
                 "relevant_doc_ids": ids(r.get("relevant_doc_ids")), "confusable_doc_ids": ids(r.get("confusable_doc_ids")),
                 "memo": (r.get("memo") or "").strip()} for r in csv.DictReader(f)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/queries_team")
    ap.add_argument("--docs", default="data/holdout_labeled.jsonl")
    ap.add_argument("--out", default="data/queries30.jsonl")
    ap.add_argument("--skip-no-gold", action="store_true",
                    help="정답 문서가 빈 질문(답변 확인용)은 오류 대신 건너뜀")
    a = ap.parse_args()

    doc_ids = {json.loads(l).get("id") for l in open(os.path.join(ROOT, a.docs), encoding="utf-8") if l.strip()}
    files = sorted(p for p in glob.glob(os.path.join(ROOT, a.dir, "*.csv")) + glob.glob(os.path.join(ROOT, a.dir, "*.jsonl"))
                   if not os.path.basename(p).startswith("_") and "_draft" not in os.path.basename(p))   # 템플릿·초안 제외
    if not files:
        sys.exit(f"{a.dir} 에 CSV/JSONL 파일이 없습니다")

    rows, errors, seen_q, seen_text, skipped = [], [], {}, {}, []
    for p in files:
        name = os.path.basename(p)
        for i, r in enumerate(read_file(p), 2):
            where = f"{name} {i}행 ({r.get('qid') or 'qid 없음'})"
            if r.get("qid") and r.get("query") and not r.get("relevant_doc_ids") and a.skip_no_gold:
                skipped.append(r["qid"])
                continue
            if not r.get("qid") or not r.get("query") or not r.get("relevant_doc_ids"):
                errors.append(f"{where}: qid·query·relevant_doc_ids 는 필수 (답변 확인용이면 --skip-no-gold)")
                continue
            if r["qid"] in seen_q:
                errors.append(f"{where}: qid 중복 ({seen_q[r['qid']]})")
            if r["query"] in seen_text:
                errors.append(f"{where}: 같은 질의가 이미 있음 ({seen_text[r['query']]})")
            if r.get("type") not in TYPES:
                errors.append(f"{where}: type '{r.get('type')}' 은 목록에 없음")
            bad = [d for d in r["relevant_doc_ids"] + r.get("confusable_doc_ids", []) if d not in doc_ids]
            if bad:
                errors.append(f"{where}: 없는 문서 ID {bad}")
            both = set(r["relevant_doc_ids"]) & set(r.get("confusable_doc_ids", []))
            if both:
                errors.append(f"{where}: 정답과 오답에 같은 문서 {sorted(both)}")
            seen_q[r["qid"]], seen_text[r["query"]] = where, where
            rows.append({k: r.get(k, []) for k in ("qid", "query", "type", "relevant_doc_ids", "confusable_doc_ids")})

    by_file = collections.Counter(r["qid"][:1] for r in rows)
    by_type = collections.Counter(r["type"] for r in rows)
    print(f"파일 {len(files)}개, 질의 {len(rows)}개  (접두어별 {dict(by_file)})")
    if skipped:
        print(f"정답 문서가 없어 건너뛴 질문 {len(skipped)}개: {', '.join(skipped)}")
    print("유형별:", ", ".join(f"{t} {by_type[t]}" for t in TYPES if by_type[t]))
    missing = [t for t in TYPES if not by_type[t]]
    if missing:
        print("질의가 없는 유형:", ", ".join(missing))
    if errors:
        print(f"\n오류 {len(errors)}개 — 고친 뒤 다시 실행하세요 (파일은 만들지 않음)")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    with open(os.path.join(ROOT, a.out), "w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda r: r["qid"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n{a.out} 저장 → python tools/eval_queries.py --queries {a.out}")


if __name__ == "__main__":
    main()
