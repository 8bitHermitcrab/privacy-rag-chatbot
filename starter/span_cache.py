"""외부 탐지기 결과로 detect() 를 대신하기 (검색 비교용: 탐지기만 바꾸고 나머지는 그대로)

    PII_SPANS_CACHE=outputs/spans_v6.jsonl python -m starter.ingest --data data/holdout_labeled.jsonl --reset

- 캐시 파일은 tools/build_span_cache.py 로 만듭니다. 한 줄 = {"sha": 원문 sha256, "entities": [...]} (원문 없음)
- PII_SPANS_CACHE 가 설정되면 pii_pipeline.detect() 가 모델을 돌리지 않고 캐시의 위치를 그대로 씁니다.
- 캐시에 없는 텍스트가 들어오면 바로 오류를 냅니다 (우리 탐지기가 몰래 섞이지 않도록).
  PII_SPANS_FALLBACK=1 이면 대신 원래 detect() 로 넘어갑니다 (권장하지 않음).
- PII_SPANS_FILTER=1 이면 캐시 결과에 팀 기준 필터(hybrid_detector.post_filter: 성+직급·성만·일반어 제거)를 적용합니다.
"""
import hashlib
import json
import os

PATHS = [p for p in os.getenv("PII_SPANS_CACHE", "").split(",") if p.strip()]
FALLBACK = os.getenv("PII_SPANS_FALLBACK", "0") == "1"
TEAM_FILTER = os.getenv("PII_SPANS_FILTER", "0") == "1"
_cache = None


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load():
    global _cache
    if _cache is None:
        _cache = {}
        for p in PATHS:
            with open(p.strip(), encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        d = json.loads(line)
                        _cache[d["sha"]] = d["entities"]
        print(f"[span_cache] {', '.join(PATHS)} 에서 텍스트 {len(_cache)}개 위치 사용 (모델 탐지 안 함"
              f"{', 팀 기준 필터 적용' if TEAM_FILTER else ''})")
    return _cache


def lookup(text):
    """캐시 사용 중이 아니면 None. 사용 중이면 spans 리스트 (없으면 오류 또는 None)"""
    if not PATHS:
        return None
    hit = _load().get(sha(text))
    if hit is None:
        hit = _load().get(sha(text.rstrip()))
    if hit is None:
        if FALLBACK:
            return None
        raise KeyError(f"[span_cache] 캐시에 없는 텍스트 (sha {sha(text)[:12]}…, {len(text)}자). "
                       "build_span_cache.py 의 --source 에 이 텍스트가 들어 있는 파일을 넣었는지 확인하세요.")
    spans = [dict(s) for s in hit if s["end"] <= len(text)]
    if TEAM_FILTER:
        from .hybrid_detector import post_filter
        spans, _ = post_filter(text, spans)
    return spans
