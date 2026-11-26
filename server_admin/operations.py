"""로컬 챗봇 제어·분산·진행 상태·시스템 관측을 관리합니다."""
import contextlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

import psutil

from .security import REPO, read_private, state_dir, write_private, audit, prepare_private_dir

class Progress:
    class JobBusy(RuntimeError):
        pass

    def __init__(self):
        self.lock=threading.RLock()
        self.state={'busy':False,'phase':'idle','message':'진행 중인 작업이 없습니다.'}

    def begin(self,target,action):
        with self.lock:
            if self.state['busy']:
                raise self.JobBusy('다른 작업이 진행 중입니다. 완료 후 다시 시도하세요.')
            self.state.clear()
            self.state.update(busy=True,job_id=secrets.token_hex(12),target=target,action=action,
                phase='checking',message='대상과 실행 상태를 확인하고 있습니다.',started=time.monotonic(),
                phase_started=time.monotonic(),active_requests=None,drain_limit_seconds=None,waited_seconds=0)

    def update(self,phase=None,message=None,**fields):
        with self.lock:
            if not self.state['busy']:return
            now=time.monotonic()
            if phase and phase!=self.state['phase']:
                if self.state['phase']=='waiting':self.state['waited_seconds']=round(now-self.state['phase_started'],1)
                self.state['phase']=phase;self.state['phase_started']=now
            if message is not None:self.state['message']=message
            self.state.update(fields)

    def finish(self,phase,message):
        with self.lock:
            self.update(phase,message)
            self.state['finished']=time.monotonic();self.state['busy']=False

    def snapshot(self):
        with self.lock:
            result={key:value for key,value in self.state.items() if key not in ('started','finished','phase_started')}
            if 'started' not in self.state:return result
            now=time.monotonic()
            result['elapsed_seconds']=round(self.state.get('finished',now)-self.state['started'],1)
            if self.state['phase']=='waiting':
                result['waited_seconds']=round(now-self.state['phase_started'],1)
                result['remaining_seconds']=max(0,round((self.state.get('drain_limit_seconds') or 0)-result['waited_seconds'],1))
            return result

progress=Progress()


OPERATIONS_LOCK = threading.RLock()

CONFIG_LOCK = threading.RLock()

DEFAULT_DRAIN_TIMEOUT = 120

MIN_DRAIN_TIMEOUT = 15

MAX_DRAIN_TIMEOUT = 120

NODES = {"node1":8001,"node2":8002}

PROXY_PORT = 8080

class OperationError(RuntimeError):
    pass

class DrainDeferred(OperationError):
    pass

def settings():
    with CONFIG_LOCK:
        if not (state_dir()/"operations.json").exists():
            write_private("operations.json",{
                "drain_timeout_seconds":DEFAULT_DRAIN_TIMEOUT,
                "provider":"local", "load_balancer":"local", "proxy_token":secrets.token_urlsafe(32),
                "nodes":{node:{"enabled":True,"weight":50} for node in NODES}})
        config=read_private("operations.json")
        if config.get("provider")!='local' or config.get("load_balancer")!='local':
            raise OperationError("현재 구성은 로컬 전용입니다. 클라우드 설정은 별도 백업본을 사용하세요.")
        if set(config.get("nodes",{}))!=set(NODES):
            raise OperationError("등록된 서버 구성이 올바르지 않습니다.")
        for value in config["nodes"].values():
            if type(value.get("enabled")) is not bool or type(value.get("weight")) is not int or not 0<=value["weight"]<=100:
                raise OperationError("서버 가중치 설정을 확인하세요.")
        timeout=config.get('drain_timeout_seconds',DEFAULT_DRAIN_TIMEOUT)
        if type(timeout) is not int or not MIN_DRAIN_TIMEOUT<=timeout<=MAX_DRAIN_TIMEOUT:
            raise OperationError('재시작 대기 시간은 15~120초의 정수여야 합니다.')
        return config

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def local_json(port,path,token=None,timeout=1,data=None):
    headers={"X-Manager-Token":token} if token else {}
    if data is not None:headers['Content-Type']='application/json'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}{path}",headers=headers,data=json.dumps(data).encode() if data is not None else None),timeout=timeout) as response:
            return json.loads(response.read(128*1024))
    except Exception:
        return None

