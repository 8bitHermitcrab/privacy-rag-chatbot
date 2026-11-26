"""적재 파이프라인: 문서 → 탐지 → 가명처리 → 청크 → 임베딩 → rag.chunks / vault.token_map
(데이터 버전 v2 이상이면 rag_v2.chunks / vault_v2.token_map. 문서 권한 rag.document_info 는 공용)

사용법
    python -m starter.ingest --data data/holdout_labeled.jsonl --reset

- 입력 파일의 entities(정답 라벨)는 쓰지 않습니다. 탐지는 pii_pipeline.detect() 가 합니다.
- outputs/pseudo_<파일명>.jsonl 에 문서별 가명처리 결과를 남깁니다 (check_token_consistency.py 입력).
- PII_V6_URL 이 설정되면 하이브리드 + V6 통합 탐지, 한쪽만 잡은 구간은 outputs/review_queue_<파일명>.jsonl 에
  위치만 기록합니다 (원문 없음). 검수 때문에 적재가 멈추지는 않습니다.
"""
import argparse
import json
import time
from pathlib import Path

from . import config, db, pii_pipeline
from .embed import embed
from . import region



def load_docs(path, limit=None):
    with open(path, encoding="utf-8") as f:
        docs = [json.loads(l) for l in f if l.strip()]
    return docs[:limit] if limit else docs


