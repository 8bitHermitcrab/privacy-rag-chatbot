"""탐지 통합(V6 경계 + 하이브리드 라벨 + 서로 보충)과 V6 서버·클라이언트 테스트 — 모델 없이 실행

    python -m pytest -q tests/test_detect_merge.py
"""
import importlib
import os
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

os.environ.setdefault("HMAC_KEY", "test-key-only-for-unit-tests")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starter.detect_merge import merge  # noqa: E402

TEXT = "홍길동님께 010-1234-5678로 안내함. 담당 금 재원, 문의자 선우 님"
HYB = [{"label": "person", "start": 0, "end": 4},            # 홍길동님 (경계가 김)
       {"label": "phone_number", "start": 6, "end": 19},     # 전화번호 (라벨 맞음)
       {"label": "person", "start": 30, "end": 34}]          # 금 재원 (V6 미탐)
V6 = [{"label": "person", "start": 0, "end": 3},             # 홍길동 (정확)
      {"label": "card_number", "start": 6, "end": 19},       # 라벨 틀림
      {"label": "person", "start": 40, "end": 42}]           # 선우 (하이브리드 미탐)


def test_merge_v6_boundary_hybrid_label_and_fill():
    spans, review = merge(HYB, V6)
    assert spans == [{"label": "person", "start": 0, "end": 3},          # V6 경계
                     {"label": "phone_number", "start": 6, "end": 19},  # 하이브리드 라벨
                     {"label": "person", "start": 30, "end": 34},       # 하이브리드 보충
                     {"label": "person", "start": 40, "end": 42}]       # V6 보충
    assert [(r["start"], r["source"]) for r in review] == [(30, "hybrid_only"), (40, "v6_only")]


def test_merge_empty_sides():
    assert merge([], []) == ([], [])
    spans, review = merge(HYB, [])
    assert spans == sorted(HYB, key=lambda s: (s["start"], s["end"])) and len(review) == 3
    spans, review = merge([], V6)
    assert [s["label"] for s in spans] == ["person", "card_number", "person"] and len(review) == 3


def test_merge_picks_label_with_largest_overlap():
    v6 = [{"label": "person", "start": 0, "end": 10}]
    hyb = [{"label": "phone_number", "start": 0, "end": 2}, {"label": "account_number", "start": 2, "end": 10}]
    assert merge(hyb, v6)[0] == [{"label": "account_number", "start": 0, "end": 10}]


class FakeDetector:
    def detect(self, text):
        if "boom" in text:
            raise RuntimeError("fail")
        return {"status": "READY_FOR_PSEUDONYMIZATION",
                "entities": [{"label": "phone_number", "start": 6, "end": 19}],
                "mask_intervals": [{"start": 0, "end": 3}, {"start": 6, "end": 19}]}


def _serve(det):
    tools = Path(__file__).resolve().parents[1] / "tools"
    sys.path.insert(0, str(tools))
    v6_server = importlib.import_module("v6_server")
    srv = HTTPServer(("127.0.0.1", 0), v6_server.make_handler(det, {"loaded_s": 0}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _client(url, mode=""):
    os.environ["PII_V6_URL"] = url
    os.environ["PII_V6"] = mode
    os.environ["PII_V6_RETRY_S"] = "60"
    import starter.v6_client as c
    return importlib.reload(c)


def test_server_client_round_trip_and_labels():
    srv = _serve(FakeDetector())
    try:
        c = _client(f"http://127.0.0.1:{srv.server_port}")
        out = c.detect_many([TEXT, TEXT])
        assert len(out) == 2 and out[0]["status"] == "READY_FOR_PSEUDONYMIZATION"
        # mask_intervals 라벨: 겹치는 entity 가 있으면 그 라벨, 없으면 person
        assert out[0]["spans"] == [{"label": "person", "start": 0, "end": 3},
                                   {"label": "phone_number", "start": 6, "end": 19}]
    finally:
        srv.shutdown()


def test_client_falls_back_when_server_errors_or_down():
    srv = _serve(FakeDetector())
    port = srv.server_port
    try:
        c = _client(f"http://127.0.0.1:{port}")
        assert c.detect_one("boom") is None          # 서버 내부 오류 → None
        assert c.detect_one(TEXT) is None            # 재시도 대기 중에는 부르지 않음
    finally:
        srv.shutdown()
        srv.server_close()
    c = _client(f"http://127.0.0.1:{port}")          # 서버가 꺼진 상태
    assert c.detect_one(TEXT) is None and c.STATS["v6_fail"] == 1


def test_detect_info_paths(monkeypatch):
    import starter.pii_pipeline as P
    monkeypatch.setattr(P, "_detect_hybrid", lambda text, use_model=True: list(HYB))
    c = _client("")                                   # V6 꺼짐
    spans, info = P.detect_info(TEXT)
    assert info["source"] == "hybrid" and spans == HYB
    c = _client("http://127.0.0.1:9")
    monkeypatch.setattr(c, "detect_one", lambda t: {"status": "READY_FOR_PSEUDONYMIZATION", "spans": list(V6)})
    spans, info = P.detect_info(TEXT)
    assert info["source"] == "merge" and len(spans) == 4 and len(info["review"]) == 2
    assert spans[1]["label"] == "phone_number"
    monkeypatch.setattr(c, "detect_one", lambda t: None)
    spans, info = P.detect_info(TEXT)
    assert info["source"] == "hybrid_fallback" and spans == HYB
    assert P.detect(TEXT) == HYB
    _client("")


class FakeCtx:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return FakeDetector()

    def __exit__(self, *a):
        self.closed = True


def test_inproc_mode_matches_server_conversion(monkeypatch):
    import types
    fake = types.ModuleType("pii_detector")
    calls = []
    fake.create_detector = lambda: calls.append(1) or FakeCtx()
    monkeypatch.setitem(sys.modules, "pii_detector", fake)
    c = _client("", mode="inproc")
    assert c.enabled()
    out = c.detect_many([TEXT, TEXT])
    assert out[0]["spans"] == [{"label": "person", "start": 0, "end": 3},
                               {"label": "phone_number", "start": 6, "end": 19}]
    c.detect_one(TEXT)
    assert len(calls) == 1                         # 모델은 한 번만 로딩
    srv = _serve(FakeDetector())                   # 서버 방식과 결과가 같은지
    try:
        http = _client(f"http://127.0.0.1:{srv.server_port}")
        assert http.detect_many([TEXT]) == [out[0]]
    finally:
        srv.shutdown()
    _client("")


def test_inproc_falls_back_when_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pii_detector", None)       # import 하면 ImportError
    c = _client("", mode="inproc")
    assert c.detect_one(TEXT) is None and c.STATS["v6_fail"] == 1
    assert c.detect_one(TEXT) is None                            # 재시도 대기 중
    _client("")

