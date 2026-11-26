"""로컬 관리자 인증·기동·제어·진행 상태 회귀 테스트."""
from pathlib import Path
import contextlib
import importlib.util
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from server_admin import __main__ as launcher
import os
import itertools
from server_admin import operations as op
from server_admin import proxy
import http.cookiejar
import socket
import subprocess
import time
import urllib.request
import urllib.error
from server_admin.security import REPO
from server_admin.security import make_password, verify_password, Security, IDLE_SECONDS

def result(code=0, text=''):
    return SimpleNamespace(returncode=code, stdout=text, stderr='')

def container(status='running', port=5433, image='pgvector/pgvector:pg16'):
    return '|'.join((json.dumps(value) for value in (image, status, {'5432/tcp': [{'HostPort': str(port)}]})))

class LauncherTest(unittest.TestCase):

    def test_child_error_is_shown_without_settings_secrets(self):
        fake=SimpleNamespace(returncode=1,stdout='',stderr='ERROR:__main__: DB 준비 실패: fixture-private-password')
        with mock.patch.object(launcher,'command',return_value=fake),mock.patch('dotenv.dotenv_values',return_value={'MIGRATION_PASSWORD':'fixture-private-password'}):
            with self.assertRaises(launcher.StartError) as error:
                launcher.run_python(['-m','starter.migrate'],'DB 준비')
        self.assertIn('DB 준비 실패',str(error.exception))
        self.assertIn('[가림]',str(error.exception))
        self.assertNotIn('fixture-private-password',str(error.exception))

    def test_setup_precedes_version_upgrade(self):
        calls=[]
        with contextlib.ExitStack() as stack:
            for name,value in [('environment',{'DB_PORT':'5433'}),('prepare_private_dir',None),('docker_executable','docker'),('ensure_docker',None),('ensure_database',None),('wait_database',None),('prepare_accounts',None),('ensure_manager',True)]:
                stack.enter_context(mock.patch.object(launcher,name,return_value=value))
            stack.enter_context(mock.patch.object(launcher,'launch_lock',return_value=contextlib.nullcontext()))
            stack.enter_context(mock.patch.object(launcher,'run_python',side_effect=lambda args,*a,**kw:calls.append(args)))
            launcher.launch(open_browser=False)
        self.assertEqual(calls,[['-m','starter.migrate','--prepare'],['-m','starter.migrate']])


    def test_running_db_is_preserved_without_compose(self):
        with mock.patch.object(launcher, 'command', return_value=result(text=container())) as run:
            launcher.ensure_database('docker', {'DB_PORT': '5433'})
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('compose', run.call_args.args[0])

    def test_stopped_db_is_started_without_recreation(self):
        with mock.patch.object(launcher, 'command', side_effect=[result(text=container('exited')), result()]) as run:
            launcher.ensure_database('docker', {'DB_PORT': '5433'})
        self.assertEqual(run.call_args.args[0], ['docker', 'start', 'pii_rag_db'])

    def test_new_db_uses_compose_up_only(self):
        with mock.patch.object(launcher, 'command', side_effect=[result(1), result(), result()]) as run:
            launcher.ensure_database('docker', {'DB_PORT': '5433'})
        args = run.call_args.args[0]
        self.assertEqual(args[-3:], ['up', '-d', 'db'])
        self.assertTrue(all((not any((x in call.args[0] for x in ['down', 'rm', '--volumes'])) for call in run.call_args_list)))

    def test_unknown_existing_container_is_not_replaced(self):
        with mock.patch.object(launcher, 'command', side_effect=[result(1), result(text='pii_rag_db\n')]) as run:
            with self.assertRaises(launcher.StartError):
                launcher.ensure_database('docker', {'DB_PORT': '5433'})
        self.assertEqual(run.call_count, 2)

    def test_wrong_image_or_port_never_starts_or_recreates(self):
        for value in [container(image='unrelated:latest'), container('exited', port=5434), container(port=5434)]:
            with mock.patch.object(launcher, 'command', return_value=result(text=value)) as run:
                with self.assertRaises(launcher.StartError):
                    launcher.ensure_database('docker', {'DB_PORT': '5433'})
            self.assertEqual(run.call_count, 1)

    def test_running_manager_is_reused(self):
        with mock.patch.object(launcher, 'existing_manager', return_value=True), mock.patch.object(launcher, 'manager_ready', return_value=True), mock.patch.object(launcher.subprocess, 'Popen') as spawn:
            self.assertTrue(launcher.ensure_manager())
        spawn.assert_not_called()

    def test_remote_db_and_bad_port_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('placeholder', encoding='utf-8')
            values = {name: 'testing-password-only' for name in ['POSTGRES_PASSWORD', 'RAG_PASSWORD', 'VAULT_PASSWORD', 'MIGRATION_PASSWORD']}
            for override in [{'DB_HOST': 'remote.example'}, {'DB_PORT': 'invalid'}]:
                with mock.patch.object(launcher, 'REPO', root), mock.patch('dotenv.dotenv_values', return_value={**values, **override}):
                    with self.assertRaises(launcher.StartError):
                        launcher.environment()

    def test_new_db_refuses_example_password_but_existing_db_is_preserved(self):
        config = {'DB_PORT': '5433', 'RAG_PASSWORD': 'change-me-rag'}
        with mock.patch.object(launcher, 'command', side_effect=[result(1), result()]) as run:
            with self.assertRaises(launcher.StartError):
                launcher.ensure_database('docker', config)
        self.assertEqual(run.call_count, 2)
        with mock.patch.object(launcher, 'command', return_value=result(text=container())) as run:
            launcher.ensure_database('docker', config)
        self.assertEqual(run.call_count, 1)

    def test_pipeline_order_and_no_browser_mode(self):
        stages = []
        with contextlib.ExitStack() as stack:
            for name, value in [('environment', {'DB_PORT': '5433'}), ('prepare_private_dir', None), ('docker_executable', 'docker'), ('ensure_docker', None), ('ensure_database', None), ('wait_database', None), ('run_python', None), ('prepare_accounts', None), ('ensure_manager', True)]:
                stack.enter_context(mock.patch.object(launcher, name, side_effect=lambda *args, n=name, v=value, **kwargs: (stages.append(n), v)[1]))
            stack.enter_context(mock.patch.object(launcher, 'launch_lock', return_value=contextlib.nullcontext()))
            browser = stack.enter_context(mock.patch.object(launcher.webbrowser, 'open'))
            self.assertTrue(launcher.launch(open_browser=False)['manager_reused'])
            browser.assert_not_called()
        self.assertLess(stages.index('wait_database'), stages.index('run_python'))
        self.assertLess(stages.index('run_python'), stages.index('ensure_manager'))

    def test_failure_stops_before_manager_start(self):
        with mock.patch.object(launcher, 'environment', side_effect=launcher.StartError('설정 오류')), mock.patch.object(launcher, 'ensure_manager') as start:
            with self.assertRaises(launcher.StartError):
                launcher.launch(open_browser=False)
        start.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows Docker Desktop 경로 검사')
    def test_user_installation_of_docker_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'resources/bin').mkdir(parents=True)
            (base/'Docker Desktop.exe').write_bytes(b'')
            with mock.patch.object(launcher.shutil,'which',return_value=str(base/'resources/bin/docker.exe')),mock.patch.object(launcher.subprocess,'Popen') as spawn:
                launcher.start_desktop()
            self.assertEqual(spawn.call_args.args[0],[str(base/'Docker Desktop.exe')])


