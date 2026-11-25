"""실제 업무 관리자: 기존 auth 테이블, 전용 DB 계정, 서명 세션과 변경 이력."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from server_admin.security import (state_dir, read_private, write_private,
    prepare_private_dir, make_password, verify_password)
from . import config

router = APIRouter()
COOKIE = 'rag_business'
ROOT = Path(__file__).resolve().parents[1]
LOCK_ID = 74932106
USER_COLS = 'u.user_id,u.login_id,u.employee_id,u.role_code,u.status,u.must_change_password,u.last_login_at,u.created_at,u.updated_at,u.can_review_persons'


def connection():
    value = read_private('business-runtime.json')
    return psycopg.connect(host=config.DB_HOST, port=config.DB_PORT,
        dbname=config.DB_NAME, user='auth_user', password=value['password'], row_factory=dict_row,
        connect_timeout=3, options='-c statement_timeout=5000 -c lock_timeout=3000')


def password_hash(value):
    return json.dumps(make_password(value), separators=(',', ':'))


def password_matches(value, encoded):
    try:
        return verify_password(value, json.loads(encoded))
    except (ValueError, KeyError, TypeError, RuntimeError):
        return False


def pack(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(bytes.fromhex(read_private('business-runtime.json')['signing_key']), body.encode(), hashlib.sha256).hexdigest()
    return body + '.' + signature


def unpack(value):
    try:
        body, signature = value.split('.')
        expected = hmac.new(bytes.fromhex(read_private('business-runtime.json')['signing_key']), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if payload['exp'] <= time.time():
            raise ValueError()
        return payload
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, '다시 로그인해 주세요.') from None


def current(request, conn, *, manager=False, changing=False):
    payload = unpack(request.cookies.get(COOKIE, ''))
    user = conn.execute(f'''SELECT {USER_COLS},COALESCE(p.search_scope,'NONE') AS search_scope,COALESCE(p.can_restore,false) AS can_restore,r.can_manage_users,
        p.restore_person,p.restore_phone,p.restore_email,p.restore_address,p.restore_rrn,p.restore_account,p.restore_card,
        e.team_id,e.position_code,e.verification_status,t.is_active AS team_active
        FROM auth.user_info u JOIN auth.role_info r USING(role_code)
        LEFT JOIN auth.employee_info e USING(employee_id)
        LEFT JOIN auth.team_info t USING(team_id)
        LEFT JOIN auth.team_position_policy p ON p.team_id=e.team_id AND p.position_code=e.position_code
        WHERE u.user_id=%s''', (payload['uid'],)).fetchone()
    if not user or user['status'] != 'ACTIVE' or str(user['updated_at']) != payload['version']:
        raise HTTPException(401, '계정 상태가 변경되었습니다. 다시 로그인해 주세요.')
    if user['must_change_password'] and not changing:
        raise HTTPException(403, '먼저 초기 비밀번호를 변경해 주세요.')
    if manager and not user['can_manage_users']:
        raise HTTPException(403, '계정 관리 권한이 필요합니다.')
    if request.method not in ('GET', 'HEAD'):
        # 분산기가 Host를 바꾸므로 브라우저 Origin은 별도로 검증하고 CSRF도 필수로 확인합니다.
        login_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), payload['csrf']):
            raise HTTPException(403, '화면을 새로고침한 후 다시 시도해 주세요.')
    return user, payload


def issue(response, user):
    response.set_cookie(COOKIE, pack({'uid': user['user_id'], 'version': str(user['updated_at']),
        'csrf': secrets.token_urlsafe(32), 'exp': int(time.time()) + 1800}),
        httponly=True, samesite='strict', max_age=1800, path='/',
        secure=os.getenv('BUSINESS_COOKIE_SECURE', '0') == '1')
    response.headers['Cache-Control'] = 'no-store'
    return response


def log(conn, actor, action, reason, before=None, after=None, target=None):
    conn.execute('''INSERT INTO auth.permission_change_log
        (actor_user_id,target_user_id,target_role_code,action,reason,before_value,after_value)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''',
        (actor, target, None if target else 'ADMIN', action, reason,
         Jsonb(before) if before else None, Jsonb(after) if after else None))


class Login(BaseModel):
    login_id: str = Field(min_length=1, max_length=100)
    password: str = Field(max_length=256)


class ChangePassword(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(min_length=14, max_length=256)


class Edit(BaseModel):
    kind: str
    key: str = Field(min_length=1, max_length=100)
    values: dict
    reason: str = Field(min_length=3, max_length=500)
    version: str | None = None


def login_origin(request):
    defaults = ','.join(f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (8000, 8001, 8002, 8080))
    allowed = {s.strip() for s in os.getenv('BUSINESS_ALLOWED_ORIGINS', defaults).split(',')}
    if request.headers.get('origin', '') not in allowed:
        raise HTTPException(403, '허용되지 않은 요청 출처입니다.')


@router.post('/api/login')
def login(body: Login, request: Request):
    login_origin(request)
    with connection() as conn:
        # DB 잠금으로 서버 두 대에서도 동일 계정에 대한 시도 횟수를 함께 제한합니다.
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
        user = conn.execute('SELECT * FROM auth.user_info WHERE login_id=%s FOR UPDATE', (body.login_id,)).fetchone()
        if not user:
            password_matches(body.password, password_hash('존재하지않는계정확인용비밀번호'))
            raise HTTPException(401, '아이디 또는 비밀번호를 확인해 주세요.')
        failures = conn.execute('''SELECT count(*) AS n FROM auth.permission_change_log
            WHERE target_user_id=%s AND action='LOGIN_FAILED' AND changed_at>now()-interval '15 minutes'
            AND (%s::timestamptz IS NULL OR changed_at>%s::timestamptz) ''',
            (user['user_id'], user['password_changed_at'], user['password_changed_at'])).fetchone()['n']
        if failures >= 5:
            raise HTTPException(429, '로그인 시도가 많습니다. 15분 뒤 다시 시도해 주세요.')
        if user['status'] != 'ACTIVE' or not password_matches(body.password, user['password_hash']):
            log(conn, user['user_id'], 'LOGIN_FAILED', '계정 인증 실패', target=user['user_id'])
            return JSONResponse({'detail': '아이디 또는 비밀번호를 확인해 주세요.'}, status_code=401)
        user = conn.execute('UPDATE auth.user_info SET last_login_at=now() WHERE user_id=%s RETURNING user_id,updated_at,must_change_password', (user['user_id'],)).fetchone()
        return issue(JSONResponse({'ok': True, 'must_change_password': user['must_change_password']}), user)


@router.get('/api/me')
def me(request: Request):
    with connection() as conn:
        user, payload = current(request, conn, changing=True)
        return {'login_id': user['login_id'], 'csrf': payload['csrf'], 'must_change_password': user['must_change_password'],
            'can_manage_users': user['can_manage_users'], 'role_code': user['role_code'], 'team_id': user['team_id'], 'position_code': user['position_code'],
            'search_scope': user['search_scope'], 'can_restore': user['can_restore'], 'restore': restore_of(user),
            'can_review_persons': bool(user['can_manage_users'] and user['can_review_persons'])}


@router.post('/api/password')
def change_password(body: ChangePassword, request: Request):
    with connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
        user, _ = current(request, conn, changing=True)
        old = conn.execute('SELECT password_hash FROM auth.user_info WHERE user_id=%s FOR UPDATE', (user['user_id'],)).fetchone()
        if not password_matches(body.old_password, old['password_hash']):
            raise HTTPException(400, '현재 비밀번호를 확인해 주세요.')
        if body.old_password == body.new_password:
            raise HTTPException(400, '기존 비밀번호와 다른 값을 입력하세요.')
        saved = conn.execute('''UPDATE auth.user_info SET password_hash=%s,must_change_password=false,
            password_changed_at=now(),updated_by=user_id WHERE user_id=%s RETURNING user_id,updated_at''',
            (password_hash(body.new_password), user['user_id'])).fetchone()
        log(conn, user['user_id'], 'PASSWORD_CHANGED', '본인 비밀번호 변경', target=user['user_id'])
        return issue(JSONResponse({'ok': True}), saved)


@router.post('/api/logout')
def logout(request: Request):
    with connection() as conn:
        user, _ = current(request, conn, changing=True)
        conn.execute('UPDATE auth.user_info SET updated_by=user_id WHERE user_id=%s', (user['user_id'],))
    result = JSONResponse({'ok': True})
    result.delete_cookie(COOKIE, path='/')
    return result


@router.get('/admin')
def admin_page(request: Request):
    try:
        with connection() as conn:
            current(request, conn, manager=True)
    except HTTPException as error:
        if error.status_code == 401 or (error.status_code == 403 and '초기 비밀번호' in str(error.detail)):
            return RedirectResponse('/login', status_code=303)
        if error.status_code == 403 and (ROOT / 'web/forbidden.html').exists():   # 관리 권한 없음 → 안내 화면
            return FileResponse(ROOT / 'web/forbidden.html', status_code=403, headers={'Cache-Control': 'no-store'})
        raise
    return FileResponse(ROOT / 'web/admin.html', headers={'Cache-Control': 'no-store'})


@router.get('/api/admin/data')
def data(request: Request):
    with connection() as conn:
        current(request, conn, manager=True)
        result = {
            'users': conn.execute(f'SELECT {USER_COLS} FROM auth.user_info u ORDER BY u.user_id').fetchall(),
            'employees': conn.execute('SELECT employee_id,team_id,position_code,verification_status,created_at,updated_at FROM auth.employee_info ORDER BY employee_id').fetchall(),
            'teams': conn.execute('SELECT * FROM auth.team_info ORDER BY team_id').fetchall(),
            'roles': conn.execute('SELECT role_code,role_name,can_manage_users,created_at,updated_at FROM auth.role_info ORDER BY role_code').fetchall(),
            'policies': conn.execute('SELECT * FROM auth.team_position_policy ORDER BY team_id,position_code').fetchall(),
            'documents': conn.execute('SELECT * FROM rag.document_info ORDER BY document_id').fetchall(),
            'logs': conn.execute('''SELECT l.log_id,u.login_id AS actor,l.target_user_id,l.target_role_code,l.action,l.reason,l.before_value,l.after_value,l.changed_at
                FROM auth.permission_change_log l JOIN auth.user_info u ON u.user_id=l.actor_user_id ORDER BY l.log_id DESC LIMIT 100''').fetchall()}
        return result


def clean_values(kind, values):
    fields = {
        'users': {'employee_id', 'role_code', 'status', 'can_review_persons'},
        'employees': {'team_id', 'position_code', 'verification_status'},
        'teams': {'team_name', 'is_active'},
        'roles': {'role_name'},
        'documents': {'owner_employee_id', 'team_id', 'metadata_status'}}
    if kind not in fields or not values or set(values) - fields[kind]:
        raise HTTPException(400, '허용되지 않은 수정 항목입니다.')
    for key, value in values.items():
        if key in ('is_active', 'can_restore', 'can_review_persons'):
            if type(value) is not bool:
                raise HTTPException(400, '참 또는 거짓 값이 필요합니다.')
        elif value is not None and (not isinstance(value, str) or not 1 <= len(value.strip()) <= 200):
            raise HTTPException(400, '문자 입력값을 확인해 주세요.')
    return values


@router.post('/api/admin/save')
def save(body: Edit, request: Request):
    table = {'users': ('user_info', 'user_id'), 'employees': ('employee_info', 'employee_id'),
             'teams': ('team_info', 'team_id'), 'roles': ('role_info', 'role_code'),
             'documents': ('document_info', 'document_id')}
    values = clean_values(body.kind, body.values)
    name, key = table[body.kind]
    schema = 'rag' if body.kind == 'documents' else 'auth'
    if body.kind == 'users' and not body.key.isdecimal():
        raise HTTPException(400, '계정 번호를 확인하세요.')
    try:
        with connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
            actor, _ = current(request, conn, manager=True)
            before = conn.execute(f'SELECT * FROM {schema}.{name} WHERE {key}=%s FOR UPDATE', (body.key,)).fetchone()
            if before and body.version != before['updated_at'].isoformat():
                raise HTTPException(409, '다른 작업자가 변경했습니다. 새로고침 후 다시 확인하세요.')
            if not before and body.version:
                raise HTTPException(409, '대상이 변경되었습니다.')
            if body.kind in ('roles', 'documents') and not before:
                raise HTTPException(400, '기존 역할 또는 문서만 수정할 수 있습니다.')
            if body.kind == 'employees':
                effective = {**(before or {}), **values}
                if effective.get('verification_status') == 'CONFIRMED':
                    if not effective.get('team_id') or not str(effective.get('position_code') or '').strip():
                        raise HTTPException(400, '직원 확정에는 소속 팀과 직책이 필요합니다. 이름 토큰은 사용하지 않습니다.')
            if body.kind == 'documents' and values.get('metadata_status', before['metadata_status']) == 'CONFIRMED':
                employee = conn.execute('''SELECT e.team_id,e.verification_status,t.is_active
                    FROM auth.employee_info e LEFT JOIN auth.team_info t USING(team_id) WHERE employee_id=%s''',
                    (values.get('owner_employee_id', before['owner_employee_id']),)).fetchone()
                team_id = values.get('team_id', before['team_id'])
                if not employee or employee['verification_status'] != 'CONFIRMED' or not employee['is_active'] or employee['team_id'] != team_id:
                    raise HTTPException(400, '확정된 담당 직원과 해당 직원의 사용 중인 팀을 선택하세요.')
            if body.kind == 'users':
                if not before:
                    raise HTTPException(400, '계정 생성 메뉴를 이용하세요.')
                managers = conn.execute('''SELECT count(*) AS n FROM auth.user_info u JOIN auth.role_info r USING(role_code)
                    WHERE u.status='ACTIVE' AND r.can_manage_users''').fetchone()['n']
                role = conn.execute('SELECT can_manage_users FROM auth.role_info WHERE role_code=%s', (values.get('role_code', before['role_code']),)).fetchone()
                losing = values.get('status', before['status']) != 'ACTIVE' or not role or not role['can_manage_users']
                if before['user_id'] == actor['user_id'] and values.get('can_review_persons') is True and not before['can_review_persons']:
                    # 본인 지정은 아직 아무도 권한이 없을 때(최초 지정)만 허용합니다. 이후에는 다른 권한자가 지정합니다.
                    reviewers = conn.execute('''SELECT count(*) AS n FROM auth.user_info u JOIN auth.role_info r USING(role_code)
                        WHERE u.status='ACTIVE' AND r.can_manage_users AND u.can_review_persons''').fetchone()['n']
                    if reviewers:
                        raise HTTPException(400, '본인의 인물 검토 권한은 다른 관리자가 지정해야 합니다.')
                if before['user_id'] == actor['user_id'] and losing:
                    raise HTTPException(400, '본인의 관리 권한이나 사용 상태를 해제할 수 없습니다.')
                if managers <= 1 and before['status'] == 'ACTIVE' and before['role_code'] == 'ADMIN' and losing:
                    raise HTTPException(400, '마지막 사용 가능한 관리자는 해제할 수 없습니다.')
                values['updated_by'] = actor['user_id']
            columns = list(values)
            if before:
                conn.execute(f"UPDATE {schema}.{name} SET " + ','.join(f'{c}=%s' for c in columns) + f' WHERE {key}=%s', (*values.values(), body.key))
            else:
                conn.execute(f"INSERT INTO auth.{name} ({key}," + ','.join(columns) + ') VALUES (' + ','.join(['%s'] * (len(columns) + 1)) + ')', (body.key, *values.values()))
            # 비밀번호·원문은 이력에 넣지 않습니다. 팀·직원은 ADMIN 정책 대상 + 엔터티 키로 구분합니다.
            safe_before = {c: before.get(c) for c in columns if c != 'updated_by'} if before else None
            log(conn, actor['user_id'], body.kind.upper() + '_SAVED', body.reason,
                {'entity': body.kind, 'key': body.key, 'values': safe_before},
                {'entity': body.kind, 'key': body.key, 'values': {c: values[c] for c in columns if c != 'updated_by'}},
                int(body.key) if body.kind == 'users' else None)
        return {'ok': True}
    except (psycopg.IntegrityError, psycopg.DataError):
        raise HTTPException(400, '중복값·연결된 직원/팀·확정 필수값·역할 정책을 확인해 주세요.') from None


class CreateUser(BaseModel):
    login_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    employee_id: str | None = Field(default=None, max_length=100)
    role_code: str
    reason: str = Field(min_length=3, max_length=500)


@router.post('/api/admin/users')
def create_user(body: CreateUser, request: Request):
    temporary = secrets.token_urlsafe(18)
    try:
        with connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
            actor, _ = current(request, conn, manager=True)
            user = conn.execute('''INSERT INTO auth.user_info(login_id,password_hash,employee_id,role_code,created_by,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING user_id''',
                (body.login_id, password_hash(temporary), body.employee_id or None, body.role_code, actor['user_id'], actor['user_id'])).fetchone()
            log(conn, actor['user_id'], 'USER_CREATED', body.reason,
                after={'login_id': body.login_id, 'role_code': body.role_code}, target=user['user_id'])
        return JSONResponse({'ok': True, 'temporary_password': temporary}, headers={'Cache-Control': 'no-store'})
    except psycopg.IntegrityError:
        raise HTTPException(400, '아이디·직원 연결·역할을 확인하세요. 직원당 계정은 하나입니다.') from None


class InviteMember(BaseModel):
    employee_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')


@router.post('/api/admin/invite')
def invite_member(body: InviteMember, request: Request):
    """회원 초대: 관리자는 사번만 입력합니다. 아이디=사번, 역할=STAFF, 임시 비밀번호는 응답으로 한 번만 돌려줍니다.
    직원 정보가 없으면 '확인 전(PENDING)' 직원으로 함께 등록합니다. 팀·직책을 확정하기 전에는 검색 정책이 없어 검색할 수 없습니다."""
    employee_id = body.employee_id
    reason = '관리자 회원 초대 (사번 입력)'
    temporary = secrets.token_urlsafe(18)
    try:
        with connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
            actor, _ = current(request, conn, manager=True)
            if conn.execute('SELECT 1 FROM auth.user_info WHERE employee_id=%s OR login_id=%s', (employee_id, employee_id)).fetchone():
                raise HTTPException(409, '이미 계정이 있는 사번입니다.')
            employee = conn.execute('SELECT verification_status FROM auth.employee_info WHERE employee_id=%s', (employee_id,)).fetchone()
            if not employee:
                conn.execute('INSERT INTO auth.employee_info(employee_id) VALUES (%s)', (employee_id,))
                log(conn, actor['user_id'], 'EMPLOYEES_SAVED', reason, None,
                    {'entity': 'employees', 'key': employee_id, 'values': {'verification_status': 'PENDING'}})
            user = conn.execute('''INSERT INTO auth.user_info(login_id,password_hash,employee_id,role_code,created_by,updated_by)
                VALUES (%s,%s,%s,'STAFF',%s,%s) RETURNING user_id''',
                (employee_id, password_hash(temporary), employee_id, actor['user_id'], actor['user_id'])).fetchone()
            # 임시 비밀번호는 이력에 남기지 않습니다.
            log(conn, actor['user_id'], 'USER_CREATED', reason,
                after={'login_id': employee_id, 'role_code': 'STAFF', 'invited': True}, target=user['user_id'])
        return JSONResponse({'ok': True, 'login_id': employee_id, 'temporary_password': temporary,
                             'employee_created': not employee,
                             'employee_status': employee['verification_status'] if employee else 'PENDING'},
                            headers={'Cache-Control': 'no-store'})
    except psycopg.IntegrityError:
        raise HTTPException(400, '사번을 확인하세요. 직원당 계정은 하나입니다.') from None


class ResetPassword(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post('/api/admin/users/{user_id}/reset-password')
def reset_password(user_id: int, body: ResetPassword, request: Request):
    temporary = secrets.token_urlsafe(18)
    with connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
        actor, _ = current(request, conn, manager=True)
        if user_id == actor['user_id']:
            raise HTTPException(400, '본인 비밀번호는 비밀번호 변경 메뉴를 이용하세요.')
        saved = conn.execute('''UPDATE auth.user_info SET password_hash=%s,must_change_password=true,
            password_changed_at=now(),updated_by=%s WHERE user_id=%s RETURNING user_id''',
            (password_hash(temporary), actor['user_id'], user_id)).fetchone()
        if not saved:
            raise HTTPException(404, '계정을 찾을 수 없습니다.')
        log(conn, actor['user_id'], 'PASSWORD_RESET', body.reason, target=user_id)
    return JSONResponse({'ok': True, 'temporary_password': temporary}, headers={'Cache-Control': 'no-store'})


# 개인정보 7종 복원 수준. 마킹은 항상 7종 모두 하고, 화면 표시 때 되돌릴 종류만 정합니다.
RESTORE_KINDS = ('person', 'phone', 'email', 'address', 'rrn', 'account', 'card')
RESTORE_PARTIAL_ONLY = ('rrn', 'account', 'card')
RESTORE_LEVELS = ('NONE', 'PARTIAL', 'FULL')
NO_RESTORE = {kind: 'NONE' for kind in RESTORE_KINDS}


def restore_of(row):
    """정책 행(또는 current 사용자)의 7종 복원 수준. 전체 스위치나 검색 범위가 막혀 있으면 모두 NONE."""
    if not row or not row.get('can_restore') or row.get('search_scope', 'NONE') == 'NONE':
        return dict(NO_RESTORE)
    return {kind: row.get('restore_' + kind) or 'NONE' for kind in RESTORE_KINDS}


def checked_restore(values, scope):
    """요청의 복원 수준을 검증합니다. None 이면 변경하지 않는다는 뜻입니다."""
    if values is None:
        return None
    if set(values) - set(RESTORE_KINDS):
        raise HTTPException(400, '복원 항목은 이름·전화번호·이메일·주소·주민번호·계좌·카드만 설정할 수 있습니다.')
    levels = {kind: values.get(kind, 'NONE') for kind in RESTORE_KINDS}
    for kind, level in levels.items():
        if level not in RESTORE_LEVELS:
            raise HTTPException(400, '복원 수준은 안 함·일부·전체 중에서 선택하세요.')
        if kind in RESTORE_PARTIAL_ONLY and level == 'FULL':
            raise HTTPException(400, '주민번호·계좌·카드는 일부 복원까지만 허용합니다.')
    if scope == 'NONE' and any(level != 'NONE' for level in levels.values()):
        raise HTTPException(400, '검색 불가 정책에는 복원을 허용할 수 없습니다.')
    return levels


def stored_restore(old):
    return {kind: old['restore_' + kind] for kind in RESTORE_KINDS} if old else dict(NO_RESTORE)


def write_policy(conn, team_id, position_code, scope, can_restore, levels, exists):
    cols = ['search_scope', 'can_restore'] + ['restore_' + kind for kind in RESTORE_KINDS]
    values = [scope, can_restore] + [levels[kind] for kind in RESTORE_KINDS]
    if exists:
        conn.execute('UPDATE auth.team_position_policy SET ' + ','.join(c + '=%s' for c in cols)
                     + ' WHERE team_id=%s AND position_code=%s', (*values, team_id, position_code))
    else:
        conn.execute('INSERT INTO auth.team_position_policy(team_id,position_code,' + ','.join(cols) + ') VALUES ('
                     + ','.join(['%s'] * (len(cols) + 2)) + ')', (team_id, position_code, *values))


class TeamPositionPolicy(BaseModel):
    team_id: str = Field(min_length=1, max_length=100)
    position_code: str = Field(min_length=1, max_length=100)
    search_scope: str
    can_restore: bool = False
    restore: dict[str, str] | None = None
    reason: str = Field(min_length=3, max_length=500)
    version: str | None = None


@router.post('/api/admin/policies')
def save_policy(body: TeamPositionPolicy, request: Request):
    if body.search_scope not in ('OWN', 'TEAM', 'NONE') or not body.position_code.strip():
        raise HTTPException(400, '직책과 검색 범위를 확인하세요.')
    if body.can_restore and body.search_scope == 'NONE':
        raise HTTPException(400, '검색 불가 정책에는 복원을 허용할 수 없습니다.')
    requested = checked_restore(body.restore, body.search_scope)
    if body.team_id != body.team_id.strip() or body.position_code != body.position_code.strip():
        raise HTTPException(400, '팀 코드와 직책 앞뒤 공백을 제거하세요.')
    if len(body.reason.strip()) < 3:
        raise HTTPException(400, '변경 사유를 3자 이상 입력하세요.')
    try:
        with connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
            actor, _ = current(request, conn, manager=True)
            old = conn.execute('''SELECT * FROM auth.team_position_policy
                WHERE team_id=%s AND position_code=%s FOR UPDATE''', (body.team_id, body.position_code)).fetchone()
            if (old and body.version != old['updated_at'].isoformat()) or (not old and body.version):
                raise HTTPException(409, '정책이 변경되었습니다. 새로고침 후 다시 확인하세요.')
            # 복원 수준을 보내면 그 값으로, 안 보내면 기존 값을 유지합니다. 전체 스위치가 꺼지면 7종 모두 NONE 입니다.
            if requested is not None:
                levels = requested
                can_restore = body.can_restore or any(v != 'NONE' for v in levels.values())
            else:
                can_restore = body.can_restore
                levels = stored_restore(old) if can_restore else dict(NO_RESTORE)
            write_policy(conn, body.team_id, body.position_code, body.search_scope, can_restore, levels, bool(old))
            before = {'search_scope': old['search_scope'], 'can_restore': old['can_restore'], 'restore': stored_restore(old)} if old else None
            log(conn, actor['user_id'], 'TEAM_POSITION_POLICY_SAVED', body.reason,
                {'entity': 'team_position_policy', 'team_id': body.team_id, 'position_code': body.position_code, 'values': before},
                {'entity': 'team_position_policy', 'team_id': body.team_id, 'position_code': body.position_code,
                 'values': {'search_scope': body.search_scope, 'can_restore': can_restore, 'restore': levels}})
        return {'ok': True}
    except psycopg.IntegrityError:
        raise HTTPException(400, '등록된 팀과 정책 조합을 확인하세요.') from None


class TeamPolicyRow(BaseModel):
    position_code: str = Field(min_length=1, max_length=100)
    search_scope: str
    restore: dict[str, str] | None = None
    version: str | None = Field(default=None, max_length=100)


class TeamPolicyBatch(BaseModel):
    team_id: str = Field(min_length=1, max_length=100)
    policies: list[TeamPolicyRow] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


@router.post('/api/admin/policies/batch')
def save_team_policies(body: TeamPolicyBatch, request: Request):
    """팀 안의 직책별 변경을 한 트랜잭션으로 저장하며 기존 복원 설정은 보존합니다."""
    if body.team_id != body.team_id.strip() or len(body.reason.strip()) < 3:
        raise HTTPException(400, '팀 코드와 변경 사유를 확인하세요.')
    positions = [row.position_code for row in body.policies]
    if len(set(positions)) != len(positions):
        raise HTTPException(400, '같은 직책을 두 번 등록할 수 없습니다.')
    for row in body.policies:
        if not row.position_code.strip() or row.position_code != row.position_code.strip():
            raise HTTPException(400, '직책을 입력하고 앞뒤 공백을 제거하세요.')
        if row.search_scope not in ('OWN', 'TEAM', 'NONE'):
            raise HTTPException(400, '검색 범위는 본인 담당·소속 팀·검색 불가 중 선택하세요.')
        checked_restore(row.restore, row.search_scope)
    try:
        with connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
            actor, _ = current(request, conn, manager=True)
            if not conn.execute('SELECT 1 FROM auth.team_info WHERE team_id=%s', (body.team_id,)).fetchone():
                raise HTTPException(404, '팀을 찾을 수 없습니다. 목록을 새로고침하세요.')
            records = conn.execute('''SELECT * FROM auth.team_position_policy
                WHERE team_id=%s AND position_code=ANY(%s::text[]) FOR UPDATE''', (body.team_id, positions)).fetchall()
            existing = {row['position_code']: row for row in records}
            # 모든 버전을 먼저 확인합니다. 한 직책이라도 충돌하면 어느 직책도 저장하지 않습니다.
            for row in body.policies:
                old = existing.get(row.position_code)
                if (old and row.version != old['updated_at'].isoformat()) or (not old and row.version):
                    raise HTTPException(409, row.position_code + ' 정책이 다른 곳에서 변경되었습니다. 창을 닫고 다시 열어 확인하세요.')
            saved = []
            for row in body.policies:
                old = existing.get(row.position_code)
                requested = checked_restore(row.restore, row.search_scope)
                if requested is not None:
                    levels = requested
                    can_restore = any(v != 'NONE' for v in levels.values())
                else:
                    # 복원 항목을 보내지 않으면 기존 설정을 보존하고, 검색 불가로 바뀌면 모두 끕니다.
                    can_restore = bool(old and old['can_restore'] and row.search_scope != 'NONE')
                    levels = stored_restore(old) if can_restore else dict(NO_RESTORE)
                after = {'search_scope': row.search_scope, 'can_restore': can_restore, 'restore': levels}
                before = {'search_scope': old['search_scope'], 'can_restore': old['can_restore'], 'restore': stored_restore(old)} if old else None
                if before == after:
                    continue
                write_policy(conn, body.team_id, row.position_code, row.search_scope, can_restore, levels, bool(old))
                log(conn, actor['user_id'], 'TEAM_POSITION_POLICY_SAVED', body.reason,
                    {'entity': 'team_position_policy', 'team_id': body.team_id, 'position_code': row.position_code, 'values': before},
                    {'entity': 'team_position_policy', 'team_id': body.team_id, 'position_code': row.position_code, 'values': after})
                saved.append(row.position_code)
        return {'ok': True, 'saved': len(saved), 'positions': saved}
    except (psycopg.IntegrityError, psycopg.DataError):
        raise HTTPException(400, '팀과 직책 정책을 확인하세요. 이번 변경은 모두 취소했습니다.') from None


def chat_access(request):
    """채팅 요청 사용자와 검색 가능한 확정 문서 목록. 복원 수준은 restore_of(user) 로 구합니다."""
    with connection() as conn:
        user, _ = current(request, conn)
    if user['search_scope'] == 'NONE' or user['verification_status'] != 'CONFIRMED' or not user['team_active']:
        return user, []
    from .db import rag_conn
    column, value = ('owner_employee_id', user['employee_id']) if user['search_scope'] == 'OWN' else ('team_id', user['team_id'])
    with rag_conn() as conn:
        return user, [r[0] for r in conn.execute(f"SELECT document_id FROM rag.document_info WHERE metadata_status='CONFIRMED' AND {column}=%s", (value,)).fetchall()]


def allowed_documents(request):
    return chat_access(request)[1]


def write_restore_log(user, doc_ids, tokens, levels):
    """채팅 복원 1회 = 1행. 원래 값과 질문 원문은 넣지 않습니다. 실패하면 예외를 올려 복원 값을 보내지 않게 합니다."""
    with connection() as conn:
        conn.execute('''INSERT INTO auth.restore_log (user_id,team_id,position_code,doc_ids,tokens,levels)
            VALUES (%s,%s,%s,%s,%s,%s)''', (user['user_id'], user.get('team_id'), user.get('position_code'),
            list(doc_ids), list(tokens), Jsonb(levels)))


@router.get('/api/admin/health')
def health(request: Request):
    with connection() as conn:
        current(request, conn, manager=True)
        conn.execute('SELECT 1')
    from .db import rag_conn
    with rag_conn() as conn:
        metadata = dict(conn.execute('SELECT metadata_status,count(*) FROM rag.document_info GROUP BY 1').fetchall())
    from datetime import datetime, timezone
    return {'checked_at': datetime.now(timezone.utc).isoformat(),
        'items': {'db': {'status': 'ok', 'detail': '업무 DB 연결 및 문서 메타데이터 조회 확인'}},
        'ops': {}, 'database': 'ok', 'document_metadata': metadata, 'llm': '미검사',
        'notice': 'API 응답 성공이나 개인정보 보호 성공을 의미하지 않습니다.'}


@router.get('/login')
def login_page():
    page = ROOT / 'web/login.html'   # 디자인한 로그인 화면이 있으면 그 파일, 없으면 기본 화면
    if page.exists():
        return FileResponse(page, headers={'Cache-Control': 'no-store'})
    return HTMLResponse(LOGIN_HTML, headers={'Cache-Control': 'no-store'})


LOGIN_HTML = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>업무 계정 로그인</title><style>body{font-family:맑은 고딕,sans-serif;background:#f4f6fa;color:#1b2333}main{max-width:420px;margin:10vh auto;padding:32px;background:white;border-radius:18px}input,button{box-sizing:border-box;width:100%;padding:13px;margin:8px 0}button{background:#2f6bff;color:white;border:0;border-radius:8px}#message{white-space:pre-wrap;color:#b43b3b}</style>
<main><h1>업무 계정 로그인</h1><p>관리자가 발급한 계정으로 접속하세요.</p><form id="login"><label>아이디<input id="id" autocomplete="username" required maxlength="100"></label><label>비밀번호<input id="pw" type="password" autocomplete="current-password" required maxlength="256"></label><button>로그인</button></form>
<form id="change" hidden><h2>초기 비밀번호 변경</h2><label>현재 비밀번호<input id="old" type="password" autocomplete="current-password" required maxlength="256"></label><label>새 비밀번호 (14자 이상)<input id="new" type="password" autocomplete="new-password" required minlength="14" maxlength="256"></label><button>변경 후 시작</button></form><p id="message" role="status"></p></main>
<script>let csrf='';async function send(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'입력값을 확인하세요.');return d}
async function ready(){const r=await fetch('/api/me',{cache:'no-store'});if(!r.ok)return;const d=await r.json();csrf=d.csrf;if(d.must_change_password||new URLSearchParams(location.search).has('change')){document.querySelector('#login').hidden=true;document.querySelector('#change').hidden=false}else location.href=d.can_manage_users?'/admin':'/chat'}
if(new URLSearchParams(location.search).has('relogin'))document.querySelector('#message').textContent='계정 정보나 권한이 바뀌었거나 로그인 시간이 지났어요. 다시 로그인해 주세요.';
document.querySelector('#login').onsubmit=async e=>{e.preventDefault();try{await send('/api/login',{login_id:document.querySelector('#id').value,password:document.querySelector('#pw').value});document.querySelector('#pw').value='';await ready()}catch(e){document.querySelector('#message').textContent=e.message}};
document.querySelector('#change').onsubmit=async e=>{e.preventDefault();try{await send('/api/password',{old_password:document.querySelector('#old').value,new_password:document.querySelector('#new').value});document.querySelector('#old').value='';document.querySelector('#new').value='';location.href='/login'}catch(e){document.querySelector('#message').textContent=e.message}};ready();</script></html>'''


def prepare():
    """최초 실행 전 유지보수 계정으로 DB 접속 계정만 준비합니다. 웹 요청에서는 호출하지 않습니다."""
    from dotenv import dotenv_values
    from psycopg import sql
    env = dotenv_values(ROOT / '.env')
    prepare_private_dir()
    path = state_dir() / 'business-runtime.json'
    settings = read_private(path.name) if path.exists() else {'password': secrets.token_urlsafe(32), 'signing_key': secrets.token_hex(32)}
    with psycopg.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
            user=env.get('POSTGRES_USER', 'admin'), password=env['POSTGRES_PASSWORD']) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='auth_user'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE auth_user LOGIN PASSWORD {}').format(sql.Literal(settings['password'])))
        elif not path.exists():
            raise RuntimeError('기존 auth_user의 접속 설정이 없습니다. 계정을 임의로 초기화하지 않았습니다.')
        conn.execute('GRANT USAGE ON SCHEMA auth TO auth_user')
        conn.execute('GRANT SELECT,INSERT,UPDATE ON auth.user_info,auth.employee_info,auth.team_info,auth.role_info,auth.team_position_policy TO auth_user')
        conn.execute('GRANT SELECT,INSERT ON auth.permission_change_log TO auth_user')
        conn.execute('GRANT USAGE ON SCHEMA rag TO auth_user')
        conn.execute('GRANT SELECT,UPDATE ON rag.document_info TO auth_user')
        conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA auth TO auth_user')
        count = conn.execute('SELECT count(*) FROM auth.user_info').fetchone()[0]
        if count == 0:
            temporary = secrets.token_urlsafe(18)
            uid = conn.execute('''INSERT INTO auth.user_info(login_id,password_hash,role_code) VALUES (%s,%s,'ADMIN') RETURNING user_id''',
                ('businessadmin', password_hash(temporary))).fetchone()[0]
            conn.execute('UPDATE auth.user_info SET created_by=user_id,updated_by=user_id WHERE user_id=%s', (uid,))
            conn.execute('''INSERT INTO auth.permission_change_log(actor_user_id,target_user_id,action,reason)
                VALUES (%s,%s,'BOOTSTRAP','최초 업무 관리자 생성')''', (uid, uid))
            write_private('business-initial-login.txt', '업무 관리자 초기 로그인\n주소: http://127.0.0.1:8080/login\n아이디: businessadmin\n초기 비밀번호: ' + temporary + '\n최초 로그인 후 반드시 변경하세요. 전달 후 이 파일을 삭제하세요.\n')
        write_private(path.name, settings)
    with connection() as conn:
        conn.execute('SELECT document_id FROM rag.document_info LIMIT 0')
        conn.execute('SELECT team_id FROM auth.team_position_policy LIMIT 0')
    print('업무 관리자 준비 완료. 비밀 설정은 Git 밖의 보호 폴더에 저장했습니다.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='업무 관리자 최초 준비')
    parser.add_argument('--prepare', action='store_true', required=True)
    parser.parse_args()
    prepare()


# ───────── 인물 검토 (업무 관리자 중 지정된 권한자 전용) ─────────
# 계정 관리 권한(can_manage_users)과 개인별 인물 검토 권한(can_review_persons)을 모두 가진 사용자만 접근합니다.
# 관리자 판단을 위해 원문 값을 보여 주므로 열람·판정은 auth.permission_change_log 에 남깁니다(원문 값은 기록하지 않음).
def reviewer(request, conn):
    user, payload = current(request, conn, manager=True)
    if not user['can_review_persons']:
        raise HTTPException(403, '인물 검토 권한이 필요합니다. 다른 관리자에게 권한 지정을 요청하세요.')
    return user


def review_call(function, *args, **kwargs):
    from .person_review import ReviewError
    try:
        return function(*args, **kwargs)
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from None


class ReviewDecision(BaseModel):
    mention_id: int
    action: str
    expected_updated_at: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=500)
    pid: int | None = None
    target_mention_id: int | None = None


@router.get('/api/admin/person-review/summary')
def person_review_summary(request: Request):
    from .person_review import summary
    with connection() as conn:
        reviewer(request, conn)
    return review_call(summary)


@router.get('/api/admin/person-review/items')
def person_review_items(request: Request, queue: str = 'person'):
    from .person_review import list_items
    with connection() as conn:
        reviewer(request, conn)
    return {'items': review_call(list_items, queue)}


@router.get('/api/admin/person-review/item/{mention_id}')
def person_review_item(mention_id: int, request: Request):
    from .person_review import detail
    with connection() as conn:
        user = reviewer(request, conn)
        result = review_call(detail, mention_id)
        log(conn, user['user_id'], 'PERSON_REVIEW_VIEW', '인물 검토 원문 열람',
            after={'mention_id': mention_id, 'doc_id': result['mention']['doc_id']})
    return result


@router.post('/api/admin/person-review/decide')
def person_review_decide(body: ReviewDecision, request: Request):
    from .person_review import decide, PERSON_ACTIONS, FEATURE_ACTIONS
    if body.action not in PERSON_ACTIONS + FEATURE_ACTIONS + ('undo',):
        raise HTTPException(400, '처리 종류를 확인하세요.')
    with connection() as conn:
        user = reviewer(request, conn)
        result = review_call(decide, body.mention_id, body.action, body.expected_updated_at,
                             pid=body.pid, target_mention_id=body.target_mention_id,
                             note=f'{user["login_id"]}: {body.reason.strip()}')
        log(conn, user['user_id'], 'PERSON_REVIEW_' + body.action.upper(), body.reason.strip(),
            after={'mention_id': body.mention_id, 'doc_id': result.get('doc_id'), 'action': body.action, 'status': result.get('status'),
                   'pid': result.get('pid'), 'person_deleted': bool(result.get('person_deleted'))})
    return result
