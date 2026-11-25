"""임시 DB에서 신규 설치·기존 스타터 보충·데이터 보존·반복 실행을 검증합니다."""
import os
import secrets
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql
from dotenv import dotenv_values
from sqlalchemy import create_engine, URL
from sqlalchemy.pool import NullPool

from starter.migrate import prepare_existing_database, migrate, capture, ROOT
from starter.schema_bootstrap import reconcile, differences, contract


class SchemaBootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.getenv('SCHEMA_TEST_DB_PORT'):
            raise unittest.SkipTest('tools/verify_schema_install.py로 격리된 검증 DB에서 실행하세요.')
        cls.env = dict(dotenv_values(ROOT / '.env'))
        cls.env.update(DB_HOST='127.0.0.1',DB_PORT=os.environ['SCHEMA_TEST_DB_PORT'],POSTGRES_DB='schema_verify',POSTGRES_USER='admin',
            POSTGRES_PASSWORD=os.environ['SCHEMA_TEST_ADMIN_PASSWORD'],RAG_PASSWORD='fixture-rag-password',VAULT_PASSWORD='fixture-vault-password',MIGRATION_PASSWORD='fixture-migration-password')
        cls.admin = psycopg.connect(host=cls.env.get('DB_HOST','localhost'),port=int(cls.env.get('DB_PORT','5433')),
            dbname=cls.env.get('POSTGRES_DB','pii_rag'),user=cls.env.get('POSTGRES_USER','admin'),password=cls.env['POSTGRES_PASSWORD'],autocommit=True)
        cls.databases=[]

    @classmethod
    def tearDownClass(cls):
        try:
            for name in cls.databases:
                if not name.startswith('codex_schema_test_'):
                    raise RuntimeError('임시 DB 범위 확인 실패')
                cls.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        finally:cls.admin.close()

    def database(self):
        name='codex_schema_test_'+secrets.token_hex(6)
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.databases.append(name)
        env={**self.env,'POSTGRES_DB':name}
        def engine(user='admin'):
            return create_engine(URL.create('postgresql+psycopg',username=env.get('POSTGRES_USER','admin') if user=='admin' else 'migration_user',
                password=env['POSTGRES_PASSWORD'] if user=='admin' else env['MIGRATION_PASSWORD'],host=env.get('DB_HOST','localhost'),port=int(env.get('DB_PORT','5433')),database=name),poolclass=NullPool,hide_parameters=True)
        return env,engine

    def test_00_missing_service_roles_are_created(self):
        self.assertEqual(self.admin.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('rag_user','vault_user','migration_user')").fetchone()[0],0)
        env,make=self.database()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        self.assertEqual(self.admin.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('rag_user','vault_user','migration_user') AND rolcanlogin").fetchone()[0],3)
        e=make('migration_user')
        try:self.assertEqual(migrate(engine=e),'0007')
        finally:e.dispose()

    def test_equivalent_latest_schema_without_version_is_adopted(self):
        env,make=self.database()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make('migration_user')
        try:migrate(engine=e)
        finally:e.dispose()
        e=make()
        try:
            with e.begin() as c:
                c.exec_driver_sql("INSERT INTO auth.user_info(login_id,password_hash,role_code) VALUES ('preserve-admin','unchanged-hash','ADMIN')")
                c.exec_driver_sql('DELETE FROM public.alembic_version')
            with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
            with e.connect() as c:
                self.assertEqual(c.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar(),'0002')
                self.assertEqual(c.exec_driver_sql('SELECT password_hash FROM auth.user_info').scalar(),'unchanged-hash')
        finally:e.dispose()

    def test_versioned_missing_nullable_column_is_repaired(self):
        env,make=self.database()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make('migration_user')
        try:migrate(engine=e)
        finally:e.dispose()
        e=make()
        try:
            with e.begin() as c:c.exec_driver_sql('ALTER TABLE vault.token_map DROP COLUMN created_at')
            with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
            with e.connect() as c:
                head=c.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar()
                self.assertEqual(head,'0007')
                self.assertFalse(differences(capture(c),contract(head+'_contract.json')))
        finally:e.dispose()

    def test_empty_database_and_idempotence(self):
        env,make=self.database()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make('migration_user')
        try:
            self.assertEqual(migrate(engine=e),'0007')
            self.assertEqual(migrate(engine=e,check=True),'0007')
        finally:e.dispose()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make()
        try:
            with e.connect() as c:
                self.assertFalse(differences(capture(c),contract('0002_contract.json')))
                self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM auth.role_info').scalar(),3)
        finally:e.dispose()

    def test_legacy_data_and_extra_column_preserved(self):
        env,make=self.database()
        e=make()
        try:
            with e.begin() as c:
                c.exec_driver_sql('CREATE EXTENSION vector')
                c.exec_driver_sql('CREATE SCHEMA rag')
                c.exec_driver_sql('CREATE SCHEMA vault')
                c.exec_driver_sql("""CREATE TABLE rag.chunks (
                    doc_id text NOT NULL,chunk_id text CONSTRAINT legacy_chunk_pk PRIMARY KEY,
                    masked_text text NOT NULL,chunk_idx integer NOT NULL,doc_type text,
                    embedding vector(1024) NOT NULL,tokens text[] NOT NULL DEFAULT '{}',extra_note text)""")
                c.exec_driver_sql("INSERT INTO rag.chunks(doc_id,chunk_id,chunk_idx,doc_type,masked_text,embedding,extra_note) VALUES ('TEST_DOC','TEST_CHUNK',0,'검증','가명 문서',%s::vector,'추가 열 보존')",('['+','.join(['1']+['0']*1023)+']',))
                c.exec_driver_sql("CREATE TABLE vault.token_map(token text PRIMARY KEY,label text NOT NULL,normalized text NOT NULL,originals text[] NOT NULL DEFAULT '{}')")
                c.exec_driver_sql("INSERT INTO vault.token_map(token,label,normalized) VALUES ('TEST_TOKEN','person','검증값')")
        finally:e.dispose()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make('migration_user')
        try:self.assertEqual(migrate(engine=e),'0007')
        finally:e.dispose()
        e=make()
        try:
            with e.connect() as c:
                self.assertFalse(differences(capture(c),contract('0002_contract.json')))
                self.assertEqual(c.exec_driver_sql('SELECT masked_text,extra_note FROM rag.chunks').one(),('가명 문서','추가 열 보존'))
                self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM vault.token_map').scalar(),1)
                self.assertEqual(c.exec_driver_sql('SELECT metadata_status,owner_employee_id,team_id FROM rag.document_info').one(),('PENDING',None,None))
        finally:e.dispose()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()

    def test_incompatible_vector_dimension_rolls_back_everything(self):
        env,make=self.database()
        e=make()
        try:
            with e.begin() as c:
                c.exec_driver_sql('CREATE EXTENSION vector')
                c.exec_driver_sql('CREATE SCHEMA rag')
                c.exec_driver_sql('CREATE TABLE rag.chunks(chunk_id text PRIMARY KEY,embedding vector(3))')
                c.exec_driver_sql("INSERT INTO rag.chunks VALUES ('PRESERVE','[1,2,3]')")
            with patch('dotenv.dotenv_values',return_value=env),self.assertRaisesRegex(RuntimeError,'rag.chunks.embedding'):
                prepare_existing_database()
            with e.connect() as c:
                self.assertIsNone(c.exec_driver_sql("SELECT to_regclass('auth.user_info')").scalar())
                self.assertEqual(c.exec_driver_sql('SELECT embedding::text FROM rag.chunks').scalar(),'[1,2,3]')
                self.assertIsNone(c.exec_driver_sql("SELECT to_regclass('public.alembic_version')").scalar())
        finally:e.dispose()

    def test_readonly_check_does_not_create_tables(self):
        env,make=self.database()
        e=make()
        try:
            with self.assertRaisesRegex(RuntimeError,'미등록'):
                migrate(engine=e,check=True)
            with e.connect() as c:
                self.assertIsNone(c.exec_driver_sql("SELECT to_regclass('auth.user_info')").scalar())
        finally:e.dispose()

    def test_duplicate_document_chunk_index_aborts_without_data_loss(self):
        from sqlalchemy.exc import IntegrityError
        from starter.migrate import safe_error
        env,make=self.database()
        e=make()
        try:
            with e.begin() as c:
                c.exec_driver_sql('CREATE EXTENSION vector')
                c.exec_driver_sql('CREATE SCHEMA rag')
                c.exec_driver_sql("CREATE TABLE rag.chunks(chunk_id text PRIMARY KEY,doc_id text NOT NULL,chunk_idx integer NOT NULL,doc_type text,masked_text text NOT NULL,tokens text[] NOT NULL DEFAULT '{}',embedding vector(1024) NOT NULL)")
                vec='['+','.join(['1']+['0']*1023)+']'
                for key in ('A','B'):
                    c.exec_driver_sql("INSERT INTO rag.chunks VALUES (%s,'DUPLICATE_PRIVATE_DOCUMENT',0,'검증','KEEP_PRIVATE_TEXT','{}',%s::vector)",(key,vec))
            with patch('dotenv.dotenv_values',return_value=env),self.assertRaises(IntegrityError) as caught:
                prepare_existing_database()
            message=safe_error(caught.exception)
            self.assertIn('uq_chunks_document_index',message)
            self.assertNotIn('DUPLICATE_PRIVATE_DOCUMENT',message)
            self.assertNotIn('KEEP_PRIVATE_TEXT',message)
            with e.connect() as c:
                self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM rag.chunks').scalar(),2)
                self.assertIsNone(c.exec_driver_sql("SELECT to_regclass('rag.document_info')").scalar())
                self.assertIsNone(c.exec_driver_sql("SELECT to_regclass('auth.user_info')").scalar())
        finally:e.dispose()

    def test_empty_person_table_missing_identity_column(self):
        env,make=self.database()
        e=make()
        try:
            with e.begin() as c:
                c.exec_driver_sql('CREATE SCHEMA vault')
                c.exec_driver_sql('CREATE TABLE vault.person(data1 text[])')
            with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
            with e.begin() as c:
                self.assertEqual(c.exec_driver_sql("INSERT INTO vault.person(data1) VALUES (ARRAY['FIXTURE']) RETURNING pid").scalar(),1)
        finally:e.dispose()

    def test_concurrent_first_installation_is_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        env,make=self.database()
        def install():
            prepare_existing_database()
            e=make('migration_user')
            try:return migrate(engine=e)
            finally:e.dispose()
        with patch('dotenv.dotenv_values',return_value=env),ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(install) for _ in range(2)]
            self.assertEqual([f.result(timeout=30) for f in futures],['0007','0007'])


    def test_wrong_existing_password_is_not_reset(self):
        env,make=self.database()
        bad={**env,'MIGRATION_PASSWORD':'intentionally-wrong-fixture-password'}
        with patch('dotenv.dotenv_values',return_value=bad),self.assertRaisesRegex(RuntimeError,'MIGRATION_PASSWORD'):
            prepare_existing_database()
        e=make()
        try:
            with e.connect() as c:
                self.assertIsNone(c.exec_driver_sql("SELECT to_regclass('auth.user_info')").scalar())
        finally:e.dispose()
        e=make('migration_user')
        try:
            with e.connect() as c:self.assertEqual(c.exec_driver_sql('SELECT 1').scalar(),1)
        finally:e.dispose()


    def test_data_version_rotation_flow(self):
        """키 교체: v1 자동 등록 → v2 빈 스키마 생성(구조·외래키·소유자·트리거) → 적재 전 시작 차단 → 준비 완료 → 전환 → v1 정리"""
        from starter import config, pii_pipeline, data_version as dv
        env,make=self.database()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make('migration_user')
        saved=(config.HMAC_KEY,config.DATA_VERSION)
        db=psycopg.connect(host=env['DB_HOST'],port=int(env['DB_PORT']),dbname=env['POSTGRES_DB'],user='admin',password=env['POSTGRES_PASSWORD'],autocommit=True)
        try:
            self.assertEqual(migrate(engine=e),'0007')
            config.use_data_version('v1');config.HMAC_KEY='fixture-old-key'
            self.assertEqual(dv.verify_runtime(engine=e),'v1')
            with patch.dict('os.environ',{'FIXTURE_OLD_KEY':'fixture-old-key','FIXTURE_NEW_KEY':'fixture-new-key'}):
                with self.assertRaises(dv.DataVersionError):dv.create('v2','FIXTURE_OLD_KEY',engine=e)   # 같은 키 재사용 금지
                dv.create('v2','FIXTURE_NEW_KEY',engine=e)
                with self.assertRaises(dv.DataVersionError):dv.create('v2','FIXTURE_NEW_KEY',engine=e)
                q=lambda sql_text:db.execute(sql_text).fetchall()
                self.assertEqual(q("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid IN ('rag_v2.chunks'::regclass) ")[0][0],'rag_user')
                self.assertEqual({r[0] for r in q("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid IN ('vault_v2.person'::regclass,'vault_v2.token_map'::regclass,'vault_v2.person_mention'::regclass)")},{'vault_user'})
                self.assertEqual(q("SELECT confrelid::regclass::text FROM pg_constraint WHERE conrelid='rag_v2.chunks'::regclass AND contype='f'")[0][0],'rag.document_info')
                self.assertEqual(q("SELECT confrelid::regclass::text FROM pg_constraint WHERE conrelid='vault_v2.person_mention'::regclass AND contype='f'")[0][0],'vault_v2.person')
                self.assertTrue(q("SELECT 1 FROM pg_trigger WHERE tgrelid='vault_v2.person'::regclass AND NOT tgisinternal"))
                self.assertFalse(q("SELECT has_table_privilege('rag_user','vault_v2.token_map','SELECT')")[0][0])
                with e.connect() as c:
                    base,copy=capture(c)['rag.chunks']['columns'],None
                    copy=[list(r) for r in c.exec_driver_sql("""SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
                        coalesce(pg_get_expr(d.adbin,d.adrelid),''), a.attidentity FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                        WHERE a.attrelid='rag_v2.chunks'::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""").fetchall()]
                self.assertEqual(copy,base)
                # 적재 전: 서버 시작·준비 완료 모두 막힘
                config.use_data_version('v2');config.HMAC_KEY='fixture-new-key'
                with self.assertRaises(dv.DataVersionError):dv.verify_runtime(engine=e)
                with self.assertRaises(dv.DataVersionError):dv.ready('v2','FIXTURE_NEW_KEY',engine=e)
                # 새 키로 적재한 것처럼 1건 넣고 준비 완료 → 새 키로만 시작 가능
                token=pii_pipeline._token('person','홍길동')
                db.execute("INSERT INTO rag.document_info(document_id) VALUES ('D1')")
                db.execute("INSERT INTO rag_v2.chunks(chunk_id,doc_id,chunk_idx,masked_text,tokens,embedding) VALUES ('D1#0','D1',0,%s,%s,%s::vector)",
                           (token,[token],'['+','.join(['0.1']*int(os.getenv('EMBED_DIM','1024')))+']'))
                db.execute("INSERT INTO vault_v2.token_map(token,label,normalized,originals) VALUES (%s,'person','홍길동','{홍길동}')",(token,))
                with self.assertRaises(dv.DataVersionError):dv.ready('v2','FIXTURE_OLD_KEY',engine=e)
                self.assertEqual(dv.ready('v2','FIXTURE_NEW_KEY',engine=e)['status'],'READY')
                config.HMAC_KEY='fixture-old-key'
                with self.assertRaises(dv.DataVersionError):dv.verify_runtime(engine=e)   # 키만 안 바꾼 경우
                config.HMAC_KEY='fixture-new-key'
                self.assertEqual(dv.verify_runtime(engine=e),'v2')
                self.assertEqual(q("SELECT status FROM auth.data_version WHERE version='v2'")[0][0],'ACTIVE')
                # 예전 버전 정리
                with self.assertRaises(dv.DataVersionError):dv.drop('v1',engine=e)
                dv.retire('v1',engine=e);dv.drop('v1',engine=e)
                config.use_data_version('v1');config.HMAC_KEY='fixture-old-key'
                with self.assertRaises(dv.DataVersionError):dv.verify_runtime(engine=e)
                self.assertEqual(q("SELECT count(*) FROM rag.document_info")[0][0],1)   # 공용 문서 권한은 유지
        finally:
            config.use_data_version(saved[1]);config.HMAC_KEY=saved[0]
            db.close();e.dispose()


if __name__=='__main__':unittest.main()