class DataVersionAdminTest(unittest.TestCase):
    """데이터 버전(HMAC 키 교체) 관리: 키 보관·서버별 환경·.env 갱신 규칙 (DB 없이)."""
    def setUp(self):
        from server_admin import data_versions as dvm
        self.dvm = dvm
        self.directory = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {'SERVER_ADMIN_STATE_DIR': self.directory.name})
        self.environment.start()
        op.settings()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_node_env_needs_stored_key(self):
        dvm = self.dvm
        self.assertEqual(dvm.node_env('node1'), {})
        dvm._assign('node1', 'v2')
        with self.assertRaises(dvm.DataVersionUIError):
            dvm.node_env('node1')
        dvm._save_key('v2', 'k' * 40)
        self.assertEqual(dvm.node_env('node1'), {'DATA_VERSION': 'v2', 'HMAC_KEY': 'k' * 40})
        self.assertEqual(dvm.node_env('node2'), {})

    def test_key_rules(self):
        dvm = self.dvm
        self.assertEqual(dvm.check_key('A' * 32, 'A' * 32), 'A' * 32)
        for key, confirm in (('short', 'short'), ('A' * 31 + ' ', 'A' * 31 + ' '), ('A' * 32, 'B' * 32), ('A' * 30 + '\nX=1', 'A' * 30 + '\nX=1')):
            with self.assertRaises(dvm.DataVersionUIError):
                dvm.check_key(key, confirm)

    def test_rewrite_env_keeps_other_lines(self):
        path = Path(self.directory.name) / 'sample.env'
        path.write_text('# 설명\nPOSTGRES_DB=pii_rag\nHMAC_KEY=old\n# HMAC_KEY=주석은 그대로\nNEW_HMAC_KEY=new\n', encoding='utf-8')
        self.dvm.rewrite_env(path, {'HMAC_KEY': 'new', 'DATA_VERSION': 'v2'}, remove={'NEW_HMAC_KEY'})
        self.assertEqual(path.read_text(encoding='utf-8').splitlines(),
                         ['# 설명', 'POSTGRES_DB=pii_rag', 'HMAC_KEY=new', '# HMAC_KEY=주석은 그대로', 'DATA_VERSION=v2'])

    def test_next_version(self):
        self.assertEqual(self.dvm.next_version([{'version': 'v1'}, {'version': 'v2'}]), 'v3')
        self.assertEqual(self.dvm.next_version([]), 'v2')

    def test_start_process_gives_node_its_version_only(self):
        dvm = self.dvm
        dvm._assign('node1', 'v2'); dvm._save_key('v2', 'k' * 40)
        seen = {}
        def spawn(args, **kwargs):
            seen[args[-3] if '--port' not in args else args[args.index('--port') + 1]] = kwargs.get('env')
            return SimpleNamespace(pid=4321)
        with mock.patch.object(op, 'listening', return_value=False), mock.patch.object(op, 'healthy', return_value=True), \
                mock.patch.object(op.subprocess, 'Popen', side_effect=spawn), \
                mock.patch.object(op.psutil, 'Process', return_value=SimpleNamespace(create_time=lambda: 1.0)):
            op.start_process('node1'); op.start_process('node2')
        self.assertEqual((seen['8001']['HMAC_KEY'], seen['8001']['DATA_VERSION']), ('k' * 40, 'v2'))
        self.assertIsNone(seen['8002'])   # 지정이 없는 서버는 .env 를 그대로 따름

    def test_promote_and_retire_need_all_nodes(self):
        dvm = self.dvm
        with mock.patch.object(dvm, 'running_version', return_value='v1'), mock.patch.object(dvm, 'env_default_version', return_value='v1'):
            with self.assertRaises(dvm.DataVersionUIError):
                dvm.promote('v2')
            with self.assertRaises(dvm.DataVersionUIError):
                dvm.retire('v1')

    def test_drop_needs_typed_name_and_recent_backup(self):
        dvm = self.dvm
        with self.assertRaises(dvm.DataVersionUIError):
            dvm.drop('v1', 'v2')
        with mock.patch.object(dvm, 'last_backup', return_value={'age_hours': 30}):
            with self.assertRaises(dvm.DataVersionUIError):
                dvm.drop('v1', 'v1')


