"""로컬 기동 진입점. DB 데이터와 기존 프로세스를 보존하며 관리자만 준비합니다."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from .security import REPO, prepare_private_dir, read_private, state_dir

URL = 'http://127.0.0.1:9000'
CONTAINER = 'pii_rag_db'

class StartError(RuntimeError):
    pass

def command(args, *, timeout=30):
    try:
        return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    except (OSError,subprocess.TimeoutExpired):
        raise StartError('실행 도구가 응답하지 않습니다. 설치와 실행 상태를 확인하세요.') from None

def run_python(arguments, label, timeout=90):
    result=command([sys.executable,'-B',*arguments],timeout=timeout)
    if result.returncode:
        # 하위 도구의 원문 출력에는 연결 정보가 포함될 수 있으므로 표시하지 않습니다.
        raise StartError(label+'에 실패했습니다. 설정과 DB 권한을 확인하세요.')

def environment():
    from dotenv import dotenv_values
    path=REPO/'.env'
    if not path.is_file():
        raise StartError('.env가 없습니다. .env.example을 복사해 실제 설정을 먼저 입력하세요.')
    config=dotenv_values(path)
    required=('POSTGRES_PASSWORD','RAG_PASSWORD','VAULT_PASSWORD','MIGRATION_PASSWORD','HMAC_KEY')
    missing=[name for name in required if not config.get(name)]
    if missing:
        raise StartError('.env에 필요한 설정이 없습니다: '+', '.join(missing))
    if config.get('DB_HOST','localhost') not in ('localhost','127.0.0.1'):
        raise StartError('이 파일은 로컬 DB 기동용입니다. DB_HOST 설정을 확인하세요.')
    try:
        port=int(config.get('DB_PORT','5433'))
        if not 1<=port<=65535:raise ValueError()
    except (TypeError,ValueError):
        raise StartError('DB_PORT 설정이 올바르지 않습니다.') from None
    return config

def docker_executable():
    executable=shutil.which('docker')
    if executable:return executable
    if os.name=='nt':
        candidate=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Docker/Docker/resources/bin/docker.exe'
        if candidate.is_file():return str(candidate)
    raise StartError('Docker가 설치되어 있지 않거나 실행 경로를 찾지 못했습니다.')

def start_desktop():
    if os.name!='nt':
        raise StartError('Docker가 실행 중이 아닙니다. Docker 서비스를 먼저 실행하세요.')
    candidates=[Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Docker/Docker/Docker Desktop.exe',
        Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'Programs/DockerDesktop/Docker Desktop.exe']
    cli=shutil.which('docker')
    if cli:
        candidate=Path(cli).resolve()
        if len(candidate.parents)>=3:candidates.insert(0,candidate.parents[2]/'Docker Desktop.exe')
    executable=next((path for path in candidates if path.is_file()),None)
    if executable is None:
        raise StartError('Docker Desktop을 찾지 못했습니다. 설치와 실행 상태를 확인하세요.')
    startup=subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=subprocess.SW_HIDE
    subprocess.Popen([str(executable)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,startupinfo=startup,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

def ensure_docker(executable):
    def info():return command([executable,'info','--format','{{.OSType}}'],timeout=10)
    result=info()
    if result.returncode:
        print('  Docker Desktop을 실행하고 준비를 기다립니다.',flush=True)
        start_desktop()
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            time.sleep(2)
            result=info()
            if result.returncode==0:break
        else:raise StartError('Docker 준비 시간이 초과됐습니다. Docker Desktop 상태를 확인한 뒤 다시 실행하세요.')
    if result.stdout.strip()!='linux':
        raise StartError('Docker를 Linux 컨테이너 모드로 실행하세요.')

def ensure_database(executable,config):
    result=command([executable,'container','inspect','--format',
        '{{json .Config.Image}}|{{json .State.Status}}|{{json .HostConfig.PortBindings}}',CONTAINER])
    if result.returncode:
        # inspect 실패를 컨테이너 부재로 단정하지 않고 목록에서도 확인합니다.
        listing=command([executable,'container','ls','-a','--filter','name=^/'+CONTAINER+'$','--format','{{.Names}}'])
        if listing.returncode or CONTAINER in listing.stdout.splitlines():
            raise StartError('기존 DB 컨테이너를 확인하지 못했습니다. 자동 재생성하지 않았습니다.')
        if any(str(config.get(name,'')).startswith('change-me') for name in ('POSTGRES_PASSWORD','RAG_PASSWORD','VAULT_PASSWORD','MIGRATION_PASSWORD')):
            raise StartError('새 DB를 만들기 전에 .env의 예제 비밀번호를 실제 값으로 변경하세요.')
        print('  최초 DB 저장소를 생성합니다. 기존 데이터 삭제 명령은 실행하지 않습니다.',flush=True)
        result=command([executable,'compose','--project-directory',str(REPO),'--env-file',str(REPO/'.env'),
            '-f',str(REPO/'docker-compose.yml'),'up','-d','db'],timeout=300)
        if result.returncode:
            raise StartError('DB를 생성하지 못했습니다. Docker Compose와 .env 설정을 확인하세요.')
        return
    try:
        image,status,ports=(json.loads(part) for part in result.stdout.strip().split('|'))
    except (ValueError,TypeError):
        raise StartError('DB 컨테이너 정보를 확인하지 못했습니다.') from None
    if not image.startswith('pgvector/pgvector:'):
        raise StartError('같은 이름의 다른 컨테이너가 있습니다. 해당 컨테이너는 변경하지 않았습니다.')
    bindings=(ports or {}).get('5432/tcp') or []
    if str(config.get('DB_PORT','5433')) not in {item['HostPort'] for item in bindings}:
        raise StartError('기존 DB의 포트가 .env와 다릅니다. DB를 재생성하지 않았습니다.')
    if status!='running':
        print('  기존 DB 컨테이너를 시작합니다.',flush=True)
        if command([executable,'start',CONTAINER],timeout=90).returncode:
            raise StartError('기존 DB를 시작하지 못했습니다.')
    else:
        print('  기존 DB를 그대로 사용합니다.',flush=True)

def wait_database(config):
    import psycopg
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        try:
            with psycopg.connect(host=config.get('DB_HOST','localhost'),port=int(config.get('DB_PORT','5433')),
                dbname=config.get('POSTGRES_DB','pii_rag'),user='rag_user',password=config['RAG_PASSWORD'],
                connect_timeout=2,options='-c default_transaction_read_only=on') as connection:
                connection.execute('SELECT 1').fetchone()
            return
        except psycopg.Error:
            time.sleep(1)
    raise StartError('DB 연결 준비 시간이 초과됐습니다. DB 포트·계정·비밀번호를 확인하세요.')

def prepare_accounts():
    if not (state_dir()/'auth.json').exists():
        run_python(['-m','server_admin','--setup','--generate'],'최초 관리자 계정 준비')
        print('  최초 로그인 정보: '+str(state_dir()/'initial-login.txt'),flush=True)
    else:
        auth=read_private('auth.json')
        if not auth.get('username') or not auth.get('password'):
            raise StartError('기존 관리자 계정 설정을 확인하세요. 비밀번호를 자동 초기화하지 않았습니다.')
    from starter.business_admin import connection as business_connection
    try:
        with business_connection() as business_db:
            business_db.execute('SELECT document_id FROM rag.document_info LIMIT 0')
            business_db.execute('SELECT team_id FROM auth.team_position_policy LIMIT 0')
        business_ready = True
    except Exception:
        business_ready = False
    if not business_ready:
        run_python(['-m','starter.business_admin','--prepare'],'업무 관리자 최초 준비')
    from .operations import database_status
    if database_status()['state']!='ok':
        print('  DB 버전 조회 전용 계정을 준비합니다.',flush=True)
        run_python(['-m','server_admin','--prepare-monitor'],'DB 버전 조회 계정 준비')

def is_manager(process):
    import psutil
    try:
        args=process.cmdline()
        return '-m' in args and args[args.index('-m')+1]=='server_admin' and os.path.normcase(process.cwd())==os.path.normcase(str(REPO))
    except (psutil.Error,ValueError,IndexError):return False

def existing_manager():
    import psutil
    found=False
    try:
        connections=psutil.net_connections(kind='tcp')
        for item in connections:
            if item.status!=psutil.CONN_LISTEN or item.laddr.port!=9000:continue
            if item.laddr.ip!='127.0.0.1' or not item.pid or not is_manager(psutil.Process(item.pid)):
                raise StartError('9000 포트가 다른 프로그램 또는 다른 접속 설정으로 사용 중입니다. 임의 종료하지 않았습니다.')
            found=True
    except psutil.Error:
        raise StartError('관리자 포트의 실행 프로그램을 확인할 권한이 없습니다.') from None
    return found

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def manager_ready():
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(URL+'/',timeout=2) as response:
            body=response.read(65536).decode('utf-8')
            return response.status==200 and '<title>서버 관리자 · RAG 운영</title>' in body
    except (OSError,UnicodeError):return False

def ensure_manager():
    already=existing_manager()
    child=None
    if already:
        print('  실행 중인 관리자 페이지를 그대로 사용합니다.',flush=True)
    else:
        flags={'creationflags':subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
        with (state_dir()/'server.stdout.log').open('ab') as output,(state_dir()/'server.stderr.log').open('ab') as error:
            child=subprocess.Popen([sys.executable,'-B','-m','server_admin'],cwd=REPO,stdin=subprocess.DEVNULL,
                stdout=output,stderr=error,**flags)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if manager_ready() and existing_manager():return already
        if child is not None and child.poll() is not None:break
        time.sleep(.25)
    raise StartError('관리자 페이지 준비를 확인하지 못했습니다. 관리자 실행 기록을 확인하세요.')

@contextlib.contextmanager
def launch_lock():
    path=state_dir()/'launcher.lock'
    with path.open('a+b') as handle:
        handle.seek(0,2)
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise StartError('다른 시작 작업이 진행 중입니다. 잠시 후 다시 실행하세요.') from None
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

def launch(*,open_browser=True):
    for module in ['dotenv','psycopg','alembic','sqlalchemy','fastapi','uvicorn','psutil']:
        if importlib.util.find_spec(module) is None:
            raise StartError('필요한 패키지가 없습니다. 가상환경에서 requirements.txt를 먼저 설치하세요.')
    config=environment()
    prepare_private_dir()
    with launch_lock():
        print('[1/5] Docker 준비 확인',flush=True)
        executable=docker_executable();ensure_docker(executable)
        print('[2/5] DB 시작 및 연결 확인',flush=True)
        ensure_database(executable,config);wait_database(config)
        print('[3/5] DB 버전 확인 및 미적용 변경 반영',flush=True)
        run_python(['-m','starter.migrate'],'DB 버전 확인·변경',timeout=180)
        print('[4/5] 관리자 계정과 조회 설정 확인',flush=True)
        prepare_accounts()
        print('[5/5] 관리자 페이지 준비',flush=True)
        reused=ensure_manager()
        if open_browser:
            try:webbrowser.open(URL,new=2)
            except Exception:pass
        print('\n시작 완료: '+URL,flush=True)
        print('관리자 화면에서 챗봇 서버 두 개와 분산기를 시작하세요.',flush=True)
        return {'url':URL,'manager_reused':reused}

def main():
    parser=argparse.ArgumentParser(description='로컬 서버 관리자와 시스템 시작')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--start',action='store_true',help='DB 확인 후 관리자 실행')
    modes.add_argument('--setup',action='store_true',help='최초 관리자 계정 준비')
    modes.add_argument('--prepare-monitor',action='store_true',help='DB 버전 조회 계정 준비 (일회성 유지보수)')
    parser.add_argument('--port',type=int,default=9000)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--username',default='serveradmin')
    parser.add_argument('--generate',action='store_true')
    parser.add_argument('--reset',action='store_true')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('포트는 1024~65535 사이로 지정하세요.')
    try:
        if args.setup:
            from .security import setup_account
            setup_account(username=args.username,generate=args.generate,reset=args.reset)
        elif args.prepare_monitor:
            from .operations import bootstrap_monitor
            bootstrap_monitor()
        elif args.start:
            if args.port!=9000:parser.error('시스템 시작은 기본 관리자 포트 9000을 사용합니다.')
            launch(open_browser=not args.no_browser)
        else:
            if args.generate or args.reset or args.no_browser:
                parser.error('계정 옵션은 --setup과, 브라우저 옵션은 --start와 사용하세요.')
            import uvicorn
            uvicorn.run('server_admin.app:app',host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False,workers=1)
    except (StartError,ValueError) as exc:
        print('실행하지 못했습니다: '+str(exc),file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print('실행하지 못했습니다. 설정·접근 권한·실행 기록을 확인하세요. 기존 데이터를 초기화하지 않았습니다.',file=sys.stderr)
        raise SystemExit(1) from None

if __name__=='__main__':main()
