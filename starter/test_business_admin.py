"""기존 DB는 롤백으로 보존하면서 인증·권한·저장·검색 필터를 검증합니다."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch, Mock

from fastapi import HTTPException
from starlette.requests import Request
from starter import business_admin as b
from starter import search


class BusinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = b.connection()
        cls.outer = cls.conn.transaction(force_rollback=True)
        cls.outer.__enter__()
        cls.conn.execute('SELECT pg_advisory_xact_lock(%s)', (b.LOCK_ID,))
        cls.suffix = __import__('secrets').token_hex(5)
        cls.admin_id = cls.conn.execute("""INSERT INTO auth.user_info(login_id,password_hash,role_code,must_change_password)
            VALUES (%s,%s,'ADMIN',false) RETURNING user_id""", ('test-' + cls.suffix, b.password_hash('검증용비밀번호_충분한길이123'))).fetchone()['user_id']

        @contextmanager
        def scoped():
            with cls.conn.transaction():
                yield cls.conn
        cls.patcher = patch.object(b, 'connection', scoped)
        cls.patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()
        try:
            cls.outer.__exit__(None, None, None)
        finally:
            cls.conn.close()

    def request(self, uid=None, method='POST', csrf=True):
        uid = uid or self.admin_id
        row = self.conn.execute('SELECT user_id,updated_at FROM auth.user_info WHERE user_id=%s', (uid,)).fetchone()
        cookie = b.pack({'uid': uid, 'version': str(row['updated_at']), 'csrf': 'test-csrf', 'exp': __import__('time').time() + 1000})
        headers = [(b'cookie', (b.COOKIE + '=' + cookie).encode()), (b'origin', b'http://127.0.0.1:8080')]
        if csrf:
            headers.append((b'x-csrf-token', b'test-csrf'))
        return Request({'type': 'http', 'method': method, 'path': '/', 'headers': headers})

    def test_01_signature_tamper_and_expiry(self):
        token = b.pack({'exp': 0})
        with self.assertRaises(HTTPException):
            b.unpack(token)
        token = b.pack({'exp': __import__('time').time() + 1000})
        with self.assertRaises(HTTPException):
            b.unpack(token[:-1] + ('0' if token[-1] != '0' else '1'))

    def test_02_anonymous_and_csrf_denied(self):
        with self.assertRaises(HTTPException):
            b.data(Request({'type': 'http', 'method': 'GET', 'headers': []}))
        with self.assertRaises(HTTPException) as error:
            b.current(self.request(csrf=False), self.conn)
        self.assertEqual(error.exception.status_code, 403)

    def test_03_team_saved_and_conflict_detected(self):
        key = 'test-team-' + self.suffix
        b.save(b.Edit(kind='teams', key=key, values={'team_name': key, 'is_active': True}, reason='팀 등록 검증'), self.request())
        row = self.conn.execute('SELECT * FROM auth.team_info WHERE team_id=%s', (key,)).fetchone()
        self.assertEqual(row['team_name'], key)
        with self.assertRaises(HTTPException) as error:
            b.save(b.Edit(kind='teams', key=key, values={'is_active': False}, reason='동시 수정 검증', version='오래된 버전'), self.request())
        self.assertEqual(error.exception.status_code, 409)

    def test_04_self_lockout_is_rejected(self):
        row = self.conn.execute('SELECT updated_at FROM auth.user_info WHERE user_id=%s', (self.admin_id,)).fetchone()
        with self.assertRaises(HTTPException):
            b.save(b.Edit(kind='users', key=str(self.admin_id), values={'status': 'LOCKED'}, reason='본인 잠금 검증', version=row['updated_at'].isoformat()), self.request())

    def test_05_create_and_password_are_not_logged(self):
        name = 'test-new-' + self.suffix
        result = b.create_user(b.CreateUser(login_id=name, role_code='ADMIN', reason='계정 발급 검증'), self.request())
        temporary = json.loads(result.body)['temporary_password']
        user = self.conn.execute('SELECT * FROM auth.user_info WHERE login_id=%s', (name,)).fetchone()
        self.assertTrue(b.password_matches(temporary, user['password_hash']))
        self.assertTrue(user['must_change_password'])
        logs = self.conn.execute('SELECT before_value,after_value FROM auth.permission_change_log WHERE target_user_id=%s', (user['user_id'],)).fetchall()
        self.assertNotIn(temporary, json.dumps(logs))
        self.assertNotIn('password_hash', json.dumps(logs))
        response = b.reset_password(user['user_id'], b.ResetPassword(reason='초기 비밀번호 재발급 검증'), self.request())
        replacement = json.loads(response.body)['temporary_password']
        encoded = self.conn.execute('SELECT password_hash FROM auth.user_info WHERE user_id=%s', (user['user_id'],)).fetchone()['password_hash']
        self.assertTrue(b.password_matches(replacement, encoded))
        self.assertFalse(b.password_matches(temporary, encoded))

    def test_06_employee_confirmation_without_name_token(self):
        team = 'employee-team-' + self.suffix
        emp = 'employee-' + self.suffix
        b.save(b.Edit(kind='teams',key=team,values={'team_name':team},reason='직원 팀 등록 검증'),self.request())
        with self.assertRaises(HTTPException):
            b.save(b.Edit(kind='employees', key=emp, values={'team_id':team,'verification_status':'CONFIRMED'}, reason='직책 필수 검증'), self.request())
        b.save(b.Edit(kind='employees',key=emp,values={'team_id':team,'position_code':'사원','verification_status':'CONFIRMED'},reason='이름 토큰 없는 직원 확정'),self.request())
        row = self.conn.execute('SELECT * FROM auth.employee_info WHERE employee_id=%s',(emp,)).fetchone()
        self.assertIsNone(row['name_token'])
        self.assertEqual(row['verification_status'],'CONFIRMED')
        with self.assertRaises(HTTPException):
            b.clean_values('employees',{'name_token':'<PERSON_test>'})
        self.assertNotIn('name_token', b.data(self.request(method='GET'))['employees'][0])

    def test_07_session_revocation(self):
        # now()는 같은 트랜잭션에서 고정되므로 실제 다른 요청의 시각 변경을 명시적으로 재현합니다.
        uid = self.conn.execute("SELECT user_id FROM auth.user_info WHERE login_id=%s", ('test-new-' + self.suffix,)).fetchone()['user_id']
        req = self.request(uid=uid)
        self.conn.execute("UPDATE auth.user_info SET status='LOCKED' WHERE user_id=%s", (uid,))
        with self.assertRaises(HTTPException) as error:
            b.current(req, self.conn, changing=True)
        self.assertEqual(error.exception.status_code, 401)

    def test_08_db_runtime_privileges(self):
        allowed = self.conn.execute("SELECT has_table_privilege(current_user,'auth.user_info','SELECT,INSERT,UPDATE') AS ok").fetchone()['ok']
        denied = self.conn.execute("SELECT has_schema_privilege(current_user,'vault','USAGE') AS ok").fetchone()['ok']
        self.assertTrue(allowed)
        self.assertFalse(denied)

    def test_09_forbidden_columns(self):
        with self.assertRaises(HTTPException):
            b.clean_values('users', {'password_hash': '바꾸기'})
        with self.assertRaises(HTTPException):
            b.clean_values('roles', {'can_manage_users': True})

    def test_10_all_modes_filter_before_limit(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, args):
                self.sql, self.args = sql, args
                self_case.assertIn('doc_id = ANY(%s::text[])', sql)
                self_case.assertLess(sql.index('doc_id = ANY'), sql.index('LIMIT'))
                self_case.assertIn(['allowed-doc'], args)
            def fetchall(self): return []
        self_case = self
        conn = Mock()
        conn.cursor.side_effect = lambda: Cursor()
        with patch.object(search, 'mask_query', return_value=('가명 질문', ['<PERSON_test>'])), patch.object(search, 'embed', return_value=[[0.1]]):
            for mode in ('v1', 'v2', 'v3', 'v4'):
                search.search('검증', conn=conn, mode=mode, allowed_doc_ids=['allowed-doc'])

    def test_11_empty_scope_never_calls_external_api(self):
        with patch.object(search, 'embed') as embed:
            self.assertEqual(search.search('검증', allowed_doc_ids=[]), ('', [], []))
            embed.assert_not_called()

    def test_12_origin_must_match_exact_port(self):
        request = Request({'type': 'http', 'headers': [(b'origin', b'http://127.0.0.1:9999')]})
        with self.assertRaises(HTTPException):
            b.login_origin(request)

    def test_13_login_failure_persists_without_password(self):
        name = 'test-' + self.suffix
        request = Request({'type': 'http', 'method': 'POST', 'headers': [(b'origin', b'http://127.0.0.1:8080')]})
        result = b.login(b.Login(login_id=name, password='틀린 비밀번호'), request)
        self.assertEqual(result.status_code, 401)
        log = self.conn.execute("SELECT action FROM auth.permission_change_log WHERE target_user_id=%s AND action='LOGIN_FAILED'", (self.admin_id,)).fetchone()
        self.assertIsNotNone(log)

    def test_14_team_position_policy_controls_search_without_role_fallback(self):
        team = 'scope-team-' + self.suffix
        emp = 'scope-emp-' + self.suffix
        self.conn.execute('INSERT INTO auth.team_info(team_id,team_name) VALUES (%s,%s)', (team, team))
        self.conn.execute("INSERT INTO auth.employee_info(employee_id,team_id,position_code,verification_status) VALUES (%s,%s,'사원','CONFIRMED')", (emp,team))
        uid = self.conn.execute("INSERT INTO auth.user_info(login_id,password_hash,employee_id,role_code,must_change_password) VALUES (%s,%s,%s,'STAFF',false) RETURNING user_id", ('scope-user-' + self.suffix,b.password_hash('직원검증용비밀번호_12345'),emp)).fetchone()['user_id']
        with self.assertRaises(HTTPException) as error:
            b.data(self.request(uid,method='GET'))
        self.assertEqual(error.exception.status_code,403)
        self.assertEqual(b.allowed_documents(self.request(uid)),[])
        b.save_policy(b.TeamPositionPolicy(team_id=team,position_code='사원',search_scope='OWN',reason='사원 정책 등록 검증'),self.request())
        from starter import db
        rag=Mock()
        rag.__enter__=Mock(return_value=rag)
        rag.__exit__=Mock(return_value=False)
        rag.execute.return_value.fetchall.return_value=[('permitted-document',)]
        with patch.object(db,'rag_conn',return_value=rag):
            self.assertEqual(b.allowed_documents(self.request(uid)),['permitted-document'])
        self.assertIn('owner_employee_id=%s',rag.execute.call_args.args[0])
        self.assertEqual(rag.execute.call_args.args[1],(emp,))
        policy=self.conn.execute('SELECT * FROM auth.team_position_policy WHERE team_id=%s AND position_code=%s',(team,'사원')).fetchone()
        with self.assertRaises(HTTPException) as error:
            b.save_policy(b.TeamPositionPolicy(team_id=team,position_code='사원',search_scope='TEAM',reason='동시 수정 차단 검증',version='오래된 버전'),self.request())
        self.assertEqual(error.exception.status_code,409)
        b.save_policy(b.TeamPositionPolicy(team_id=team,position_code='사원',search_scope='TEAM',reason='팀 검색 정책 변경',version=policy['updated_at'].isoformat()),self.request())
        # 계정 역할은 STAFF 그대로 두어도 팀·직책 정책 변경이 즉시 적용됩니다.
        with patch.object(db,'rag_conn',return_value=rag):
            b.allowed_documents(self.request(uid))
        self.assertIn('team_id=%s',rag.execute.call_args.args[0])
        self.assertEqual(rag.execute.call_args.args[1],(team,))
        self.conn.execute("UPDATE auth.employee_info SET position_code='팀장' WHERE employee_id=%s",(emp,))
        self.assertEqual(b.allowed_documents(self.request(uid)),[])
        self.conn.execute("UPDATE auth.employee_info SET position_code='사원' WHERE employee_id=%s",(emp,))
        self.conn.execute('UPDATE auth.team_info SET is_active=false WHERE team_id=%s',(team,))
        self.assertEqual(b.allowed_documents(self.request(uid)),[])

    def test_15_password_change_and_attempt_limit(self):
        name = 'password-test-' + self.suffix
        uid = self.conn.execute("INSERT INTO auth.user_info(login_id,password_hash,role_code,must_change_password) VALUES (%s,%s,'ADMIN',false) RETURNING user_id", (name,b.password_hash('검증용비밀번호_충분한길이123'))).fetchone()['user_id']
        req = self.request(uid)
        result = b.change_password(b.ChangePassword(old_password='검증용비밀번호_충분한길이123', new_password='변경한비밀번호_충분한길이123'), req)
        self.assertIn('rag_business=', result.headers['set-cookie'])
        self.assertTrue(b.password_matches('변경한비밀번호_충분한길이123', self.conn.execute('SELECT password_hash FROM auth.user_info WHERE user_id=%s', (uid,)).fetchone()['password_hash']))
        # 단일 롤백 트랜잭션의 고정 now() 대신 실제 요청 간 시각 차이를 재현합니다.
        self.conn.execute("UPDATE auth.user_info SET password_changed_at=now()-interval '1 second' WHERE user_id=%s", (uid,))
        for _ in range(5):
            b.login(b.Login(login_id=name, password='틀린 비밀번호'), req)
        with self.assertRaises(HTTPException) as error:
            b.login(b.Login(login_id=name, password='변경한비밀번호_충분한길이123'), req)
        self.assertEqual(error.exception.status_code, 429)
        self.conn.execute('UPDATE auth.user_info SET password_changed_at=clock_timestamp() WHERE user_id=%s', (uid,))
        self.assertEqual(b.login(b.Login(login_id=name,password='변경한비밀번호_충분한길이123'),req).status_code,200)

    def test_16_document_confirmation_checks_employee_team(self):
        team = 'doc-team-' + self.suffix
        emp = 'doc-emp-' + self.suffix
        self.conn.execute('INSERT INTO auth.team_info(team_id,team_name) VALUES (%s,%s)', (team, team))
        self.conn.execute("INSERT INTO auth.employee_info(employee_id,position_code,team_id,verification_status) VALUES (%s,%s,%s,'CONFIRMED')", (emp, '사원', team))
        row = self.conn.execute('SELECT * FROM rag.document_info ORDER BY document_id LIMIT 1').fetchone()
        if not row:
            self.skipTest('검토할 기존 문서가 없습니다.')
        version = row['updated_at'].isoformat()
        with self.assertRaises(HTTPException):
            b.save(b.Edit(kind='documents', key=row['document_id'], version=version,
                values={'owner_employee_id': emp, 'team_id': None, 'metadata_status': 'CONFIRMED'}, reason='불일치 팀 차단 검증'), self.request())
        b.save(b.Edit(kind='documents', key=row['document_id'], version=version,
            values={'owner_employee_id': emp, 'team_id': team, 'metadata_status': 'CONFIRMED'}, reason='문서 확정 검증'), self.request())
        self.assertEqual(self.conn.execute('SELECT metadata_status FROM rag.document_info WHERE document_id=%s', (row['document_id'],)).fetchone()['metadata_status'], 'CONFIRMED')
        self.assertFalse(self.conn.execute("SELECT has_table_privilege(current_user,'rag.chunks','SELECT') AS ok").fetchone()['ok'])

    def test_17_real_http_login_change_data_and_logout(self):
        import socket
        import threading
        import time
        import requests
        import uvicorn
        from fastapi import FastAPI
        name = 'http-' + self.suffix
        password = '실제HTTP검증용비밀번호_12345'
        self.conn.execute("INSERT INTO auth.user_info(login_id,password_hash,role_code) VALUES (%s,%s,'ADMIN')", (name, b.password_hash(password)))
        app = FastAPI()
        app.include_router(b.router)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        config = uvicorn.Config(app, log_level='critical', access_log=False)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started)
            base = 'http://127.0.0.1:' + str(port)
            with requests.Session() as client:
                client.trust_env = False
                client.headers['Origin'] = 'http://127.0.0.1:8080'
                result = client.post(base + '/api/login', json={'login_id': name, 'password': password}, timeout=5)
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json()['must_change_password'])
                self.assertEqual(client.get(base + '/api/admin/data', timeout=5).status_code, 403)
                client.headers['X-CSRF-Token'] = client.get(base + '/api/me', timeout=5).json()['csrf']
                result = client.post(base + '/api/password', json={'old_password': password, 'new_password': '변경된HTTP검증용비밀번호_12345'}, timeout=5)
                self.assertEqual(result.status_code, 200)
                client.headers['X-CSRF-Token'] = client.get(base + '/api/me', timeout=5).json()['csrf']
                result = client.get(base + '/api/admin/data', timeout=5)
                self.assertEqual(result.status_code, 200)
                self.assertIn('documents', result.json())
                self.assertEqual(client.get(base + '/admin', timeout=5).status_code, 200)
                result = client.post(base + '/api/admin/save', json={'kind': 'teams', 'key': 'http-team-' + self.suffix, 'values': {'team_name': 'HTTP 검증 팀', 'is_active': True}, 'reason': '실제 HTTP 저장 검증'}, timeout=5)
                self.assertEqual(result.status_code, 200)
                result = client.post(base + '/api/admin/policies',json={'team_id':'http-team-'+self.suffix,'position_code':'사원','search_scope':'OWN','can_restore':False,'reason':'실제 HTTP 정책 저장 검증'},timeout=5)
                self.assertEqual(result.status_code,200)
                self.assertTrue(any(p['team_id']=='http-team-'+self.suffix for p in client.get(base+'/api/admin/data',timeout=5).json()['policies']))
                self.assertEqual(client.post(base + '/api/logout', json={}, timeout=5).status_code, 200)
                self.assertEqual(client.get(base + '/api/me', timeout=5).status_code, 401)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()

    def test_18_policy_same_position_different_teams_and_db_constraints(self):
        from psycopg import IntegrityError
        first='first-policy-'+self.suffix
        second='second-policy-'+self.suffix
        for team in (first,second):
            self.conn.execute('INSERT INTO auth.team_info(team_id,team_name) VALUES (%s,%s)',(team,team))
        for team,scope in ((first,'OWN'),(second,'TEAM')):
            b.save_policy(b.TeamPositionPolicy(team_id=team,position_code='사원',search_scope=scope,reason='팀별 직책 정책 분리'),self.request())
        rows=self.conn.execute('SELECT team_id,search_scope FROM auth.team_position_policy WHERE team_id=ANY(%s)',([first,second],)).fetchall()
        self.assertEqual({r['team_id']:r['search_scope'] for r in rows},{first:'OWN',second:'TEAM'})
        with self.assertRaises(IntegrityError):
            with self.conn.transaction():
                self.conn.execute("INSERT INTO auth.team_position_policy(team_id,position_code,search_scope) VALUES (%s,'사원','NONE')",(first,))
        with self.assertRaises(HTTPException):
            b.save_policy(b.TeamPositionPolicy(team_id=first,position_code='이사',search_scope='NONE',can_restore=True,reason='검색 불가 복원 차단'),self.request())
        with self.assertRaises(HTTPException):
            b.save_policy(b.TeamPositionPolicy(team_id='missing-'+self.suffix,position_code='사원',search_scope='OWN',reason='존재하지 않는 팀 차단'),self.request())
        with self.assertRaises(HTTPException):
            b.clean_values('roles',{'search_scope':'TEAM'})

    def test_19_invite_with_employee_number_only(self):
        from pydantic import ValidationError
        emp = 'inv-' + self.suffix
        # 1) 처음 보는 사번 → 확인 전 직원 + STAFF 계정, 임시 비밀번호는 응답에만
        result = json.loads(b.invite_member(b.InviteMember(employee_id=emp), self.request()).body)
        self.assertEqual(result['login_id'], emp)
        self.assertTrue(result['employee_created'])
        employee = self.conn.execute('SELECT * FROM auth.employee_info WHERE employee_id=%s', (emp,)).fetchone()
        self.assertEqual(employee['verification_status'], 'PENDING')
        self.assertIsNone(employee['team_id'])
        user = self.conn.execute('SELECT * FROM auth.user_info WHERE login_id=%s', (emp,)).fetchone()
        self.assertEqual((user['employee_id'], user['role_code'], user['status']), (emp, 'STAFF', 'ACTIVE'))
        self.assertTrue(user['must_change_password'])
        self.assertTrue(b.password_matches(result['temporary_password'], user['password_hash']))
        logs = self.conn.execute('''SELECT action,before_value,after_value FROM auth.permission_change_log
            WHERE target_user_id=%s OR after_value->>'key'=%s''', (user['user_id'], emp)).fetchall()
        self.assertEqual({l['action'] for l in logs}, {'USER_CREATED', 'EMPLOYEES_SAVED'})
        self.assertNotIn(result['temporary_password'], json.dumps(logs, default=str))
        # 2) 같은 사번 다시 초대 → 거절, 계정은 하나
        with self.assertRaises(HTTPException) as error:
            b.invite_member(b.InviteMember(employee_id=emp), self.request())
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM auth.user_info WHERE employee_id=%s', (emp,)).fetchone()['n'], 1)
        # 3) 이미 등록된 직원 → 직원 정보는 그대로 두고 계정만 발급
        team = 'inv-team-' + self.suffix
        known = 'inv-known-' + self.suffix
        b.save(b.Edit(kind='teams', key=team, values={'team_name': team}, reason='초대 팀 등록'), self.request())
        b.save(b.Edit(kind='employees', key=known, values={'team_id': team, 'position_code': '사원', 'verification_status': 'CONFIRMED'}, reason='초대 직원 확정'), self.request())
        result = json.loads(b.invite_member(b.InviteMember(employee_id=known), self.request()).body)
        self.assertFalse(result['employee_created'])
        self.assertEqual(result['employee_status'], 'CONFIRMED')
        self.assertEqual(self.conn.execute('SELECT team_id FROM auth.employee_info WHERE employee_id=%s', (known,)).fetchone()['team_id'], team)
        # 4) 사번 형식이 아니면 거절 (공백·한글·빈 값)
        for bad in ('', 'E 001', '사번001'):
            with self.assertRaises(ValidationError):
                b.InviteMember(employee_id=bad)
        # 5) 권한 확인: 확인 전 직원 계정은 검색 정책이 없어 NONE
        invited = self.conn.execute('SELECT user_id FROM auth.user_info WHERE login_id=%s', (emp,)).fetchone()['user_id']
        self.conn.execute('UPDATE auth.user_info SET must_change_password=false WHERE user_id=%s', (invited,))
        me, _ = b.current(self.request(invited, method='GET'), self.conn)
        self.assertEqual(me['search_scope'], 'NONE')
        # 6) 관리 권한 없는 계정은 초대할 수 없음
        with self.assertRaises(HTTPException) as error:
            b.invite_member(b.InviteMember(employee_id='inv-x-' + self.suffix), self.request(invited))
        self.assertEqual(error.exception.status_code, 403)



if __name__ == '__main__':
    unittest.main()
