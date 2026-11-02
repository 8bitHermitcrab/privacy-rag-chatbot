"""가명처리 검색 챗봇 웹 서버 (Streamlit 대신 쓰는 HTML 화면)

    pip install fastapi uvicorn
    uvicorn server:app --port 8000
    → 브라우저에서 http://127.0.0.1:8000         (아기 사자 어흥이 버전, 기본)
                  http://127.0.0.1:8000/simple  (심플 버전)

- 검색·답변은 app.py 와 같은 starter.chat.answer 를 그대로 사용 (프롬프트 ③, SEARCH_MODE=v3)
- 화면에는 가명처리된 질의·문서·답변만 보내고, 토큰은 복원하지 않습니다
- 기본 주소는 127.0.0.1 (내 컴퓨터에서만 접속). 다른 기기에서 열 때만 --host 0.0.0.0
"""
import os

os.environ.setdefault("SEARCH_MODE", "v3")

import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from starter import config
from starter.chat import answer
from starter.search import SEARCH_MODE

app = FastAPI(title="가명처리 검색 챗봇")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LION = os.path.join(WEB_DIR, "index.html")      # 어흥이 버전 (기본)
SIMPLE = os.path.join(WEB_DIR, "simple.html")   # 심플 버전


class Ask(BaseModel):
    query: str
    k: int = 3


@app.get("/")
def index():
    return FileResponse(LION)


@app.get("/simple")
def simple():
    return FileResponse(SIMPLE)


@app.get("/api/info")
def info():
    return {"search_mode": SEARCH_MODE, "embed_model": config.EMBED_MODEL, "llm_model": config.LLM_MODEL}


@app.post("/api/chat")
def chat(req: Ask):            # 일반 def → FastAPI가 스레드에서 실행 (탐지·API 호출이 블로킹이라)
    q = req.query.strip()
    if not q:
        return {"error": "질문이 비어 있어요"}
    try:
        res = answer(q, max(1, min(10, req.k)))
    except requests.exceptions.HTTPError as e:      # 서버가 죽지 않고 화면에 안내만 표시
        code = e.response.status_code if e.response is not None else None
        if code == 429:
            return {"error": "API 호출 한도를 넘었어요. 20~30초 뒤에 다시 물어봐 주세요."}
        return {"error": f"API 오류 ({code}). 터미널 로그를 확인해 주세요."}
    except requests.exceptions.RequestException:
        return {"error": "API에 연결하지 못했어요. 네트워크와 .env 설정을 확인해 주세요."}
    return {
        "answer": res["answer"],
        "masked_query": res["masked_query"],
        "query_tokens": res["query_tokens"],
        "docs": [{"doc_id": d["doc_id"], "score": round(float(d["score"]), 3), "tokens": d["tokens"],
                  "masked_text": d["masked_text"][:600]} for d in res["docs"]],
    }
