"""간단한 대화 예제: 질의 가명처리 → 검색 → LLM 답변 (마스킹 텍스트만 전달, 토큰 그대로 표시).

    python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"

실명 복원·권한·출력 재검사는 종합에서 다룹니다. 이 파일은 vault 에 접근하지 않습니다.
LLM_API_KEY 가 없으면 검색 결과까지만 보여줍니다.
"""
import argparse

import requests

from . import config
from .search import search

SYSTEM = ("당신은 금융사 사내 문서 검색 도우미입니다. 주어진 문서 내용만 근거로 한국어로 답하세요.\n"
          "- <PERSON_...>, <PHONE_...> 같은 토큰은 가명처리된 개인정보입니다. 글자 하나도 바꾸지 말고 그대로 쓰고, 원래 값을 추측하지 마세요.\n"
          "- 같은 토큰은 같은 사람·같은 값입니다. 여러 문서에 같은 토큰이 나오면 연결해서 설명하세요.\n"
          "- 질문에 이미 나온 값을 반복하기보다, 그 값과 관련해 문서에 기록된 일(무슨 업무, 언제, 결과)을 요약하세요.\n"
          "- 문장마다 근거 문서 ID를 [H0058]처럼 붙이세요. 문서에 없는 내용이면 없다고 답하세요."
          "\n\n답변 형식 예시:\n"
          "- [H0001] <PERSON_aaaa1111> 고객이 카드 분실을 신고해 재발급을 접수함 (2026-05-02)\n"
          "- [H0002] 같은 고객(<PERSON_aaaa1111>)이 결제 계좌를 A은행에서 B은행으로 변경함\n"
          "공통점: 두 문서 모두 같은 고객 <PERSON_aaaa1111>의 기록임")


def answer(query, k=3):
    masked_q, q_tokens, docs = search(query, k)
    context = "\n\n".join(f"[{d['doc_id']}]\n{d['masked_text']}" for d in docs)
    result = {"masked_query": masked_q, "query_tokens": q_tokens, "docs": docs, "answer": None}
    if not config.LLM_API_KEY:
        return result
    r = requests.post(
        config.LLM_API_BASE.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json={"model": config.LLM_MODEL, "temperature": 0,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f"문서:\n{context}\n\n질문: {masked_q}"}]},
        timeout=60)
    r.raise_for_status()
    result["answer"] = r.json()["choices"][0]["message"]["content"]
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=3)
    a = ap.parse_args()
    res = answer(a.query, a.k)
    print(f"질의(가명처리): {res['masked_query']}")
    print("참고 문서:", ", ".join(d["doc_id"] for d in res["docs"]))
    print("\n답변:", res["answer"] or "(LLM_API_KEY 없음 — 검색 결과만 표시)")


if __name__ == "__main__":
    main()
