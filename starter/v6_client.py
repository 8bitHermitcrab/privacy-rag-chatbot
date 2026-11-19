"""V6(AegisRAG Step1: A.X + Veil) 호출 — 실패하면 None 을 돌려주고, detect() 는 하이브리드 단독으로 계속

두 가지 방식 (.env)
    PII_V6=inproc                         같은 프로세스에서 직접 실행 (서버 불필요, 권장)
                                          준비: pip install onnx onnxruntime
                                                pip install --no-deps -e ~/projects/AegisRAG/step1_pii_detector
                                          HF_TOKEN 은 AegisRAG/.env 를 V6 가 스스로 읽음 (또는 우리 .env)
    PII_V6_URL=http://127.0.0.1:8765      별도 서버 (tools/v6_server.py, AegisRAG 가상환경) — PII_V6 가 비어 있을 때만
    둘 다 비어 있으면 V6 를 쓰지 않음 (하이브리드 단독)

    PII_V6_TIMEOUT=30     서버 방식 한 요청 제한 (초)
    PII_V6_RETRY_S=60     실패 후 이 시간 동안은 다시 시도하지 않음

검증: 우리 가상환경(transformers 5.20.2 · numpy 2.5.4 · onnxruntime 1.32.0)의 inproc 결과가
      AegisRAG 가상환경 결과와 블라인드 171/171 문서에서 같음 (2026-11-19).
이 모듈은 표준 라이브러리만 씀 → tools/v6_server.py(AegisRAG 가상환경)에서도 to_spans() 를 그대로 import.
"""
import atexit
import json
import os
import threading
import time
import urllib.error
import urllib.request

MODE = os.getenv("PII_V6", "").strip().lower()                   # "inproc" | ""
URL = os.getenv("PII_V6_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("PII_V6_TIMEOUT", "30"))
RETRY_S = float(os.getenv("PII_V6_RETRY_S", "60"))
LABELS7 = {"person", "phone_number", "email", "address", "resident_registration_number",
           "account_number", "card_number"}

_down_until = 0.0
_det = None
_det_ctx = None
_lock = threading.Lock()
STATS = {"v6_ok": 0, "v6_fail": 0}


def enabled():
    return MODE == "inproc" or bool(URL)


def to_spans(result):
    """V6 detect() 결과 → {"status", "spans"}. spans = mask_intervals (실제로 가리는 범위),
    라벨은 겹치는 entity 의 라벨, 없으면 person. 서버·inproc 이 같은 함수를 써서 결과가 같음"""
    ents = [{"label": e["label"], "start": int(e["start"]), "end": int(e["end"])} for e in result.get("entities", [])]
    spans = []
    for m in result.get("mask_intervals", []):
        s, e = int(m["start"]), int(m["end"])
        lab = next((x["label"] for x in ents if x["start"] < e and s < x["end"]), "person")
        spans.append({"label": lab, "start": s, "end": e})
    return {"status": result.get("status"), "spans": spans}


def _fail(n, why):
    global _down_until
    if _down_until == 0.0 or time.time() >= _down_until:
        print(f"[v6] 사용할 수 없음({why}) → {RETRY_S:.0f}초 동안 하이브리드 단독으로 동작", flush=True)
    _down_until = time.time() + RETRY_S
    STATS["v6_fail"] += n
    return [None] * n


def _clean(r):
    return {"status": r.get("status"),
            "spans": [{"label": s["label"], "start": int(s["start"]), "end": int(s["end"])}
                      for s in r.get("spans", []) if s.get("label") in LABELS7]}


def _close():
    global _det, _det_ctx
    if _det_ctx is not None:
        try:
            _det_ctx.__exit__(None, None, None)
        except Exception:
            pass
    _det = _det_ctx = None


def preload():
    """inproc 모델을 미리 로딩 (챗봇 서버 시작 때 부르면 첫 질의가 느려지지 않음). 성공하면 True"""
    global _det, _det_ctx
    if MODE != "inproc":
        return False
    with _lock:
        if _det is not None:
            return True
        t0 = time.perf_counter()
        from pii_detector import create_detector          # ImportError 는 호출한 쪽에서 처리
        ctx = create_detector()
        det = ctx.__enter__()
        _det_ctx, _det = ctx, det
        atexit.register(_close)
        print(f"[v6] inproc 로딩 완료 ({time.perf_counter() - t0:.1f}초)", flush=True)
        return True


def _inproc(texts):
    try:
        preload()
        with _lock:
            return [_clean(to_spans(_det.detect(t))) for t in texts]
    except Exception as exc:                                # 원문이 섞일 수 있는 메시지는 출력하지 않음
        return _fail(len(texts), type(exc).__name__)


def _http(texts):
    try:
        req = urllib.request.Request(URL + "/detect", data=json.dumps({"texts": texts}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            results = json.loads(resp.read())["results"]
        if len(results) != len(texts):
            raise ValueError("result_count")
        return [_clean(r) for r in results]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        return _fail(len(texts), type(exc).__name__)


def detect_many(texts):
    """[ {"status", "spans"} | None ... ] — 실패하면 전부 None"""
    if not enabled() or not texts:
        return [None] * len(texts)
    if time.time() < _down_until:
        STATS["v6_fail"] += len(texts)
        return [None] * len(texts)
    out = _inproc(texts) if MODE == "inproc" else _http(texts)
    if out and out[0] is not None:
        STATS["v6_ok"] += len(texts)
    return out


def detect_one(text):
    return detect_many([text])[0]
