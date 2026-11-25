"""검색 (실전 2 · 팀 버전): 질의 토큰 + 의미 검색 결합

    python -m starter.search "윤선영 고객 대출 상담 내역 찾아줘"

.env 의 SEARCH_MODE 로 실험 버전을 고릅니다 (run_queries.py --out 이름도 같이 바꿔 저장)
  v1 : 의미 검색만 (스타터 기본과 같음)
  v2 : 토큰 필터 any  — 질의 토큰을 하나라도 가진 청크를 먼저 의미 점수 순으로, 모자라면 의미 검색으로 채움
  v3 : 토큰 필터 all  — 질의 토큰을 모두 가진 청크 → 하나라도 가진 청크 → 의미 검색 순으로 채움
  v4 : 점수 결합     — 의미 점수 + SEARCH_TOKEN_WEIGHT × (질의 토큰 중 일치한 비율), 필터 없이 순위만 조정

지역 토큰 (SEARCH_REGION=1, starter/region.py) — v2·v3 에만 적용
  질의에 '시도+시군구'가 있으면 지역 토큰을 만들어 다음 순서로 채움 (지역 일치는 기존 토큰 일치보다 뒤)
    v3: 질의 토큰+지역 모두 → 질의 토큰 모두 → 질의 토큰 하나라도 → 지역 일치 → 의미 검색
  질의 토큰이 없고 지역만 있어도 (예: '서울시 구로구 고객 상담 내역') 지역 일치 → 의미 검색

약속 (run_queries.py, chat.py 가 사용)
  search(query, k, conn=None) -> (masked_query, query_tokens, docs)
  docs: [{"doc_id", "chunk_id", "masked_text", "tokens", "score"}, ...]  순위 순서
검색 단계는 rag_user 계정만 씁니다 (매핑 테이블 접근 없음).
"""
import argparse
import os

from . import db, pii_pipeline, region
from .embed import embed

SEARCH_MODE = os.getenv("SEARCH_MODE", "v2")
TOKEN_WEIGHT = float(os.getenv("SEARCH_TOKEN_WEIGHT", "0.5"))   # v4 가산점 크기
POOL = int(os.getenv("SEARCH_POOL", "50"))                      # v4 의미 검색 후보 수
# 필터(권한 문서·토큰 조건)가 붙은 벡터 검색 누락 방지.
# HNSW 인덱스는 가까운 후보 ef_search(기본 40)개를 먼저 고른 뒤 WHERE 조건을 거르므로, 볼 수 있는 문서가 적으면
# 결과가 LIMIT 보다 적게(심하면 0건) 나옵니다. pgvector 0.8+ 의 iterative_scan 은 모자라면 인덱스를 더 읽어 채웁니다.
#   strict_order(기본): 거리 순서를 정확히 지킴 / relaxed_order: 조금 더 빠르나 순서가 약간 바뀔 수 있음 / off: 끄기
ITERATIVE_SCAN = os.getenv("SEARCH_ITERATIVE_SCAN", "strict_order")
MAX_SCAN_TUPLES = int(os.getenv("SEARCH_MAX_SCAN_TUPLES", "20000"))   # 한 번 검색에서 더 읽을 최대 행 수
if ITERATIVE_SCAN not in ("strict_order", "relaxed_order", "off"):
    raise ValueError("SEARCH_ITERATIVE_SCAN 은 strict_order / relaxed_order / off 중 하나여야 합니다.")

_COLS = "doc_id, chunk_id, masked_text, tokens, 1 - (embedding <=> %s) AS sim"


_resolver = None


def _get_resolver():
    """적재 때 만든 동명이인 레지스트리 (없으면 실전 2 방식 그대로)"""
    global _resolver
    if _resolver is None:
        from .ingest import REGISTRY_PATH
        from .person_resolver import PersonResolver
        _resolver = PersonResolver.load(REGISTRY_PATH, pii_pipeline._token) if REGISTRY_PATH.exists() else False
    return _resolver or None


