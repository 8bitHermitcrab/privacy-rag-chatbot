"""V6(AegisRAG Step1: A.X + Veil) 탐지 서버 — AegisRAG 가상환경의 파이썬으로 실행

    ~/projects/AegisRAG/.venv/bin/python tools/v6_server.py --aegis ~/projects/AegisRAG --port 8765

- 모델은 시작할 때 한 번만 로딩하고 예열합니다 (첫 요청이 느려지지 않게).
- 127.0.0.1 에만 열립니다. 원문은 로그·응답 어디에도 남기지 않습니다 (위치·라벨·상태만 돌려줌).
- 우리 쪽에서는 .env 에 PII_V6_URL=http://127.0.0.1:8765 를 넣으면 detect() 가 통합 모드로 동작합니다.
- 서버 없이 같은 프로세스에서 돌리려면 PII_V6=inproc (starter/v6_client.py) — 이 서버는 버전 충돌 등으로
  inproc 을 쓸 수 없을 때의 대안입니다.

요청  POST /detect   {"texts": ["...", "..."]}
응답  {"results": [{"status": "READY_FOR_PSEUDONYMIZATION", "ms": 41.2,
                    "spans": [{"label": "person", "start": 0, "end": 3}, ...]}, ...]}
      spans = V6 의 mask_intervals (실제로 가리는 범위). 라벨은 겹치는 entity 의 라벨, 없으면 person
상태  GET /health    {"ok": true, "loaded_s": 31.2}
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MAX_BODY = 2_000_000          # 한 요청 최대 2MB
MAX_TEXTS = 64


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # 우리 레포 루트 (starter.v6_client)
from starter.v6_client import to_spans                              # noqa: E402  표준 라이브러리만 쓰는 모듈


def run_one(det, text):
    t0 = time.perf_counter()
    out = to_spans(det.detect(text))                                # inproc 모드와 같은 변환
    return {**out, "ms": round(1000 * (time.perf_counter() - t0), 1)}


def make_handler(det, info):
    lock = threading.Lock()           # 탐지기는 한 번에 하나씩 (스레드 안전성 보장 안 됨)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, **info})
            return self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/detect":
                return self._send(404, {"error": "not_found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413, {"error": "body_size"})
            try:
                texts = json.loads(self.rfile.read(n)).get("texts")
            except (ValueError, AttributeError):
                return self._send(400, {"error": "bad_json"})
            if not isinstance(texts, list) or not texts or len(texts) > MAX_TEXTS \
                    or not all(isinstance(t, str) for t in texts):
                return self._send(400, {"error": "bad_texts"})
            try:
                with lock:
                    results = [run_one(det, t) for t in texts]
            except Exception as exc:                       # 원문이 섞일 수 있는 메시지는 내보내지 않음
                return self._send(500, {"error": type(exc).__name__})
            return self._send(200, {"results": results})

        def log_message(self, fmt, *args):                  # 기본 접근 로그 끔 (요청 내용 노출 방지)
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aegis", required=True, help="AegisRAG 레포 경로")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    src = Path(a.aegis).expanduser().resolve() / "step1_pii_detector" / "src"
    if not (src / "pii_detector").is_dir():
        sys.exit(f"pii_detector 를 찾을 수 없음: {src}")
    sys.path.insert(0, str(src))
    from pii_detector import create_detector

    t0 = time.perf_counter()
    with create_detector() as det:
        det.detect("홍길동 고객의 연락처는 010-1234-5678입니다.")      # 합성 문장으로 예열
        info = {"loaded_s": round(time.perf_counter() - t0, 1)}
        srv = HTTPServer(("127.0.0.1", a.port), make_handler(det, info))
        print(f"[v6] 준비 완료 ({info['loaded_s']}초) → http://127.0.0.1:{a.port}  (Ctrl+C 로 종료)", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()


if __name__ == "__main__":
    main()
