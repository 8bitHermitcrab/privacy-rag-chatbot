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
    "rag": ["chunks", "document_info"], "vault": ["person", "token_map"],
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
    expected = json.loads((Path(__file__).resolve().parents[1]/"migrations"/contract).read_text(encoding="utf-8"))
    actual = capture(connection)
    mismatches = [key for key in expected if actual.get(key)!=expected[key]]
    if mismatches:
        raise RuntimeError("기준 스키마와 불일치하여 자동 등록을 중단했습니다: " + ", ".join(mismatches))


def alembic_config(config_path=None):
    if config_path is not None:return Config(str(config_path))
    config=Config()
    config.set_main_option('script_location',str(ROOT/'migrations').replace('%','%%'))
    return config

def prepare_existing_database():
    from dotenv import dotenv_values
    from psycopg import sql
    env=dotenv_values(ROOT/'.env')
    if not env.get('MIGRATION_PASSWORD'):
        raise RuntimeError('.env에 MIGRATION_PASSWORD를 설정하세요.')
    url=URL.create('postgresql+psycopg',username=env.get('POSTGRES_USER','admin'),password=env.get('POSTGRES_PASSWORD'),host=env.get('DB_HOST','localhost'),port=int(env.get('DB_PORT','5433')),database=env.get('POSTGRES_DB','pii_rag'))
    engine=create_engine(url,poolclass=NullPool,hide_parameters=True)
    try:
        with engine.begin() as c:
            version = MigrationContext.configure(c).get_current_revision()
            verify_baseline(c, "0002_contract.json" if version == "0002" else "baseline_contract.json")
            raw=c.connection.driver_connection
            if not c.exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname='migration_user'").scalar():
                raw.execute(sql.SQL('CREATE ROLE migration_user LOGIN PASSWORD {}').format(sql.Literal(env['MIGRATION_PASSWORD'])))
            c.exec_driver_sql('GRANT rag_user, vault_user TO migration_user')
            raw.execute(sql.SQL('GRANT CONNECT, CREATE ON DATABASE {} TO migration_user').format(sql.Identifier(env.get('POSTGRES_DB','pii_rag'))))
            c.exec_driver_sql('GRANT USAGE, CREATE ON SCHEMA public TO migration_user')
            c.exec_driver_sql('ALTER SCHEMA auth OWNER TO migration_user')
            for table in ['employee_info','permission_change_log','role_info','team_info','user_info','team_position_policy']:
                if not c.exec_driver_sql("SELECT to_regclass(%s)", ('auth.'+table,)).scalar():continue
                c.exec_driver_sql('ALTER TABLE auth.'+table+' OWNER TO migration_user')
            c.exec_driver_sql('ALTER FUNCTION auth.set_updated_at() OWNER TO migration_user')
        print('변경 전용 계정과 소유권 준비 완료. 업무 데이터는 수정하지 않았습니다.')
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
                    if current is None and present:
                        verify_baseline(connection)
                        if check:
                            raise RuntimeError("기존 구조는 일치하지만 버전 미등록 상태입니다. 마이그레이션을 실행하세요.")
                        command.stamp(cfg,"0001")
                        log.info("기존 DB 구조 검증 완료: 기준 버전 0001 등록")
                        current="0001"
                    elif current is None:
                        schemas=connection.exec_driver_sql("SELECT nspname FROM pg_namespace WHERE nspname IN ('auth','rag','vault')").fetchall()
                        if schemas:
                            raise RuntimeError("일부 스키마만 존재합니다. 빈 DB나 완전한 기준 구조가 필요합니다.")
                    if check and current != head:
                        raise RuntimeError("DB에 미적용 변경이 있습니다. 마이그레이션을 실행하세요.")
                    if not check:
                        command.upgrade(cfg,"head")
                    # 현재 버전 0001에서는 운영 중의 구조 변형도 검증합니다.
                    if head in ("0001", "0002"):
                        verify_baseline(connection, "0002_contract.json" if head == "0002" else "baseline_contract.json")
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

def main():
    parser=argparse.ArgumentParser(description="DB 구조 확인 및 버전별 변경 적용")
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--check",action="store_true",help="구조와 버전을 확인만 하고 변경하지 않음")
    group.add_argument("--prepare",action="store_true",help="기존 DB의 변경 전용 계정 준비 (일회성 유지보수)")
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
        message=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__+": DB 연결·권한 또는 변경 파일을 확인하세요."
        log.error("DB 준비 실패: %s",message)
        raise SystemExit(1) from None

if __name__=="__main__":
    main()