def chunk(text, max_chars=600):
    """빈 줄·줄바꿈 단위로 나누고 max_chars 안에서 이어 붙입니다. 토큰이 잘리지 않도록 줄 단위로만 자릅니다."""
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > max_chars:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(config.DATA_DIR / "holdout_labeled.jsonl"))
    ap.add_argument("--reset", action="store_true", help="기존 rag.chunks / vault.token_map 비우고 시작")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-chars", type=int, default=600)
    ap.add_argument("--no-region", action="store_true", help="SEARCH_REGION=1 이어도 지역 토큰을 만들지 않음")
    ap.add_argument("--data-version", default=config.DATA_VERSION,
                    help="적재할 데이터 버전 (기본: .env 의 DATA_VERSION). 키 교체 때 새 버전(v2 …)에 미리 적재")
    ap.add_argument("--key-env", default="HMAC_KEY", help="가명처리에 쓸 키가 든 .env 항목 이름 (키 교체 때 NEW_HMAC_KEY)")
    a = ap.parse_args()
    from .data_version import prepare_target, DataVersionError
    try:
        prepare_target(a.data_version, a.key_env)
    except DataVersionError as error:
        raise SystemExit("중단: " + str(error))
    print(f"데이터 버전 {config.DATA_VERSION} ({config.RAG_DATA_SCHEMA}.chunks / {config.VAULT_DATA_SCHEMA}.token_map) 에 적재합니다.")

    if pii_pipeline.IS_DEMO and config.EMBED_PROVIDER == "api":
        print("⚠ 데모용 마스킹은 이름 등을 탐지하지 못해 원문 일부가 임베딩 API로 전송됩니다. "
              "가상 데이터라 테스트는 가능하지만, 실제 과제에서는 반드시 여러분의 모듈로 교체하세요.")

    docs = load_docs(a.data, a.limit)
    config.OUT_DIR.mkdir(exist_ok=True)
    pseudo_path = config.OUT_DIR / (f"pseudo_{Path(a.data).stem}.jsonl" if config.DATA_VERSION == "v1"
                                    else f"pseudo_{Path(a.data).stem}_{config.DATA_VERSION}.jsonl")   # 버전별 따로 보관

    t0 = time.perf_counter()
    # 탐지 → 기본 가명처리. 이름 토큰은 HMAC("person:이름") 하나뿐이고 동명이인 구분은 하지 않습니다.
    # 동명이인·같은 사람 판단은 적재 뒤 인물사전(starter.person_dictionary, vault.person)이 맡습니다.
    # 인물사전은 문서를 같은 방식으로 다시 가명처리해 DB 본문과 대조하므로, 여기서 토큰을 바꾸면 안 됩니다.
    staged, review_rows, sources = [], [], {}
    for d in docs:
        spans, info = pii_pipeline.detect_info(d["text"])
        sources[info["source"]] = sources.get(info["source"], 0) + 1
        for r in info["review"]:      # 한쪽 탐지기만 잡은 구간: 가리기는 이미 했고, 위치만 검수 대기열에 기록
            review_rows.append({"doc_id": d["id"], "label": r["label"], "start": r["start"], "end": r["end"],
                                "reason": r["source"], "status": "PENDING"})
        if info.get("v6_status") == "REVIEW_REQUIRED":
            review_rows.append({"doc_id": d["id"], "label": None, "start": None, "end": None,
                                "reason": "v6_review_required", "status": "PENDING"})
        staged.append({"id": d["id"], "spans": spans})

    rows, mapping, pseudo, collisions = [], {}, [], set()
    use_region = region.REGION and not a.no_region
    for d, st in zip(docs, staged):
        masked, records = pii_pipeline.pseudonymize(d["text"], st["spans"])
        pseudo.append({"id": d["id"], "entities": [
            {k: r[k] for k in ("label", "start", "end", "token")} for r in records]})
        for r in records:
            m = mapping.setdefault(r["token"], {"label": r["label"], "normalized": r["normalized"], "originals": set()})
            m["originals"].add(r["original"])
            if m["normalized"] != r["normalized"]:
                collisions.add(r["token"])
        # 지역 토큰 (SEARCH_REGION=1): 주소 토큰 → 그 주소의 '시도+시군구' 토큰. 텍스트에는 넣지 않고 tokens 에만 추가
        addr_regions = {}
        if use_region:
            for r in records:
                if r["label"] == "address":
                    for key in region.region_keys(r["original"]):
                        t = region.region_token(key)
                        addr_regions.setdefault(r["token"], set()).add(t)
                        mapping.setdefault(t, {"label": "region", "normalized": key, "originals": set()})["originals"].add(key)
        for i, c in enumerate(chunk(masked, a.max_chars)):
            toks = set(pii_pipeline.TOKEN_PATTERN.findall(c))
            toks |= {rt for t in toks for rt in addr_regions.get(t, ())}
            rows.append({"chunk_id": f"{d['id']}#{i}", "doc_id": d["id"], "chunk_idx": i,
                         "doc_type": d.get("doc_type"), "masked_text": c, "tokens": sorted(toks)})
    t_mask = time.perf_counter() - t0

    t1 = time.perf_counter()
    vecs = embed([r["masked_text"] for r in rows])
    t_embed = time.perf_counter() - t1

    with db.rag_conn() as conn, conn.cursor() as cur:
        if a.reset:
            cur.execute("TRUNCATE rag.chunks")
        # rag.chunks.doc_id 는 rag.document_info 를 참조(FK)하므로 문서를 먼저 '미확정'으로 등록합니다.
        # 이미 있는 문서는 건드리지 않아 관리자가 확정한 담당자·팀 정보를 덮어쓰지 않습니다.
        cur.executemany(
            """INSERT INTO rag.document_info (document_id, document_type, metadata_status)
               VALUES (%s, %s, 'PENDING') ON CONFLICT (document_id) DO NOTHING""",
            [(d["id"], d.get("doc_type")) for d in docs])
        cur.executemany(
            """INSERT INTO rag.chunks (chunk_id, doc_id, chunk_idx, doc_type, masked_text, tokens, embedding)
               VALUES (%(chunk_id)s, %(doc_id)s, %(chunk_idx)s, %(doc_type)s, %(masked_text)s, %(tokens)s, %(embedding)s)
               ON CONFLICT (chunk_id) DO UPDATE SET masked_text = EXCLUDED.masked_text,
                   tokens = EXCLUDED.tokens, embedding = EXCLUDED.embedding, doc_type = EXCLUDED.doc_type""",
            [r | {"embedding": v} for r, v in zip(rows, vecs)])

    with db.vault_conn() as conn, conn.cursor() as cur:
        if a.reset:
            cur.execute("TRUNCATE vault.token_map")
        cur.executemany(
            """INSERT INTO vault.token_map (token, label, normalized, originals)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (token) DO UPDATE SET originals =
                   ARRAY(SELECT DISTINCT unnest(vault.token_map.originals || EXCLUDED.originals))""",
            [(t, m["label"], m["normalized"], sorted(m["originals"])) for t, m in mapping.items()])

    with open(pseudo_path, "w", encoding="utf-8") as f:
        for p in pseudo:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    n = len(docs)
    if collisions:
        print(f"⚠ 토큰 충돌 {len(collisions)}종: 서로 다른 값이 같은 토큰을 씀 (예: {sorted(collisions)[:3]})")
    print(f"문서 {n}건 → 청크 {len(rows)}개, 토큰 {len(mapping)}종 적재")
    if use_region:
        n_reg = sum(1 for m in mapping.values() if m["label"] == "region")
        print(f"지역 토큰 {n_reg}종 (시도+시군구, tokens 배열에만 추가)")
    print(f"탐지+가명처리 {t_mask * 1000 / n:.1f} ms/문서, 임베딩 {t_embed * 1000 / n:.1f} ms/문서 ({config.EMBED_PROVIDER})")
    print(f"가명처리 결과: {pseudo_path}")
    names = {"merge": "통합(V6+하이브리드)", "hybrid": "하이브리드", "hybrid_fallback": "하이브리드(V6 응답 없음)",
             "cache": "캐시"}
    print("탐지 경로: " + ", ".join(f"{names.get(k, k)} {v}건" for k, v in sorted(sources.items())))
    if review_rows or "merge" in sources:
        review_path = config.OUT_DIR / f"review_queue_{Path(a.data).stem}.jsonl"
        with open(review_path, "w", encoding="utf-8") as f:
            for r in review_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        n_docs = len({r["doc_id"] for r in review_rows})
        print(f"검수 대기열 {len(review_rows)}건 (문서 {n_docs}개, 위치만 기록 · 적재는 이미 완료): {review_path}")


if __name__ == "__main__":
    main()
