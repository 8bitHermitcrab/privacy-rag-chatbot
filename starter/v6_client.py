"""V6 탐지 서버(tools/v6_server.py) 호출 — 실패하면 None 을 돌려주고, detect() 는 하이브리드 단독으로 계속

    .env:  PII_V6_URL=http://127.0.0.1:8765     (비어 있으면 V6 를 쓰지 않음)
           PII_V6_TIMEOUT=30                     (초, 한 요청)
           PII_V6_RETRY_S=60                     (실패 후 이 시간 동안은 다시 부르지 않음)

서버로 보내는 것은 원문이지만 127.0.0.1 (같은 컴퓨터) 안에서만 오갑니다.
"""
import json
import os
import time
import urllib.error
import urllib.request

URL = os.getenv("PII_V6_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("PII_V6_TIMEOUT", "30"))
RETRY_S = float(os.getenv("PII_V6_RETRY_S", "60"))
LABELS7 = {"person", "phone_number", "email", "address", "resident_registration_number",
           "account_number", "card_number"}

_down_until = 0.0
STATS = {"v6_ok": 0, "v6_fail": 0}


def enabled():
    return bool(URL)


def detect_many(texts):
    """[ {"status", "spans"} | None ... ] — 서버 오류·시간 초과면 전부 None"""
    global _down_until
    if not URL or not texts:
        return [None] * len(texts)
    if time.time() < _down_until:
        STATS["v6_fail"] += len(texts)
        return [None] * len(texts)
    try:
        req = urllib.request.Request(URL + "/detect", data=json.dumps({"texts": texts}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            results = json.loads(resp.read())["results"]
        if len(results) != len(texts):
            raise ValueError("result_count")
        out = []
        for r in results:
            spans = [{"label": s["label"], "start": int(s["start"]), "end": int(s["end"])}
                     for s in r.get("spans", []) if s.get("label") in LABELS7]
            out.append({"status": r.get("status"), "spans": spans})
        STATS["v6_ok"] += len(texts)
        return out
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        if _down_until == 0.0 or time.time() >= _down_until:
            print(f"[v6] 응답 없음({type(exc).__name__}) → {RETRY_S:.0f}초 동안 하이브리드 단독으로 동작", flush=True)
        _down_until = time.time() + RETRY_S
        STATS["v6_fail"] += len(texts)
        return [None] * len(texts)


def detect_one(text):
    return detect_many([text])[0]
