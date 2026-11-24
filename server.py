"""가명처리 검색 챗봇 웹 서버 (Streamlit 대신 쓰는 HTML 화면)

    pip install fastapi uvicorn
    uvicorn server:app --port 8000
    → 브라우저에서 http://127.0.0.1:8000         (시작 화면 → '어흥!' 버튼으로 챗봇 이동)
                  http://127.0.0.1:8000/chat    (아기 사자 어흥이 챗봇)
                  http://127.0.0.1:8000/simple  (심플 버전)

- 검색·답변은 app.py 와 같은 starter.chat.answer 를 그대로 사용 (프롬프트 ③, SEARCH_MODE=v3)
- 화면에는 가명처리된 질의·문서·답변만 보내고, 토큰은 복원하지 않습니다
- 기본 주소는 127.0.0.1 (내 컴퓨터에서만 접속). 다른 기기에서 열 때만 --host 0.0.0.0
"""
import os
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool

os.environ.setdefault("SEARCH_MODE", "v3")

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from starter import config
from starter.chat import answer
from starter.search import SEARCH_MODE

from starter.migrate import migrate
from starter.business_admin import router as business_router, allowed_documents, current, connection

@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(migrate)
    yield

app = FastAPI(title="가명처리 검색 챗봇", lifespan=lifespan)
app.include_router(business_router)

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LOGIN = os.path.join(WEB_DIR, "login.html")     # 시작(로그인) 화면
LION = os.path.join(WEB_DIR, "index.html")      # 어흥이 버전 (기본)
SIMPLE = os.path.join(WEB_DIR, "simple.html")   # 심플 버전
STAFF_LOGIN = os.path.join(WEB_DIR, "staff_login.html")  # 직원용 로그인 화면


class Ask(BaseModel):
    query: str
    k: int = 3


@app.get("/")
def login():
    return RedirectResponse('/login', status_code=303)


@app.get("/staff")
def staff_login():
    # 직원용 로그인 화면. 로그인 처리는 기존 /api/login·/api/password 를 그대로 사용합니다.
    return FileResponse(STAFF_LOGIN, media_type='text/html')


@app.get("/chat")
def index(request: Request):
    return authenticated_chat_page(request, LION)


@app.get("/simple")
def simple(request: Request):
    return authenticated_chat_page(request, SIMPLE)


@app.get("/api/info")
def info():
    return {"search_mode": SEARCH_MODE, "embed_model": config.EMBED_MODEL, "llm_model": config.LLM_MODEL}


@app.post("/api/chat")
def chat(req: Ask, request: Request):            # 일반 def → FastAPI가 스레드에서 실행 (탐지·API 호출이 블로킹이라)
    allowed = allowed_documents(request)
    if not allowed:
        raise HTTPException(403, '검색 가능한 확정 문서가 없습니다. 직원·팀·문서 권한을 확인하세요.')
    q = req.query.strip()
    if not q:
        return {"error": "질문이 비어 있어요"}
    try:
        res = answer(q, max(1, min(10, req.k)), allowed_doc_ids=allowed)
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


@app.get('/admin-assets/business_admin.js')
def business_script():
    # no-cache: 브라우저가 예전 파일을 저장해 두고 계속 쓰지 않도록, 화면을 열 때마다 서버에 다시 확인합니다.
    # (관리자 화면 수정이 새로고침만으로 바로 반영되게 하기 위함. 파일이 작아 매번 받아도 부담이 적습니다.)
    return FileResponse(os.path.join(WEB_DIR, 'business_admin.js'), media_type='application/javascript',
                        headers={'Cache-Control': 'no-cache'})


def authenticated_chat_page(request, path):
    try:
        with connection() as conn:
            current(request, conn)
    except HTTPException as error:
        if error.status_code in (401, 403):
            # 로그인 안 됨·첫 비밀번호 변경 전 → 직원 로그인 화면 (관리자 계정이면 그 화면에서 관리자 화면으로 안내)
            return RedirectResponse('/staff', status_code=303)
        raise
    # 기존 챗봇 두 화면에 같은 인증 헤더를 적용합니다. 질문 원문은 저장하지 않습니다.
    bootstrap = """<script>const originalFetch=window.fetch.bind(window);let businessCsrf=null;
window.fetch=async function(input,options={}){if(typeof input==='string'&&input==='/api/chat'){
if(!businessCsrf){const r=await originalFetch('/api/me',{cache:'no-store'});if(!r.ok){location.href='/staff';throw Error('로그인이 필요합니다.')}businessCsrf=(await r.json()).csrf}
options={...options,headers:{...options.headers,'X-CSRF-Token':businessCsrf}};}
const response=await originalFetch(input,options);if(typeof input==='string'&&input==='/api/chat'&&!response.ok){let d;try{d=await response.clone().json()}catch{}return new Response(JSON.stringify({error:d?.detail||'요청을 처리하지 못했습니다.'}),{status:200,headers:{'Content-Type':'application/json'}})}return response};</script>"""
    with open(path, encoding='utf-8') as handle:
        html = handle.read()
    return HTMLResponse(html.replace('<head>', '<head>' + bootstrap, 1), headers={'Cache-Control': 'no-store'})


@app.middleware('http')
async def business_headers(request, call_next):
    import psycopg
    try:
        response = await call_next(request)
    except (psycopg.Error, FileNotFoundError):
        response = JSONResponse({'detail': 'DB 또는 업무 계정 설정을 확인하세요.'}, status_code=503)
    if request.url.path.startswith('/api/') or request.url.path in ('/admin', '/login', '/staff', '/chat', '/simple'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
    return response
