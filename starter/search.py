"""검색 (실전 2 · 팀 버전): 질의 토큰 + 의미 검색 결합

    python -m starter.search "윤선영 고객 대출 상담 내역 찾아줘"

.env 의 SEARCH_MODE 로 실험 버전을 고릅니다 (run_queries.py --out 이름도 같이 바꿔 저장)
  v1 : 의미 검색만 (스타터 기본과 같음)
  v2 : 토큰 필터 any  — 질의 토큰을 하나라도 가진 청크를 먼저 의미 점수 순으로, 모자라면 의미 검색으로 채움
  v3 : 토큰 필터 all  — 질의 토큰을 모두 가진 청크 → 하나라도 가진 청크 → 의미 검색 순으로 채움
  v4 : 점수 결합     — 의미 점수 + SEARCH_TOKEN_WEIGHT × (질의 토큰 중 일치한 비율), 필터 없이 순위만 조정

약속 (run_queries.py, chat.py 가 사용)
  search(query, k, conn=None) -> (masked_query, query_tokens, docs)
  docs: [{"doc_id", "chunk_id", "masked_text", "tokens", "score"}, ...]  순위 순서
검색 단계는 rag_user 계정만 씁니다 (매핑 테이블 접근 없음).
"""
import argparse
import os

from . import db, pii_pipeline
from .embed import embed

SEARCH_MODE = os.getenv("SEARCH_MODE", "v2")
TOKEN_WEIGHT = float(os.getenv("SEARCH_TOKEN_WEIGHT", "0.5"))   # v4 가산점 크기
POOL = int(os.getenv("SEARCH_POOL", "50"))                      # v4 의미 검색 후보 수

_COLS = "doc_id, chunk_id, masked_text, tokens, 1 - (embedding <=> %s) AS sim"


def mask_query(query):
    masked, records = pii_pipeline.pseudonymize(query, pii_pipeline.detect(query))
    return masked, sorted({r["token"] for r in records})


def _fetch(cur, where, params, vec, limit):
    cur.execute(f"SELECT {_COLS} FROM rag.chunks {where} ORDER BY embedding <=> %s LIMIT %s",
                (vec, *params, vec, limit))
    return [{"doc_id": d, "chunk_id": c, "masked_text": t, "tokens": list(tk), "score": float(s)}
            for d, c, t, tk, s in cur.fetchall()]


def _merge(groups, k):
    """앞 그룹이 우선. 문서별로 처음 나온(가장 우선순위 높은) 청크 하나만 남김"""
    seen, out = set(), []
    for rows in groups:
        for r in rows:
            if r["doc_id"] not in seen:
                seen.add(r["doc_id"])
                out.append(r)
                if len(out) == k:
                    return out
    return out


def search(query, k=10, conn=None, mode=None):
    mode = mode or SEARCH_MODE
    masked_q, q_tokens = mask_query(query)
    vec = embed([masked_q])[0]
    own = conn is None
    conn = conn or db.rag_conn()
    try:
        with conn.cursor() as cur:
            semantic = _fetch(cur, "", (), vec, max(k * 3, POOL))
            if mode == "v1" or not q_tokens:
                docs = _merge([semantic], k)
            elif mode == "v2":
                anyhit = _fetch(cur, "WHERE tokens && %s::text[]", (q_tokens,), vec, k * 3)
                docs = _merge([anyhit, semantic], k)
            elif mode == "v3":
                allhit = _fetch(cur, "WHERE tokens @> %s::text[]", (q_tokens,), vec, k * 3)
                anyhit = _fetch(cur, "WHERE tokens && %s::text[]", (q_tokens,), vec, k * 3)
                docs = _merge([allhit, anyhit, semantic], k)
            elif mode == "v4":
                anyhit = _fetch(cur, "WHERE tokens && %s::text[]", (q_tokens,), vec, POOL)
                pool = {}
                for r in semantic + anyhit:              # 후보 = 의미 상위 + 토큰 일치 청크
                    pool.setdefault(r["chunk_id"], r)
                qs = set(q_tokens)
                for r in pool.values():
                    r["score"] = r["score"] + TOKEN_WEIGHT * len(qs & set(r["tokens"])) / len(qs)
                ranked = sorted(pool.values(), key=lambda r: -r["score"])
                docs = _merge([ranked], k)
            else:
                raise ValueError(f"알 수 없는 SEARCH_MODE: {mode}")
    finally:
        if own:
            conn.close()
    return masked_q, q_tokens, docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--mode", help="v1|v2|v3|v4 (기본: .env 의 SEARCH_MODE)")
    a = ap.parse_args()
    masked_q, q_tokens, docs = search(a.query, a.k, mode=a.mode)
    print(f"검색 모드: {a.mode or SEARCH_MODE}\n질의(가명처리): {masked_q}\n질의 토큰: {q_tokens or '없음'}\n")
    for i, d in enumerate(docs, 1):
        hit = "🔑" if set(d["tokens"]) & set(q_tokens) else "  "
        print(f"{i}. {hit} {d['doc_id']}  score={d['score']:.3f}  tokens={d['tokens'][:4]}")
        print("   " + d["masked_text"][:120].replace("\n", " ") + " …")


if __name__ == "__main__":
    main()