def mask_query_detail(query):
    """질의 가명처리 + 동명이인 처리.
    반환: {"masked", "tokens", "homonyms": [{"original", "candidates"}]}
      - 질의에 단서('고객', '직원', 팀명, 전화번호…)가 있으면 그 사람 토큰 하나로 좁힘
      - 단서가 없으면 후보 토큰을 모두 검색에 쓰고 homonyms 에 남김 → 챗봇이 '동명이인이 n명 있습니다' 안내"""
    spans = pii_pipeline.detect(query)
    _, records = pii_pipeline.pseudonymize(query, spans)
    res, override, tokens, homonyms = _get_resolver(), {}, set(), []
    for r in records:
        if r["label"] == "person" and res:
            cands, ambiguous = res.query_candidates(query, r, records)
            override[(r["start"], r["end"])] = cands[0]
            tokens |= set(cands)
            if ambiguous:
                homonyms.append({"original": r["original"], "candidates": cands})
        else:
            tokens.add(r["token"])
    masked, _ = pii_pipeline.pseudonymize(query, spans, override=override)
    regions = region.region_tokens(query) if region.REGION else []
    return {"masked": masked, "tokens": sorted(tokens), "homonyms": homonyms, "regions": regions}
# ───────── 역할 단서 재정렬 ─────────
# 동명이인(같은 이름 토큰)이 있을 때, 질문의 역할 단서(고객/직원/대표)와 문서 안 그 이름의 역할을 비교해
# 같은 역할 문서를 앞으로, 다른 역할로만 나오는 문서를 뒤로 보냅니다. 가명처리 본문만 사용합니다(vault 미사용).
import re as _re
ROLE_RERANK = os.getenv("SEARCH_ROLE_RERANK", "1") != "0"
_STAFF_AFTER = _re.compile(r"^\s*(?:님\s*)?(?:과장|대리|부장|팀장|차장|사원|주임|책임|심사역|상담원|상담사|담당자|담당|매니저|직원|본부장|지점장|센터장)")
_STAFF_BEFORE = _re.compile(r"(?:직원|담당자|담당|상담원|상담사|심사역|작성자|처리자|확인자|승인자|기안자|센터|팀|부서|소속)\s*[:：]?\s*$|(?:팀|부|센터|본부|지점)\s*/\s*[^/\n]{1,6}\s*/\s*$")
_STAFF_CELL = _re.compile(r"^\s*\|\s*[^|\n]{0,12}(?:센터|팀|부|본부|지점)\s*\|")
_CUSTOMER_AFTER = _re.compile(r"^\s*(?:고객님|고객|님(?!\s*(?:과장|대리|부장|팀장)))")
_CUSTOMER_BEFORE = _re.compile(r"(?:고객|신청인|신청자|민원인|회원)\s*[:：]?\s*$")
_REP = _re.compile(r"^\s*대표(?:님|자)?|대표(?:자)?\s*[:：]?\s*$")


def _role_at(text, start, end):
    after, before = text[end:end + 14], text[max(0, start - 14):start]
    if _REP.match(after) or _REP.search(before):
        return "REP"
    if _STAFF_AFTER.match(after) or _STAFF_BEFORE.search(before) or _STAFF_CELL.match(after):
        return "STAFF"
    if _CUSTOMER_AFTER.match(after) or _CUSTOMER_BEFORE.search(before):
        return "CUSTOMER"
    return None


def _roles(text, token):
    return {r for m in _re.finditer(_re.escape(token), text) if (r := _role_at(text, m.start(), m.end()))}


def query_roles(masked_query):
    """질문 속 이름 토큰별 역할 단서. 예: '고객 <PERSON_x>' → {'<PERSON_x>': 'CUSTOMER'}"""
    result = {}
    for m in _re.finditer(r"<PERSON_[0-9A-Za-z]+>", masked_query):
        role = _role_at(masked_query, m.start(), m.end())
        # 질문의 '○○ 님'은 고객·직원 모두에게 쓰는 높임말이라 역할 단서로 쓰지 않습니다.
        if role == "CUSTOMER" and _re.match(r"^\s*님", masked_query[m.end():]):
            role = None
        if role:
            result[m.group()] = role
    return result


def _rerank(rows, cues):
    """문서 안에서 그 이름이 질문과 '다른 역할로만' 분명히 나오는 문서만 뒤로 보냅니다.
    역할 표현이 문서에 없는 경우가 많아, 역할이 일치한다고 앞으로 올리지는 않습니다(의미 점수 순서 유지)."""
    if not cues:
        return rows
    def mismatch(row):
        for token, role in cues.items():
            found = _roles(row["masked_text"], token)
            if found and role not in found:
                return 1
        return 0
    return sorted(rows, key=mismatch)