def healthy(node):
    return local_json(NODES[node],"/api/info") is not None

def registry():
    return read_private("processes.json") if (state_dir()/"processes.json").exists() else {}

def argv(target):
    if target=="proxy":
        return [sys.executable,"-B","-m","server_admin.proxy"]
    if target not in NODES:
        raise OperationError("등록되지 않은 서버입니다.")
    return [sys.executable,"-B","-m","uvicorn","server:app","--host","127.0.0.1","--port",str(NODES[target]),"--no-access-log"]

def managed_process(target):
    record=registry().get(target)
    if not record:
        return None
    try:
        process=psutil.Process(record["pid"])
        if abs(process.create_time()-record["created"])>0.01:
            return None
        if process.cmdline()[1:]!=argv(target)[1:] or os.path.normcase(process.cwd())!=os.path.normcase(str(REPO)):
            return None
        if process.pid==os.getpid() or process.pid in [p.pid for p in psutil.Process().parents()]:
            return None
        return process if process.is_running() else None
    except (psutil.Error,KeyError,OSError):
        return None

def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.2)
        return sock.connect_ex(("127.0.0.1",port))==0

def start_process(target):
    config=settings()
    if managed_process(target):
        return
    port=PROXY_PORT if target=="proxy" else NODES[target]
    if listening(port):
        raise OperationError("포트를 다른 프로세스가 사용 중입니다. 임의로 종료하지 않았습니다.")
    flags={"creationflags":subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=="nt" else {"start_new_session":True}
    # 챗봇 서버는 서버별로 지정한 데이터 버전의 키·DATA_VERSION 을 이 프로세스 환경으로만 받습니다(.env 보다 우선).
    environment=None
    if target in NODES:
        from .data_versions import node_env, DataVersionUIError
        try:overrides=node_env(target)
        except DataVersionUIError as exc:raise OperationError(str(exc)) from None
        if overrides:environment={**os.environ,**overrides}
    with (state_dir()/(target+".stdout.log")).open("ab") as out, (state_dir()/(target+".stderr.log")).open("ab") as error:
        process=subprocess.Popen(argv(target),cwd=REPO,stdin=subprocess.DEVNULL,stdout=out,stderr=error,env=environment,**flags)
    tracked=psutil.Process(process.pid)
    records=registry()
    records[target]={"pid":process.pid,"created":tracked.create_time()}
    write_private("processes.json",records)
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        ready=local_json(PROXY_PORT,"/__manager/status",config["proxy_token"]) is not None if target=="proxy" else healthy(target)
        if ready:
            return
        time.sleep(.25)
    raise OperationError("기동 후 정상 응답을 확인하지 못했습니다. 실행 결과와 DB 연결을 확인하세요.")

def stop_process(target):
    process=managed_process(target)
    if not process:
        port=PROXY_PORT if target=="proxy" else NODES[target]
        if listening(port):
            raise OperationError("관리 프로그램이 시작한 프로세스가 아니어서 종료하지 않았습니다.")
        return
    try:
        children=process.children(recursive=True)
        # 시작 시 기록한 프로세스와 그 자식만 대상으로 하며 PID 재사용을 검사합니다.
        targets=children[::-1]+[process]
        for item in targets:
            with contextlib.suppress(psutil.NoSuchProcess):item.terminate()
        _,alive=psutil.wait_procs(targets,timeout=8)
        for item in alive:
            with contextlib.suppress(psutil.NoSuchProcess):item.kill()
        _,alive=psutil.wait_procs(alive,timeout=3)
        if alive:
            raise OperationError("서비스 종료를 확인하지 못했습니다.")
    except psutil.Error:
        raise OperationError("서비스 종료 권한과 상태를 확인하세요.") from None
    records=registry();records.pop(target,None);write_private("processes.json",records)

def proxy_status():
    config=settings()
    response=local_json(PROXY_PORT,"/__manager/status",config['proxy_token'])
    if response and response.get("kind")=="rag-local-proxy":
        return {"running":True,"nodes":response["nodes"],"provider":"local"}
    return {"running":False,"nodes":{},"provider":"local"}

def set_routing(node,*,enabled=None,weight=None):
    config=settings()
    if node not in NODES:
        raise OperationError("등록되지 않은 서버입니다.")
    updated=dict(config['nodes'][node])
    if enabled is not None:updated['enabled']=enabled
    if weight is not None:updated['weight']=weight
    if not updated['enabled'] or updated['weight']==0:
        alternatives=[n for n in NODES if n!=node and config['nodes'][n]['enabled'] and config['nodes'][n]['weight']>0 and healthy(n)]
        if not alternatives:
            raise OperationError("다른 정상 서버가 없어 마지막 서버의 트래픽을 제외하지 않았습니다.")
    elif not healthy(node):
        raise OperationError("서버의 정상 응답을 확인한 뒤 트래픽을 복귀하세요.")
    config['nodes'][node]=updated
    write_private('operations.json',config)
    if config['load_balancer']=='local' and proxy_status()['running']:
        ack=local_json(PROXY_PORT,'/__manager/route',config['proxy_token'],data={'node':node,'enabled':updated['enabled'] and updated['weight']>0})
        if not ack or not ack.get('ok'):
            raise OperationError('분산기의 변경 확인을 받지 못했습니다. 상태를 확인하세요.')

def drain(node):
    progress.update("excluding","새 요청을 다른 정상 서버로 보내고 있습니다.")
    if settings()['nodes'][node]['enabled']:
        set_routing(node,enabled=False)
    balancer=proxy_status()
    if not balancer['running']:
        if listening(PROXY_PORT) and settings()['load_balancer']=='local':
            raise OperationError("분산기 연결을 확인할 수 없어 안전한 중지를 보류했습니다.")
        return
    if settings()['load_balancer']=='local':
        ack=local_json(PROXY_PORT,'/__manager/route',settings()['proxy_token'],data={'node':node,'enabled':False})
        if not ack or not ack.get('ok'):
            raise OperationError('새 요청 제외를 확인하지 못해 중지를 보류했습니다.')
    limit=settings().get("drain_timeout_seconds",DEFAULT_DRAIN_TIMEOUT)
    progress.update("waiting","처리 중인 요청이 끝나기를 기다리고 있습니다.",drain_limit_seconds=limit)
    deadline=time.monotonic()+limit
    while time.monotonic()<deadline:
        status=proxy_status()
        entry=status['nodes'].get(node)
        if not status['running'] or entry is None:
            raise OperationError("처리 중 요청 수를 확인할 수 없어 중지를 보류했습니다.")
        progress.update(active_requests=entry['active'])
        if entry['active']==0:return
        time.sleep(.25)
    raise DrainDeferred("설정한 시간 안에 요청이 끝나지 않아 중지를 보류했습니다. 트래픽 제외 상태를 유지합니다.")

def control(node,action,confirmation,weight=None):
    # 작업 중 재요청을 쌓아 두었다가 나중에 실행하지 않고 즉시 거절합니다.
    if node not in (*NODES,'proxy') or action not in ('start','stop','restart','exclude','include','weight') or confirmation!=node+':'+action:
        raise OperationError('허용되지 않거나 확인되지 않은 작업입니다.')
    try:progress.begin(node,action)
    except progress.JobBusy as exc:raise OperationError(str(exc)) from None
    try:
        result=_control(node,action,confirmation,weight)
        progress.finish('completed','작업을 완료했습니다.')
        return result
    except DrainDeferred as exc:
        progress.finish('deferred',str(exc));raise
    except OperationError as exc:
        progress.finish('failed',str(exc));raise
    except Exception:
        progress.finish('failed','작업을 완료하지 못했습니다. 실행 상태를 확인하세요.')
        raise OperationError('작업을 완료하지 못했습니다. 실행 상태를 확인하세요.') from None

def update_drain_timeout(seconds):
    if type(seconds) is not int or not MIN_DRAIN_TIMEOUT<=seconds<=MAX_DRAIN_TIMEOUT:
        raise OperationError('재시작 대기 시간은 15~120초의 정수로 입력하세요.')
    if not OPERATIONS_LOCK.acquire(blocking=False):
        raise OperationError('작업 중에는 대기 시간을 변경할 수 없습니다.')
    try:
        if progress.snapshot()['busy']:
            raise OperationError('작업 중에는 대기 시간을 변경할 수 없습니다.')
        config=settings();config['drain_timeout_seconds']=seconds
        write_private('operations.json',config)
        audit('drain_timeout_change')
        return {'ok':True,'drain_timeout_seconds':seconds}
    finally:OPERATIONS_LOCK.release()

def _control(node,action,confirmation,weight=None):
    if node not in (*NODES,'proxy') or action not in ('start','stop','restart','exclude','include','weight'):
        raise OperationError("허용되지 않은 작업입니다.")
    if confirmation!=node+":"+action:
        raise OperationError("작업 확인이 필요합니다.")
    if action=='weight' and (type(weight) is not int or not 0<=weight<=100):
        raise OperationError("가중치는 0~100의 정수로 입력하세요.")
    with OPERATIONS_LOCK:
        try:
            if node=='proxy':
                if action not in ('start','stop','restart') or settings()['load_balancer']!='local':
                    raise OperationError("분산기는 시작·중지·재시작만 지원합니다.")
                # 분산기 자체 재시작은 순간적인 전체 접속 중단을 발생시킵니다.
                if action in ('stop','restart'):
                    progress.update('stopping','분산기를 중지하고 있습니다.')
                    stop_process('proxy')
                if action in ('start','restart'):
                    progress.update('starting','분산기의 기동과 정상 응답을 확인하고 있습니다.')
                    start_process('proxy')
            elif action=='start':
                progress.update('starting','서비스의 기동과 정상 응답을 확인하고 있습니다.')
                start_process(node)
            elif action in ('stop','restart'):
                # 종료 권한부터 확인하여 외부 프로세스를 제외만 시키는 부작용을 방지합니다.
                if settings()['provider']=='local' and not managed_process(node):
                    if listening(NODES[node]):raise OperationError("다른 방식으로 실행된 서버라 종료하지 않았습니다.")
                was_enabled=settings()['nodes'][node]['enabled']
                drain(node)
                progress.update("stopping","진행 중인 요청이 끝나 서비스를 중지하고 있습니다.")
                stop_process(node)
                if action=='restart':
                    progress.update('starting','서비스를 다시 시작하고 정상 응답을 확인하고 있습니다.')
                    start_process(node)
                    if was_enabled:
                        progress.update('restoring','정상 응답을 확인해 트래픽을 복귀하고 있습니다.')
                        set_routing(node,enabled=True)
            elif action=='exclude':set_routing(node,enabled=False)
            elif action=='include':set_routing(node,enabled=True)
            elif action=='weight':set_routing(node,weight=weight)
            audit(node+'_'+action)
            return {"ok":True,"message":"작업을 완료했습니다."}
        except OperationError:
            audit(node+'_'+action,'failure')
            raise

def operational_status():
    config=settings()
    balancer=proxy_status()
    nodes=[]
    for node,port in NODES.items():
        stats=balancer['nodes'].get(node,{})
        online=healthy(node)
        managed=managed_process(node) is not None
        item={"id":node,"name":"챗봇 서버 "+node[-1],"port":port,"healthy":online,"managed":managed,
            **config['nodes'][node],"active":stats.get('active'),"requests":stats.get('requests'),
            }
        nodes.append(item)
    return {"drain_timeout_seconds":config.get("drain_timeout_seconds",DEFAULT_DRAIN_TIMEOUT),"provider":config['provider'],"nodes":nodes,"load_balancer":{"running":balancer['running'],"provider":balancer['provider'],"port":PROXY_PORT,"managed":managed_process('proxy') is not None}}

STARTED = time.monotonic()

def application_status():
    started=time.monotonic()
    for port in NODES.values():
        data=local_json(port,'/api/info')
        if data is not None:
            return {'state':'ok','latency_ms':round((time.monotonic()-started)*1000),'search_mode':str(data.get('search_mode','—'))[:32]}
    return {'state':'unavailable'}

def database_status():
    if not (state_dir()/"monitor.json").exists():
        return {"state":"unconfigured"}
    import psycopg
    settings = read_private("monitor.json")
    try:
        # 트랜잭션도 읽기 전용으로 설정합니다. 이 계정은 버전 테이블만 읽을 수 있습니다.
        with psycopg.connect(host=settings["host"], port=int(settings["port"]),
            dbname=settings["database"], user="server_monitor", password=settings["password"],
            connect_timeout=2, options="-c default_transaction_read_only=on -c statement_timeout=2000") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
                versions = [row[0] for row in cur.fetchall()]
        return {"state":"ok", "version":", ".join(versions) or "미등록"}
    except Exception:
        return {"state":"unavailable"}

def snapshot():
    disk = shutil.disk_usage(state_dir())
    commit = "확인 불가"
    try:
        run = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=2,
            **({"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}))
        if run.returncode==0:
            candidate=run.stdout.strip()
            if candidate and all(c in "0123456789abcdef" for c in candidate):
                commit=candidate
    except (OSError, subprocess.TimeoutExpired):
        pass
    # 두 관측의 최대 대기 시간은 각각 2초입니다.
    return {"checked_at":datetime.now(timezone.utc).isoformat(),
        "application":application_status(), "database":database_status(),
        "system":{"os":platform.system(), "python":platform.python_version(),
            "uptime_seconds":int(time.monotonic()-STARTED), "cpu_count":os.cpu_count(),
            "disk_total":disk.total,"disk_free":disk.free}, "commit":commit}

def bootstrap_monitor():
    import psycopg
    from psycopg import sql
    from dotenv import dotenv_values
    prepare_private_dir()
    env = dotenv_values(REPO / ".env")
    previous = read_private("monitor.json") if (state_dir()/"monitor.json").exists() else None
    password = previous["password"] if previous else secrets.token_urlsafe(36)
    settings = {"host":env.get("DB_HOST","localhost"),"port":int(env.get("DB_PORT","5433")),
        "database":env.get("POSTGRES_DB","pii_rag"),"password":password}
    with psycopg.connect(host=settings["host"],port=settings["port"],dbname=settings["database"],
        user=env.get("POSTGRES_USER","admin"),password=env.get("POSTGRES_PASSWORD"),connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.alembic_version')")
            if cur.fetchone()[0] is None:
                raise RuntimeError("DB 마이그레이션을 먼저 실행하세요.")
            cur.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname='server_monitor'")
            existing = cur.fetchone()
            if existing:
                if not previous or any(existing):
                    raise RuntimeError("기존 모니터링 계정의 상태를 확인해야 합니다. 자동 변경하지 않았습니다.")
                cur.execute("SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='server_monitor'")
                if cur.fetchone():
                    raise RuntimeError("모니터링 계정에 다른 역할이 부여되어 있어 중단했습니다.")
            else:
                cur.execute(sql.SQL("CREATE ROLE server_monitor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Literal(password)))
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO server_monitor").format(sql.Identifier(settings["database"])))
            cur.execute("GRANT USAGE ON SCHEMA public TO server_monitor")
            cur.execute("GRANT SELECT ON public.alembic_version TO server_monitor")
            cur.execute("ALTER ROLE server_monitor SET default_transaction_read_only=on")
    write_private("monitor.json", settings)
    audit("monitor_setup")
    print("DB 버전 조회 전용 계정 준비 완료. 비밀 설정은 Git 폴더 밖에 저장했습니다.")