class MonitoringTest(unittest.TestCase):
    """분산기 요청 기록 → 서버별 집계 (질문 내용·쿼리는 기록하지 않음)."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {'SERVER_ADMIN_STATE_DIR': self.directory.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_record_and_summary(self):
        from server_admin import monitoring
        started = time.monotonic()
        proxy.record('node1', 'v1', 'api/chat', 'POST', 200, started)
        proxy.record('node2', 'v2', 'chat', 'GET', 200, started)
        proxy.record('node2', None, 'api/chat', 'POST', 0, started)
        proxy.record(None, None, 'api/info', 'GET', 503, started)
        line = (Path(self.directory.name) / proxy.REQUEST_LOG).read_text(encoding='utf-8').splitlines()[0]
        self.assertEqual(set(json.loads(line)), {'t', 'node', 'version', 'kind', 'method', 'status', 'ms'})
        result = monitoring.summary(15)
        self.assertEqual(result['total'], 4)
        self.assertEqual((result['nodes']['node1']['requests'], result['nodes']['node1']['chat']), (1, 1))
        self.assertEqual((result['nodes']['node2']['requests'], result['nodes']['node2']['errors']), (2, 1))
        self.assertEqual(result['nodes']['none']['errors'], 1)
        self.assertEqual(result['versions_seen'], ['v1', 'v2'])
        self.assertEqual(sum(result['errors']), 2)
        self.assertEqual(proxy.kind_of('admin'), 'admin')
        self.assertEqual(proxy.kind_of('assets/x.js'), 'page')


class OperationSafetyTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {'SERVER_ADMIN_STATE_DIR': self.directory.name})
        self.environment.start()
        op.settings()
        with proxy.LOCK:
            proxy.DRAINED.clear()
            for node in op.NODES:
                proxy.CURRENT[node] = 0

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_only_registered_actions(self):
        for target, action, confirm in [('unknown', 'stop', 'unknown:stop'), ('node1', 'shell', 'node1:shell'), ('node1', 'stop', 'wrong')]:
            with self.assertRaises(op.OperationError):
                op.control(target, action, confirm)

    def test_last_healthy_server_is_protected(self):
        with mock.patch.object(op, 'healthy', return_value=False):
            with self.assertRaises(op.OperationError):
                op.set_routing('node1', enabled=False)
        self.assertTrue(op.settings()['nodes']['node1']['enabled'])

    def test_unhealthy_server_cannot_receive_traffic(self):
        config = op.settings()
        config['nodes']['node1']['enabled'] = False
        op.write_private('operations.json', config)
        with mock.patch.object(op, 'healthy', return_value=False):
            with self.assertRaises(op.OperationError):
                op.set_routing('node1', enabled=True)

    def test_unknown_process_is_never_stopped(self):
        with mock.patch.object(op, 'managed_process', return_value=None), mock.patch.object(op, 'listening', return_value=True), mock.patch.object(op, 'drain') as drain:
            with self.assertRaises(op.OperationError):
                op.control('node1', 'stop', 'node1:stop')
            drain.assert_not_called()

    def test_weight_bounds_and_type(self):
        for value in (-1, 101, True, '50', None):
            with self.assertRaises(op.OperationError):
                op.control('node1', 'weight', 'node1:weight', value)

    def test_drain_connection_failure_blocks_stop(self):
        config = op.settings()
        config['nodes']['node1']['enabled'] = False
        op.write_private('operations.json', config)
        with mock.patch.object(op, 'proxy_status', return_value={'running': True, 'nodes': {}}), mock.patch.object(op, 'local_json', return_value=None):
            with self.assertRaises(op.OperationError):
                op.drain('node1')

    def test_restart_order_and_previous_routing(self):
        calls = []
        with mock.patch.object(op, 'managed_process', return_value=object()), mock.patch.object(op, 'drain', side_effect=lambda n: calls.append('drain')), mock.patch.object(op, 'stop_process', side_effect=lambda n: calls.append('stop')), mock.patch.object(op, 'start_process', side_effect=lambda n: calls.append('start')), mock.patch.object(op, 'set_routing', side_effect=lambda n, **kw: calls.append('include')):
            op.control('node1', 'restart', 'node1:restart')
        self.assertEqual(calls, ['drain', 'stop', 'start', 'include'])

    def test_active_requests_prevent_stop(self):
        config = op.settings()
        config['nodes']['node1']['enabled'] = False
        op.write_private('operations.json', config)
        with mock.patch.object(op, 'managed_process', return_value=object()), mock.patch.object(op, 'proxy_status', return_value={'running': True, 'nodes': {'node1': {'active': 1}}}), mock.patch.object(op, 'local_json', return_value={'ok': True}), mock.patch.object(op.time, 'monotonic', side_effect=itertools.count(0, 1000)), mock.patch.object(op, 'stop_process') as stop:
            with self.assertRaises(op.OperationError):
                op.control('node1', 'stop', 'node1:stop')
        stop.assert_not_called()
        self.assertFalse(op.settings()['nodes']['node1']['enabled'])
        self.assertEqual(op.progress.snapshot()['phase'], 'deferred')

    def test_default_wait_and_saved_configuration(self):
        self.assertEqual(op.settings().get('drain_timeout_seconds', op.DEFAULT_DRAIN_TIMEOUT), 120)
        self.assertEqual(op.update_drain_timeout(60)['drain_timeout_seconds'], 60)
        self.assertEqual(op.settings()['drain_timeout_seconds'], 60)
        for value in [14, 121, True, '60', None]:
            with self.assertRaises(op.OperationError):
                op.update_drain_timeout(value)

    def test_progress_read_and_duplicate_rejection_while_job_waits(self):
        import threading
        entered = threading.Event()
        release = threading.Event()

        def work(*args):
            op.progress.update('waiting', '요청 대기', active_requests=1, drain_limit_seconds=120)
            entered.set()
            release.wait(timeout=3)
            return {'ok': True}
        with mock.patch.object(op, '_control', side_effect=work):
            thread = threading.Thread(target=op.control, args=('node1', 'restart', 'node1:restart'))
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                state = op.progress.snapshot()
                self.assertTrue(state['busy'])
                self.assertEqual(state['active_requests'], 1)
                self.assertEqual(state['phase'], 'waiting')
                self.assertGreater(state['remaining_seconds'], 0)
                with self.assertRaises(op.OperationError):
                    op.control('node2', 'restart', 'node2:restart')
                with self.assertRaises(op.OperationError):
                    op.update_drain_timeout(30)
            finally:
                release.set()
                thread.join(timeout=3)
        self.assertFalse(op.progress.snapshot()['busy'])
        self.assertEqual(op.progress.snapshot()['phase'], 'completed')

    def test_drain_completes_before_process_restart(self):
        config = op.settings()
        config['nodes']['node1']['enabled'] = False
        op.write_private('operations.json', config)
        responses = iter([{'running': True, 'nodes': {'node1': {'active': 1}}}, {'running': True, 'nodes': {'node1': {'active': 1}}}, {'running': True, 'nodes': {'node1': {'active': 0}}}])
        calls = []
        with mock.patch.object(op, 'managed_process', return_value=object()), mock.patch.object(op, 'proxy_status', side_effect=lambda: next(responses)), mock.patch.object(op, 'local_json', return_value={'ok': True}), mock.patch.object(op, 'stop_process', side_effect=lambda node: calls.append('stop')), mock.patch.object(op, 'start_process', side_effect=lambda node: calls.append('start')), mock.patch.object(op.time, 'sleep'):
            op.control('node1', 'restart', 'node1:restart')
        self.assertEqual(calls, ['stop', 'start'])
        self.assertEqual(op.progress.snapshot()['active_requests'], 0)

    def test_post_connection_failure_is_not_retried(self):
        import requests
        with mock.patch.object(proxy, 'healthy', return_value=True), mock.patch.object(proxy.requests, 'Session') as factory:
            client = factory.return_value.__enter__.return_value
            client.request.side_effect = requests.ConnectionError()
            result = proxy.forward('POST', 'api/chat', '', b'{}', {})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(client.request.call_count, 1)
        self.assertTrue(all((c['active'] == 0 for c in proxy.COUNTS.values())))

    def test_weighted_selection_is_actual_ratio(self):
        picks = [proxy.choose(['node1', 'node2'], {'node1': 75, 'node2': 25}) for _ in range(40)]
        self.assertEqual(picks.count('node1'), 30)
        self.assertEqual(picks.count('node2'), 10)

    def test_disabled_or_failed_nodes_are_not_selected(self):
        with mock.patch.object(proxy, 'healthy', return_value=False):
            result = proxy.forward('POST', 'api/chat', '', b'{}', {})
        self.assertEqual(result.status_code, 503)

class AdminIntegrationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='rag-admin-test-')
        cls.environment = os.environ.copy()
        cls.environment['SERVER_ADMIN_STATE_DIR'] = cls.directory.name
        cls.environment['PYTHONIOENCODING'] = 'utf-8'
        cls.original_state = os.environ.get('SERVER_ADMIN_STATE_DIR')
        os.environ['SERVER_ADMIN_STATE_DIR'] = cls.directory.name
        subprocess.run([sys.executable, '-B', '-m', 'server_admin', '--setup', '--generate'], cwd=REPO, env=cls.environment, capture_output=True, check=True)
        initial = (Path(cls.directory.name) / 'initial-login.txt').read_text(encoding='utf-8')
        cls.password = next((line.split(': ', 1)[1] for line in initial.splitlines() if line.startswith('비밀번호: ')))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.logfile = tempfile.TemporaryFile()
        cls.process = None
        cls.start_server()

    @classmethod
    def start_server(cls):
        cls.process = subprocess.Popen([sys.executable, '-B', '-m', 'server_admin', '--port', str(cls.port)], cwd=REPO, env=cls.environment, stdout=cls.logfile, stderr=cls.logfile, **{'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise RuntimeError('검증용 관리 프로그램 실행 실패')
            try:
                with urllib.request.urlopen(cls.base + '/', timeout=1) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError('검증용 관리 프로그램 실행 제한 시간 초과')

    @classmethod
    def tearDownClass(cls):
        if cls.process and cls.process.poll() is None:
            cls.process.terminate()
            cls.process.wait(timeout=10)
        cls.logfile.close()
        if cls.original_state is None:
            os.environ.pop('SERVER_ADMIN_STATE_DIR', None)
        else:
            os.environ['SERVER_ADMIN_STATE_DIR'] = cls.original_state
        cls.directory.cleanup()

    def setUp(self):
        self.jar = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path, body=None, headers=None):
        defaults = {}
        if body is not None:
            defaults = {'Content-Type': 'application/json', 'Origin': self.base, 'X-Admin-Request': '1'}
        defaults.update(headers or {})
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=defaults)
        try:
            response = self.client.open(request, timeout=8)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read().decode()
        status, response_headers = (response.status, response.headers)
        response.close()
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        return (status, value, response_headers)

    def login(self, password=None):
        status, _, headers = self.request('/api/login', {'username': 'serveradmin', 'password': password or self.password})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=strict', headers['Set-Cookie'])
        status, data, _ = self.request('/api/session')
        self.assertEqual(status, 200)
        return data['csrf']

    def test_01_no_anonymous_data(self):
        for path in ['/api/session', '/api/overview', '/api/operations', '/api/control/status']:
            self.assertEqual(self.request(path)[0], 401)
        status, html, headers = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request('/docs')[0], 404)
        self.assertEqual(self.request('/openapi.json')[0], 404)
        self.assertEqual(self.request('/assets/auth.json')[0], 404)

    def test_02_host_origin_and_body_limits(self):
        self.assertEqual(self.request('/', headers={'Host': 'evil.example'})[0], 403)
        data = {'username': 'serveradmin', 'password': self.password}
        self.assertEqual(self.request('/api/login', data, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/login', data, {'Origin': 'null'})[0], 403)
        self.assertEqual(self.request('/api/login', data, {'X-Admin-Request': ''})[0], 403)
        self.assertEqual(self.request('/api/login', {'padding': 'x' * 5000})[0], 413)

    def test_03_login_overview_and_csrf(self):
        csrf = self.login()
        status, data, _ = self.request('/api/overview')
        self.assertEqual(status, 200)
        self.assertIn(data['database']['state'], ['unconfigured', 'ok', 'unavailable'])
        raw = json.dumps(data)
        for secret in [self.password, 'pbkdf2_sha256', 'password=', 'POSTGRES_PASSWORD', 'masked_text', 'token_map']:
            self.assertNotIn(secret, raw)
        self.assertEqual(self.request('/api/logout', {})[0], 403)
        self.assertEqual(self.request('/api/control', {'target': 'node1', 'action': 'stop', 'confirmation': 'node1:stop'})[0], 403)
        self.assertEqual(self.request('/api/control', {'target': 'unknown', 'action': 'stop', 'confirmation': 'unknown:stop'}, {'X-CSRF-Token': csrf})[0], 409)
        self.assertEqual(self.request('/api/operations/settings', {'drain_timeout_seconds': 60})[0], 403)
        self.assertEqual(self.request('/api/operations/settings', {'drain_timeout_seconds': 60}, {'X-CSRF-Token': csrf})[0], 200)
        status, operations, _ = self.request('/api/operations')
        self.assertEqual(operations['drain_timeout_seconds'], 60)
        self.assertEqual(self.request('/api/logout', {}, {'X-CSRF-Token': 'invalid'})[0], 403)
        self.assertEqual(self.request('/api/logout', {}, {'X-CSRF-Token': csrf})[0], 200)
        self.assertEqual(self.request('/api/session')[0], 401)

    def test_04_password_and_session_expiry(self):
        record = make_password('valid-password-for-test')
        self.assertTrue(verify_password('valid-password-for-test', record))
        self.assertFalse(verify_password('invalid', record))
        self.assertNotIn('valid-password-for-test', json.dumps(record))
        security = Security()
        token, result = security.login('serveradmin', self.password)
        self.assertEqual(result, 'ok')
        security.sessions[token]['last'] = time.time() - IDLE_SECONDS - 1
        self.assertIsNone(security.session(token))

    def test_04b_status_poll_does_not_extend_session(self):
        security = Security()
        token, _ = security.login('serveradmin', self.password)
        security.sessions[token]['last'] = time.time() - 30
        before = security.sessions[token]['last']
        self.assertIsNotNone(security.session(token, touch=False))
        self.assertEqual(before, security.sessions[token]['last'])
        security.session(token)
        self.assertGreater(security.sessions[token]['last'], before)

    def test_05_password_change_revokes_sessions(self):
        csrf = self.login()
        self.assertEqual(self.request('/api/password', {'current': 'invalid', 'new': 'new-password-for-test'}, {'X-CSRF-Token': csrf})[0], 400)
        self.assertEqual(self.request('/api/password', {'current': self.password, 'new': 'short'}, {'X-CSRF-Token': csrf})[0], 400)
        status, _, _ = self.request('/api/password', {'current': self.password, 'new': 'new-password-for-test'}, {'X-CSRF-Token': csrf})
        self.assertEqual(status, 200)
        self.assertFalse((Path(self.directory.name) / 'initial-login.txt').exists())
        self.assertEqual(self.request('/api/session')[0], 401)
        type(self).password = 'new-password-for-test'
        self.login()

    def test_06_credentials_cannot_be_inside_repo(self):
        from server_admin.security import state_dir
        with mock.patch.dict(os.environ, {'SERVER_ADMIN_STATE_DIR': str(REPO / 'private')}):
            with self.assertRaises(RuntimeError):
                state_dir()

    def test_07_rate_limit_survives_restart(self):
        for _ in range(5):
            self.assertEqual(self.request('/api/login', {'username': 'serveradmin', 'password': 'wrong-password'})[0], 401)
        self.assertEqual(self.request('/api/login', {'username': 'serveradmin', 'password': self.password})[0], 429)
        self.process.terminate()
        self.process.wait(timeout=10)
        type(self).start_server()
        self.assertEqual(self.request('/api/login', {'username': 'serveradmin', 'password': self.password})[0], 429)
        audit = (Path(self.directory.name) / 'audit.jsonl').read_text(encoding='utf-8')
        self.assertNotIn(self.password, audit)
        self.assertNotIn('wrong-password', audit)

if __name__=='__main__':unittest.main()
