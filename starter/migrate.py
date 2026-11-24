"""프로그램 기동 전 단일 잠금 안에서 기존 구조 검증 및 버전별 변경 적용."""
import argparse
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, URL
from sqlalchemy.pool import NullPool
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
LOCK_KEY = 738219461082
log = logging.getLogger(__name__)


TABLES = {
    "auth": ["employee_info", "permission_change_log", "role_info", "team_info", "user_info", "team_position_policy"],
    "rag": ["chunks", "document_info"], "vault": ["person", "token_map", "person_mention"],
}

QUERIES = {
    "columns": """SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
        coalesce(pg_get_expr(d.adbin,d.adrelid),''), a.attidentity
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
    "constraints": "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname",
    "indexes": "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=split_part(%s,'.',1) AND tablename=split_part(%s,'.',2) ORDER BY indexname",
    "triggers": "SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid=to_regclass(%s) AND NOT tgisinternal ORDER BY tgname",
}

def capture(connection):
    connection.exec_driver_sql("SELECT set_config('search_path','public',true)")
    result = {}
    for schema, names in TABLES.items():
        for name in names:
            key = schema + "." + name
            if not connection.exec_driver_sql("SELECT to_regclass(%s)",(key,)).scalar():
                continue
            result[key] = {}
            for kind, query in QUERIES.items():
                params = (key,key) if kind=="indexes" else (key,)
                rows = connection.exec_driver_sql(query,params).fetchall()
                result[key][kind] = [[v.replace("\r\n","\n") if isinstance(v,str) else v for v in row] for row in rows]
    result["functions"] = []
    for name in ("auth.set_updated_at()", "vault.set_person_updated_at()"):
        value = connection.exec_driver_sql("SELECT pg_get_functiondef(to_regprocedure(%s))",(name,)).scalar()
        result["functions"].append([name, value.replace("\r\n","\n") if value else None])
    return result

def verify_baseline(connection, contract="baseline_contract.json"):
    from .schema_bootstrap import differences
    expected = json.loads((ROOT/"migrations"/contract).read_text(encoding="utf-8"))
    issues = differences(capture(connection), expected)
    if issues:
        raise RuntimeError("기준 구조와 불일치: " + "; ".join(issues))


def alembic_config(config_path=None):
    if config_path is not None:return Config(str(config_path))
    config=Config()
    config.set_main_option('script_location',str(ROOT/'migrations').replace('%','%%'))
    return config

def prepare_existing_database():
    """관리자 접속은 이 설치 준비 단계에서만 사용하며 서비스 계정을 초기화하지 않습니다."""
    from dotenv import dotenv_values
    from psycopg import sql,connect,Error
    from .schema_bootstrap import reconcile, differences, contract, OWNERS, ident, qualified
    env=dotenv_values(ROOT/'.env')
    required=('POSTGRES_PASSWORD','RAG_PASSWORD','VAULT_PASSWORD','MIGRATION_PASSWORD')
    if any(not env.get(k) for k in required):
        raise RuntimeError('.env의 DB 관리자·검색·매핑·마이그레이션 비밀번호를 먼저 설정하세요.')
    url=URL.create('postgresql+psycopg',username=env.get('POSTGRES_USER','admin'),password=env['POSTGRES_PASSWORD'],host=env.get('DB_HOST','localhost'),port=int(env.get('DB_PORT','5433')),database=env.get('POSTGRES_DB','pii_rag'))
    engine=create_engine(url,poolclass=NullPool,hide_parameters=True,connect_args={'connect_timeout':10})
    cfg=alembic_config()
    try:
        with engine.begin() as c:
            c.exec_driver_sql("SET LOCAL lock_timeout='10s'")
            c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(LOCK_KEY,))
            raw=c.connection.driver_connection
            for role,key in [('rag_user','RAG_PASSWORD'),('vault_user','VAULT_PASSWORD'),('migration_user','MIGRATION_PASSWORD')]:
                existing=c.exec_driver_sql('SELECT rolcanlogin FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
                if existing:
                    if not existing[0]:
                        raise RuntimeError(role+' 계정은 로그인이 금지되어 있습니다. 기존 계정 설정을 임의로 변경하지 않았습니다.')
                    try:
                        with connect(host=env.get('DB_HOST','localhost'),port=int(env.get('DB_PORT','5433')),dbname=env.get('POSTGRES_DB','pii_rag'),user=role,password=env[key],connect_timeout=3) as check:
                            check.execute('SELECT 1')
                    except Error as error:
                        if getattr(error,'sqlstate',None) in ('28P01','28000') or 'password authentication failed' in str(error):
                            raise RuntimeError(key+'가 기존 '+role+' 계정과 일치하지 않습니다. 비밀번호는 초기화하지 않았습니다.') from None
                        # 인증 후 DB CONNECT 권한에서 거절된 경우에는 아래 GRANT로 보충합니다.
                        if getattr(error,'sqlstate',None) != '42501':
                            raise
                else:
                    raw.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(env[key])))
            c.exec_driver_sql('GRANT rag_user,vault_user TO migration_user')
            raw.execute(sql.SQL('GRANT CONNECT,CREATE ON DATABASE {} TO migration_user').format(sql.Identifier(env.get('POSTGRES_DB','pii_rag'))))
            raw.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO rag_user,vault_user').format(sql.Identifier(env.get('POSTGRES_DB','pii_rag'))))
            c.exec_driver_sql('GRANT USAGE,CREATE ON SCHEMA public TO migration_user')
            c.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
            version=MigrationContext.configure(c,opts={'version_table_schema':'public'}).get_current_revision()
            cfg.attributes['connection']=c
            if version is None:
                if not differences(capture(c),contract('0002_contract.json')):
                    command.stamp(cfg,'0002')
                else:
                    reconcile(c,capture)
                    verify_baseline(c)
                    command.stamp(cfg,'0001')
                log.info('기존 DB의 필수 구조 보충 및 버전 등록 완료')
            elif version in ('0001','0002'):
                name='0002_contract.json' if version=='0002' else 'baseline_contract.json'
                if differences(capture(c),contract(name)):
                    reconcile(c,capture,name)
                verify_baseline(c,name)
            else:
                try:
                    ScriptDirectory.from_config(cfg).get_revision(version)
                except Exception:
                    raise RuntimeError('현재 소스에 없는 DB 버전입니다: '+version) from None
                # 0003 이후 버전도 해당 버전의 구조 계약으로 빠진 nullable 열 등을 보충합니다.
                name=f'{version}_contract.json'
                if (ROOT/'migrations'/name).is_file():
                    if differences(capture(c),contract(name)):
                        reconcile(c,capture,name)
                    verify_baseline(c,name)
            if c.exec_driver_sql("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.alembic_version'::regclass").scalar() != 'migration_user':
                c.exec_driver_sql('ALTER TABLE public.alembic_version OWNER TO migration_user')
            for schema,owner in OWNERS.items():
                if c.exec_driver_sql('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s',(schema,)).scalar() != owner:
                    c.exec_driver_sql(f'ALTER SCHEMA {ident(schema)} OWNER TO {ident(owner)}')
            for schema,names in TABLES.items():
                for name in names:
                    table=schema+'.'+name
                    owner=c.exec_driver_sql('SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=to_regclass(%s)',(table,)).scalar()
                    if owner and owner != OWNERS[schema]:
                        c.exec_driver_sql(f'ALTER TABLE {qualified(table)} OWNER TO {ident(OWNERS[schema])}')
            for name,owner in [('auth.set_updated_at()','migration_user'),('vault.set_person_updated_at()','vault_user')]:
                if c.exec_driver_sql('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=to_regprocedure(%s)',(name,)).scalar() != owner:
                    c.exec_driver_sql(f'ALTER FUNCTION {qualified(name[:-2])}() OWNER TO {ident(owner)}')
        print('DB 전용 계정·필수 구조 준비 완료. 기존 계정 비밀번호와 업무 데이터는 초기화하지 않았습니다.')
    finally:
        engine.dispose()


def engine_from_env():
    load_dotenv(ROOT / ".env")
    password = os.getenv("MIGRATION_PASSWORD")
    if not password:
        raise RuntimeError("MIGRATION_PASSWORD가 없습니다. DB 기본 환경 준비와 .env 설정을 확인하세요.")
    user = os.getenv("MIGRATION_USER", "migration_user")
    if user in ("admin", os.getenv("POSTGRES_USER", "admin"), "rag_user", "vault_user"):
        raise RuntimeError("스키마 변경에는 별도의 migration_user 계정을 사용하세요.")
    return create_engine(URL.create("postgresql+psycopg", username=user, password=password,
        host=os.getenv("DB_HOST","localhost"), port=int(os.getenv("DB_PORT","5433")),
        database=os.getenv("POSTGRES_DB","pii_rag")), poolclass=NullPool,
        connect_args={"connect_timeout": 10}, hide_parameters=True)

def migrate(*, engine=None, check=False, config_path=None):
    own_engine = engine is None
    engine = engine or engine_from_env()
    cfg = alembic_config(config_path)
    scripts = ScriptDirectory.from_config(cfg)
    head = scripts.get_current_head()
    try:
        with engine.connect() as connection:
            deadline=time.monotonic()+float(os.getenv("MIGRATION_LOCK_TIMEOUT","60"))
            while not connection.exec_driver_sql("SELECT pg_try_advisory_lock(%s)",(LOCK_KEY,)).scalar():
                connection.rollback()
                if time.monotonic()>=deadline:
                    raise RuntimeError("다른 서버의 스키마 변경 작업이 끝나지 않아 시작을 중단했습니다.")
                time.sleep(0.2)
            connection.commit()
            try:
                with connection.begin():
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                    current = MigrationContext.configure(connection, opts={"version_table_schema":"public"}).get_current_revision()
                    cfg.attributes["connection"] = connection
                    expected={s+"."+n for s,names in TABLES.items() for n in names}
                    present={name for name in expected if connection.exec_driver_sql("SELECT to_regclass(%s)",(name,)).scalar()}
                    if current is None:
                        from .schema_bootstrap import reconcile,differences,contract
                        schemas=connection.exec_driver_sql("SELECT nspname FROM pg_namespace WHERE nspname IN ('auth','rag','vault')").fetchall()
                        if check:
                            issues=differences(capture(connection),contract('0002_contract.json'))
                            raise RuntimeError("DB 버전이 미등록 상태입니다. 시스템 시작으로 필수 구조를 준비하세요. " + '; '.join(issues))
                        if present or schemas:
                            if not differences(capture(connection),contract('0002_contract.json')):
                                command.stamp(cfg,'0002');current='0002'
                            else:
                                reconcile(connection,capture)
                                verify_baseline(connection)
                                command.stamp(cfg,'0001');current='0001'
                            log.info('버전 미등록 DB의 필수 구조 준비 및 버전 등록 완료')
                    if check and current != head:
                        raise RuntimeError("DB에 미적용 변경이 있습니다. 마이그레이션을 실행하세요.")
                    if not check:
                        command.upgrade(cfg,"head")
                    # 최신 버전의 구조 계약 파일이 있으면 운영 중의 구조 변형도 검증합니다.
                    name = "baseline_contract.json" if head == "0001" else f"{head}_contract.json"
                    if (ROOT / "migrations" / name).is_file():
                        verify_baseline(connection, name)
                    dim=connection.exec_driver_sql("SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='rag.chunks'::regclass AND attname='embedding'").scalar()
                    expected_dim=int(os.getenv("EMBED_DIM","1024"))
                    if dim.split('.')[-1]!=f"vector({expected_dim})":
                        raise RuntimeError("EMBED_DIM 설정과 DB 임베딩 차원이 다릅니다. 설정을 확인하세요.")
                log.info("DB 스키마 준비 완료: %s",head)
                return head
            finally:
                connection.rollback()
                connection.exec_driver_sql("SELECT pg_advisory_unlock(%s)",(LOCK_KEY,))
                connection.commit()
    finally:
        if own_engine:
            engine.dispose()


def redact_settings(message):
    from dotenv import dotenv_values
    values=list(dotenv_values(ROOT/'.env').items())+list(os.environ.items())
    private=[str(v) for k,v in values if v and any(t in k.upper() for t in ('PASSWORD','KEY','SECRET','TOKEN'))]
    for value in sorted(set(private),key=len,reverse=True):
        message=message.replace(value,'[가림]')
    return message


def safe_error(error):
    if isinstance(error,RuntimeError):
        return redact_settings(str(error))
    cause=getattr(error,'orig',error)
    code=getattr(cause,'sqlstate',None)
    if code in ('28P01','28000') or 'password authentication failed' in str(cause):
        return 'DB 계정 인증 실패: .env의 관리자 또는 migration_user 접속 정보가 기존 DB와 일치하는지 확인하세요.'
    if code=='42501':
        return 'DB 권한 부족: 설치 준비에는 계정·스키마 소유권을 설정할 수 있는 DB 관리자 권한이 필요합니다. (42501)'
    if code=='58P01':
        return 'DB 확장 파일을 찾지 못했습니다. PostgreSQL 서버에 pgvector가 설치되어 있는지 확인하세요. (58P01)'
    if code=='3D000':
        return '대상 DB가 없습니다. POSTGRES_DB와 접속 대상 서버를 확인하세요. (3D000)'
    if code in ('23502','23503','23505','23514'):
        diag=getattr(cause,'diag',None)
        names=[getattr(diag,k,None) for k in ('schema_name','table_name','column_name','constraint_name')]
        target='.'.join(str(n) for n in names if n) or '필수 제약조건'
        return redact_settings('기존 데이터가 필요한 제약조건을 만족하지 않습니다: '+target+' ('+code+'). 해당 단계의 변경은 롤백했습니다. 원본 행 값은 출력하지 않습니다.')
    return type(cause).__name__+(' ('+code+')' if code else '')+': DB 연결 또는 변경 파일을 확인하세요.'


def main():
    parser=argparse.ArgumentParser(description="DB 구조 확인 및 버전별 변경 적용")
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--check",action="store_true",help="구조와 버전을 확인만 하고 변경하지 않음")
    group.add_argument("--prepare",action="store_true",help="DB 전용 계정 및 누락된 필수 구조 자동 준비")
    group.add_argument("--new",metavar="설명",help="다음 변경 파일 생성 (DB 변경 없음)")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        if args.prepare:
            prepare_existing_database()
            return
        if args.new:
            command.revision(alembic_config(),message=args.new)
            return
        migrate(check=args.check)
    except Exception as exc:
        # 연결 문자열·비밀번호가 포함될 수 있는 원본 예외를 터미널에 출력하지 않습니다.
        message=safe_error(exc)
        log.error("DB 준비 실패: %s",message)
        raise SystemExit(1) from None

if __name__=="__main__":
    main()
