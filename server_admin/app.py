"""인터넷 공개를 허용하지 않는 별도 서버 관리자 API."""
import hmac
import ipaddress
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .security import Security, COOKIE, audit, recent_events, prepare_private_dir
from .operations import settings, operational_status, control, update_drain_timeout, OperationError
from .operations import progress, snapshot

WEB = Path(__file__).parent / "web"

@asynccontextmanager
async def lifespan(app):
    prepare_private_dir()
    app.state.security = Security()
    settings()
    audit("manager_start")
    yield

app = FastAPI(title="서버 관리자", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

@app.middleware("http")
async def guard(request: Request, call_next):
    response = None
    try:
        client = ipaddress.ip_address(request.client.host) if request.client else None
        host = urlsplit("http://" + request.headers.get("host", ""))
        allowed = client is not None and client.is_loopback and host.hostname in ("localhost","127.0.0.1","::1") and not host.username and not host.password
        if not allowed:
            response = JSONResponse({"detail":"로컬 SSH 터널을 통해 접속하세요."}, status_code=403)
        elif request.method not in ("GET","HEAD"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers['host']}"
            if origin != expected or request.headers.get("x-admin-request") != "1":
                response = JSONResponse({"detail":"허용되지 않은 요청입니다."}, status_code=403)
        if response is None:
            response = await call_next(request)
    except Exception:
        # 원본 예외·요청·연결 문자열은 외부 응답이나 로그에 싣지 않습니다.
        logging.getLogger(__name__).error("관리자 요청 처리 실패")
        response = JSONResponse({"detail":"처리하지 못했습니다. 관리자 설정과 연결 상태를 확인하세요."},status_code=500)
    response.headers.update({
        "Cache-Control":"no-store", "Pragma":"no-cache", "X-Content-Type-Options":"nosniff",
        "X-Frame-Options":"DENY", "Referrer-Policy":"no-referrer", "Cross-Origin-Resource-Policy":"same-origin",
        "Permissions-Policy":"camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy":"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    })
    return response

def authenticated(request, *, touch=True):
    token = request.cookies.get(COOKIE, "")
    session = request.app.state.security.session(token, touch=touch)
    if session is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return token, session

def mutation(request):
    token, session = authenticated(request)
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
        raise HTTPException(403, "요청 확인에 실패했습니다.")
    return token

async def payload(request):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON 요청만 허용합니다.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 4096:
            raise HTTPException(413, "요청이 너무 큽니다.")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "요청 형식이 올바르지 않습니다.") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "요청 형식이 올바르지 않습니다.")
    return value

def field(data, name, maximum=256):
    value = data.get(name)
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise HTTPException(400, "입력값을 확인하세요.")
    return value

@app.get("/")
def page():
    return FileResponse(WEB / "index.html")

@app.get("/assets/{name}")
def asset(name: str):
    if name not in {"admin.css", "admin.js"}:
        raise HTTPException(404)
    return FileResponse(WEB / name)

@app.post("/api/login")
async def login(request: Request):
    data = await payload(request)
    token, result = await run_in_threadpool(request.app.state.security.login, field(data,"username",64), field(data,"password"))
    if result == "locked":
        raise HTTPException(429, "로그인 시도가 많습니다. 마지막 실패로부터 15분 뒤 다시 시도하세요.")
    if not token:
        raise HTTPException(401, "계정 또는 비밀번호가 올바르지 않습니다.")
    response = JSONResponse({"ok":True})
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme=="https", max_age=8*60*60, path="/")
    return response

@app.get("/api/session")
def session(request: Request):
    _, session = authenticated(request)
    return {"username":request.app.state.security.record["username"],"csrf":session["csrf"]}

@app.post("/api/logout")
def logout(request: Request):
    request.app.state.security.logout(mutation(request))
    response = JSONResponse({"ok":True})
    response.delete_cookie(COOKIE, path="/")
    return response

@app.post("/api/password")
async def password(request: Request):
    mutation(request)
    data = await payload(request)
    try:
        changed = await run_in_threadpool(request.app.state.security.change_password, field(data,"current"), field(data,"new"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if not changed:
        raise HTTPException(400, "현재 비밀번호를 확인하세요.")
    response = JSONResponse({"ok":True})
    response.delete_cookie(COOKIE, path="/")
    return response

@app.get("/api/overview")
def overview(request: Request):
    authenticated(request)
    result = snapshot()
    result["events"] = recent_events()
    return result

@app.get('/api/operations')
def operations(request:Request):
    authenticated(request)
    return operational_status()

@app.post('/api/control')
async def operation(request:Request):
    mutation(request)
    data=await payload(request)
    try:
        return await run_in_threadpool(control,field(data,'target',16),field(data,'action',16),field(data,'confirmation',32),data.get('weight'))
    except OperationError as exc:
        raise HTTPException(409,str(exc)) from None

@app.get('/api/control/status')
def control_status(request:Request):
    # 자동 상태 조회는 세션의 유효 시간을 연장하지 않습니다.
    authenticated(request,touch=False)
    return progress.snapshot()

@app.post('/api/operations/settings')
async def operation_settings(request:Request):
    mutation(request)
    data=await payload(request)
    try:
        return await run_in_threadpool(update_drain_timeout,data.get('drain_timeout_seconds'))
    except OperationError as exc:
        raise HTTPException(409,str(exc)) from None
