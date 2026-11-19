"""운영 볼륨을 연결하지 않는 임시 PostgreSQL 컨테이너에서 설치 경로를 검증합니다."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from server_admin.__main__ import docker_executable
    import psycopg
    docker=docker_executable()
    name='codex-schema-verify-'+secrets.token_hex(6)
    password=secrets.token_urlsafe(28)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    args=[docker,'run','--rm','-d','--name',name,'--label','codex.schema-test=true',
        '-e','POSTGRES_USER=admin','-e','POSTGRES_PASSWORD='+password,'-e','POSTGRES_DB=schema_verify',
        '-p','127.0.0.1:'+str(port)+':5432','pgvector/pgvector:pg16']
    started=False
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=180)
        if result.returncode:
            raise RuntimeError('검증용 DB 컨테이너를 시작하지 못했습니다. Docker 실행 상태와 이미지 준비를 확인하세요.')
        started=True
        print('격리된 검증 DB 준비 중 (운영 DB·볼륨을 연결하지 않음)',flush=True)
        deadline=time.monotonic()+60
        while True:
            try:
                with psycopg.connect(host='127.0.0.1',port=port,dbname='schema_verify',user='admin',password=password,connect_timeout=2) as c:c.execute('SELECT 1')
                break
            except psycopg.Error:
                if time.monotonic()>deadline:raise RuntimeError('검증 DB 준비 시간이 초과됐습니다.') from None
                time.sleep(.5)
        env={**os.environ,'SCHEMA_TEST_DB_PORT':str(port),'SCHEMA_TEST_ADMIN_PASSWORD':password,'EMBED_DIM':'1024','PYTHONIOENCODING':'utf-8'}
        completed=subprocess.run([sys.executable,'-B','-m','unittest','starter.test_schema_bootstrap','-v'],cwd=ROOT,env=env,timeout=240)
        return completed.returncode
    finally:
        if started:
            if not name.startswith('codex-schema-verify-'):raise RuntimeError('검증 컨테이너 범위 확인 실패')
            # 이 함수가 생성한 정확한 컨테이너만 종료합니다. --rm으로 임시 저장소도 제거됩니다.
            stopped=subprocess.run([docker,'stop','-t','2',name],capture_output=True,text=True,timeout=20)
            if stopped.returncode:print('검증 컨테이너 종료를 확인하세요: '+name,file=sys.stderr)
            else:print('검증용 컨테이너 정리 완료',flush=True)


if __name__=='__main__':
    try:raise SystemExit(main())
    except RuntimeError as error:
        print(str(error),file=sys.stderr);raise SystemExit(1) from None