def mask_query(query):
    d = mask_query_detail(query)
    return d["masked"], d["tokens"]


def _scan_settings(cur):
    """이번 트랜잭션의 HNSW 반복 탐색 설정 (SET LOCAL 이라 연결을 돌려받는 다른 코드에는 영향 없음)."""
    cur.execute("SELECT set_config('hnsw.iterative_scan', %s, true), set_config('hnsw.max_scan_tuples', %s, true)",
                (ITERATIVE_SCAN, str(MAX_SCAN_TUPLES)))


def _fetch(cur, where, params, vec, limit, allowed_doc_ids=None):
    if allowed_doc_ids is not None:
        where += (" AND " if where else "WHERE ") + "doc_id = ANY(%s::text[])"
        params = (*params, list(allowed_doc_ids))
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


def search(query, k=10, conn=None, mode=None, allowed_doc_ids=None):
    # 웹 요청은 허용된 문서만 SQL 후보에 넣습니다. 빈 목록은 외부 API 호출 전 차단합니다.
    if allowed_doc_ids is not None and not allowed_doc_ids:
        return "", [], []
    mode = mode or SEARCH_MODE
    d = mask_query_detail(query)
    masked_q, q_tokens, regions = d["masked"], d["tokens"], d["regions"]
    vec = embed([masked_q])[0]
    own = conn is None
    conn = conn or db.rag_conn()
    try:
        with conn.cursor() as cur:
            _scan_settings(cur)
            semantic = _fetch(cur, "", (), vec, max(k * 3, POOL), allowed_doc_ids)
            reghit = (_fetch(cur, "WHERE tokens && %s::text[]", (regions,), vec, k * 3, allowed_doc_ids)
                      if regions and mode in ("v2", "v3") else [])
            if mode == "v1" or not (q_tokens or reghit):
                docs = _merge([semantic], k)
            elif not q_tokens:                           # 지역만 있는 질의
                docs = _merge([reghit, semantic], k)
            elif mode == "v2":
                anyhit = _fetch(cur, "WHERE tokens && %s::text[]", (q_tokens,), vec, k * 3, allowed_doc_ids)
                docs = _merge([anyhit, reghit, semantic], k)
            elif mode == "v3":
                both = (_fetch(cur, "WHERE tokens @> %s::text[]", (q_tokens + regions,), vec, k * 3, allowed_doc_ids)
                        if regions else [])              # 예: '윤선영 고객 서울 구로구' → 그 지역의 윤선영 먼저
                allhit = _fetch(cur, "WHERE tokens @> %s::text[]", (q_tokens,), vec, k * 3, allowed_doc_ids)
                anyhit = _fetch(cur, "WHERE tokens && %s::text[]", (q_tokens,), vec, k * 3, allowed_doc_ids)
                docs = _merge([both, allhit, anyhit, reghit, semantic], k)
                cues = query_roles(masked_q) if ROLE_RERANK else {}
                docs = _merge([_rerank(allhit, cues), _rerank(anyhit, cues), semantic], k)
            elif mode == "v4":
                anyhit = _fetch(cur, "WHERE tokens && %s::text[]", (q_tokens,), vec, POOL, allowed_doc_ids)
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
    regions = mask_query_detail(a.query)["regions"]
    print(f"검색 모드: {a.mode or SEARCH_MODE}\n질의(가명처리): {masked_q}\n질의 토큰: {q_tokens or '없음'}"
          f"\n지역 토큰: {regions or ('없음' if region.REGION else '꺼짐 (SEARCH_REGION=0)')}\n")
    for i, d in enumerate(docs, 1):
        hit = "🔑" if set(d["tokens"]) & set(q_tokens) else ("📍" if set(d["tokens"]) & set(regions) else "  ")
        print(f"{i}. {hit} {d['doc_id']}  score={d['score']:.3f}  tokens={d['tokens'][:4]}")
        print("   " + d["masked_text"][:120].replace("\n", " ") + " …")


if __name__ == "__main__":
    main()
