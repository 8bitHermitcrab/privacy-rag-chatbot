"""임베딩. OpenAI 호환 /embeddings API 또는 테스트용 로컬 해시 임베딩.

주의: 이 함수에는 반드시 가명처리된(마스킹된) 텍스트만 넣어야 합니다.
"""
import hashlib
import re

import numpy as np
import requests

from . import config

_BATCH = 64


def _local(texts):
    """API 없이 파이프라인 동작만 확인하는 문자 n-gram 해시 임베딩 (검색 품질 낮음)."""
    out = []
    for t in texts:
        v = np.zeros(config.EMBED_DIM, dtype=np.float32)
        s = re.sub(r"\s+", " ", t)
        for n in (2, 3):
            for i in range(len(s) - n + 1):
                h = int(hashlib.md5(s[i:i + n].encode()).hexdigest()[:8], 16)
                v[h % config.EMBED_DIM] += 1.0
        norm = np.linalg.norm(v)
        out.append(v / norm if norm else v)
    return out


def _api(texts):
    if not config.EMBED_API_KEY:
        raise RuntimeError("EMBED_API_KEY 가 비어 있습니다. .env 를 확인하거나 EMBED_PROVIDER=local 로 테스트하세요.")
    import hashlib, json, os, time
    gap = float(os.getenv("EMBED_SLEEP", "2"))
    cache_path = config.OUT_DIR / f"embed_cache_{config.EMBED_MODEL}.jsonl"
    config.OUT_DIR.mkdir(exist_ok=True)
    cache = {}
    if cache_path.exists():
        for line in open(cache_path, encoding="utf-8"):
            c = json.loads(line)
            cache[c["key"]] = c["vec"]
    out = []
    with open(cache_path, "a", encoding="utf-8") as cf:
        for i, t in enumerate(texts):
            key = hashlib.sha256(t.encode()).hexdigest()
            if key in cache:                       # 이미 받은 임베딩은 재사용
                out.append(np.array(cache[key], dtype=np.float32))
                continue
            for attempt in range(20):
                r = requests.post(
                    config.EMBED_API_BASE.rstrip("/") + "/embeddings",
                    headers={"Authorization": f"Bearer {config.EMBED_API_KEY}"},
                    json={"model": config.EMBED_MODEL, "input": t, "encoding_format": "float"},
                    timeout=60,
                )
                body = r.json() if "json" in r.headers.get("content-type", "") else {}
                if r.ok and "data" in body:
                    break
                if r.status_code == 429 or r.status_code >= 500:
                    reset = (r.headers.get("x-ratelimit-reset-requests") or "").rstrip("s")
                    wait = float(reset) + 1 if reset.replace(".", "").isdigit() else min(60, 2 ** attempt)
                    print(f"  임베딩 {i + 1}/{len(texts)}: {r.status_code}, {wait:.0f}초 후 재시도")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"임베딩 API 오류 {r.status_code}: {r.text[:500]}")
            else:
                raise RuntimeError(f"임베딩 API 재시도 초과 ({r.status_code}): {r.text[:500]}  "
                                   f"→ 받은 {len(out)}개는 저장됨, 다시 실행하면 이어서 진행")
            vec = body["data"][0]["embedding"]
            cf.write(json.dumps({"key": key, "vec": vec}) + "\n")
            cf.flush()
            cache[key] = vec
            out.append(np.array(vec, dtype=np.float32))
            time.sleep(gap)
    if out and len(out[0]) != config.EMBED_DIM:
        raise RuntimeError(f"임베딩 차원 {len(out[0])} 이 EMBED_DIM={config.EMBED_DIM} 과 다릅니다.")
    return out


def embed(texts):
    return _api(texts) if config.EMBED_PROVIDER == "api" else _local(texts)
