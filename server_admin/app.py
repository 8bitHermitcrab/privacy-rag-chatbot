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


# 인물사전 관리는 업무 계정과 분리된 서버 관리자 인증을 사용합니다.
# 처리 결과에는 원문 값·정규화 값·비밀키를 포함하지 않습니다.
import threading
_person_job_lock = threading.Lock()
_person_previews = {}


def _person_run(sensitivity, apply=False, plan=None):
    from starter.person_dictionary import build_existing, PersonDictionaryError
    if not _person_job_lock.acquire(blocking=False):
        raise HTTPException(409, '인물사전 작업이 진행 중입니다. 완료 후 다시 시도하세요.')
    try:
        return build_existing(apply=apply,sensitivity=sensitivity,expected_plan=plan)
    except PersonDictionaryError as exc:
        # 이 모듈에서 정의한 검증 오류만 전달하며 DB 예외는 공통 처리에서 감춥니다.
        raise HTTPException(409,str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(409,'원문 파일 또는 탐지 모델이 없습니다. 서버의 PID_SOURCE_PATH와 PII_MODEL_DIR를 확인하세요.') from None
    finally:
        _person_job_lock.release()


@app.post('/api/person-dictionary/preview')
async def person_preview(request:Request):
    token=mutation(request)
    data=await payload(request)
    sensitivity=data.get('sensitivity')
    if sensitivity not in ('strict','balanced','relaxed'):
        raise HTTPException(400,'민감도 설정을 확인하세요.')
    result=await run_in_threadpool(_person_run,sensitivity)
    # 미리보기는 동일한 로그인 세션·민감도에만 적용할 수 있습니다.
    import time
    _person_previews.clear()
    _person_previews[token]=(result['plan'],sensitivity,time.monotonic())
    audit('person_dictionary_preview')
    return result


@app.post('/api/person-dictionary/apply')
async def person_apply(request:Request):
    token=mutation(request)
    data=await payload(request)
    import time
    cached=_person_previews.get(token)
    if not cached or time.monotonic()-cached[2]>1800 or data.get('plan')!=cached[0] or data.get('sensitivity')!=cached[1]:
        raise HTTPException(409,'같은 민감도로 미리보기를 다시 실행하세요. 미리보기는 30분간 유효합니다.')
    if data.get('confirmed') is not True:
        raise HTTPException(400,'반영할 결과를 확인하세요.')
    result=await run_in_threadpool(_person_run,cached[1],True,cached[0])
    _person_previews.pop(token,None)
    audit('person_dictionary_apply')
    return result


@app.get('/api/person-dictionary/options')
def person_options(request:Request):
    authenticated(request)
    import os
    from starter.person_rules import PROFILES
    from dotenv import dotenv_values
    settings_file=dotenv_values(Path(__file__).resolve().parents[1]/'.env')
    value=os.getenv('PID_SENSITIVITY',settings_file.get('PID_SENSITIVITY') or 'balanced')
    if value not in PROFILES:
        raise HTTPException(409,'서버의 PID_SENSITIVITY 설정을 확인하세요.')
    return {'sensitivity':value,'profiles':PROFILES}


# ---------------------------------------------------------------- 데이터 버전 (HMAC 키 교체)
# 서버 관리자 1인 전용. 키 값은 응답·작업 기록에 싣지 않고 지문만 보여 줍니다.
from . import data_versions as dvm


def _dv_call(function, *args):
    try:
        return function(*args)
    except dvm.DataVersionUIError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        from starter.data_version import DataVersionError
        if isinstance(exc, DataVersionError):
            raise HTTPException(409, str(exc)) from None
        if isinstance(exc, OperationError):
            raise HTTPException(409, str(exc)) from None
        raise


def _confirmed(data, expected):
    if data.get('confirmation') != expected:
        raise HTTPException(400, '작업 확인이 필요합니다.')


@app.get('/api/data-versions')
async def data_versions(request: Request):
    authenticated(request, touch=False)
    return await run_in_threadpool(_dv_call, dvm.overview)


@app.get('/api/data-versions/build')
def data_version_build_status(request: Request):
    authenticated(request, touch=False)
    return dvm.build.snapshot()


@app.post('/api/data-versions/build')
async def data_version_build(request: Request):
    mutation(request)
    data = await payload(request)
    _confirmed(data, 'build')
    source = data.get('source')
    result = await run_in_threadpool(_dv_call, dvm.start_build, source, data.get('key'), data.get('key_confirm'))
    audit('dv_build_start')
    return result


@app.post('/api/data-versions/switch')
async def data_version_switch(request: Request):
    mutation(request)
    data = await payload(request)
    node, version = field(data, 'node', 16), field(data, 'version', 8)
    _confirmed(data, node + ':' + version)
    return await run_in_threadpool(_dv_call, dvm.switch, node, version)


@app.post('/api/data-versions/promote')
async def data_version_promote(request: Request):
    mutation(request)
    data = await payload(request)
    version = field(data, 'version', 8)
    _confirmed(data, 'promote:' + version)
    return await run_in_threadpool(_dv_call, dvm.promote, version)


@app.post('/api/data-versions/retire')
async def data_version_retire(request: Request):
    mutation(request)
    data = await payload(request)
    version = field(data, 'version', 8)
    _confirmed(data, 'retire:' + version)
    return await run_in_threadpool(_dv_call, dvm.retire, version)


@app.post('/api/data-versions/drop')
async def data_version_drop(request: Request):
    mutation(request)
    data = await payload(request)
    version = field(data, 'version', 8)
    _confirmed(data, 'drop:' + version)
    return await run_in_threadpool(_dv_call, dvm.drop, version, data.get('typed'))


# ---------------------------------------------------------------- 모니터링 (분산기 요청 기록)
from . import monitoring


@app.get('/api/monitoring')
async def monitoring_summary(request: Request, minutes: int = 60):
    authenticated(request, touch=False)
    result = await run_in_threadpool(monitoring.summary, minutes)
    result['mixed'] = await run_in_threadpool(monitoring.mixed_state)
    return result
