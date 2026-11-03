"""질문 목록을 챗봇에 돌려 검색 문서와 답변을 기록 → deliverables/answers_<이름>.md

    python tools/run_answers.py --queries data/queries_team/team1_answers.csv
    python tools/run_answers.py --queries data/queries_team/team1_answers.csv -k 3 --gap 20

- 챗봇과 같은 starter.chat.answer 사용 (가명처리 → v3 검색 → LLM, .env 의 LLM_MODEL)
- 질문 사이 --gap 초 대기 (기본 20초, CLOVA 호출 한도 429 방지). 429 가 나면 기다렸다 재시도
- 정답 문서(relevant_doc_ids)가 있으면 검색 문서에 ✓ 표시. 없어도 됨 (답변 확인용 질문)
- 끝에 "시연 결과 기록표"(번호·실제 검색 문서 ID·답변 요약·판정·확인한 문제)를 만들어 둠 — 판정은 사람이 채움
- 기록에는 가명처리된 질의·답변만 씀 (토큰 복원 없음)
"""
import argparse
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def ids(cell):
    return [x.strip() for x in (cell or "").replace(",", ";").split(";") if x.strip()]


def load(path):
    if path.endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return [{"qid": r["qid"], "query": r["query"], "type": r.get("type", ""), "memo": r.get("memo", ""),
             "rel": ids(r.get("relevant_doc_ids")) if not isinstance(r.get("relevant_doc_ids"), list) else r["relevant_doc_ids"]}
            for r in rows if r.get("qid") and r.get("query")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("-k", type=int, default=3, help="LLM에 넘길 참고 문서 수 (챗봇 기본 3)")
    ap.add_argument("--gap", type=float, default=20)
    ap.add_argument("--retries", type=int, default=5)
    a = ap.parse_args()

    import requests
    from starter import config
    from starter.chat import answer

    qs = load(os.path.join(ROOT, a.queries))
    name = os.path.splitext(os.path.basename(a.queries))[0]
    out = os.path.join(ROOT, "deliverables", f"answers_{name}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    rows = []
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 챗봇 답변 기록 — {name}\n\nLLM {config.LLM_MODEL} · 참고 문서 {a.k}개 · 검색 v3\n\n")
        for i, q in enumerate(qs, 1):
            t0 = time.time()
            for n in range(a.retries + 1):
                try:
                    res = answer(q["query"], a.k)
                    break
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429 and n < a.retries:
                        print(f"  {q['qid']} 429 → {30 * (n + 1)}초 대기")
                        time.sleep(30 * (n + 1))
                    else:
                        raise
            sec = time.time() - t0
            rel = set(q["rel"])
            got = [d["doc_id"] for d in res["docs"]]
            mark = [d + (" ✓" if d in rel else "") for d in got]
            ans = (res["answer"] or "(LLM_API_KEY 없음)").strip()
            f.write(f"## {q['qid']} [{q['type']}]\n\n")
            if q["memo"]:
                f.write(f"- 기대: {q['memo']}\n")
            f.write(f"- 가명처리된 질의: {res['masked_query']}\n- 질의 토큰: {', '.join(res['query_tokens']) or '없음'}\n")
            f.write(f"- 정답 문서: {', '.join(q['rel']) or '없음 (답변 확인용)'}\n- 검색 문서: {', '.join(mark)}\n")
            f.write(f"- 응답 시간: {sec:.1f}초\n\n**답변**\n\n{ans}\n\n---\n\n")
            f.flush()
            summary = ans.replace("\n", " ").replace("|", "/")
            rows.append((q["qid"], ", ".join(mark), summary[:80] + ("…" if len(summary) > 80 else "")))
            print(f"{i}/{len(qs)} {q['qid']} 완료 ({sec:.1f}초)")
            if i < len(qs):
                time.sleep(a.gap)
        f.write("## 시연 결과 기록표\n\n판정 예시: 정상 답변 / 추가 확인 요청 / 부분 답변 / 적절한 답변 보류 / 근거 없는 단정 / 사실 오류\n\n")
        f.write("| 번호 | 실제 검색 문서 ID | 실제 답변 요약 | 판정 | 확인한 문제 |\n| --- | --- | --- | --- | --- |\n")
        for qid, docs, s in rows:
            f.write(f"| {qid} | {docs} | {s} | | |\n")
    print(f"저장: {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
