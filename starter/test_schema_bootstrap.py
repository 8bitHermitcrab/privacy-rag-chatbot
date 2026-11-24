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
        try:self.assertEqual(migrate(engine=e),'0004')
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
                self.assertEqual(head,'0004')
                self.assertFalse(differences(capture(c),contract(head+'_contract.json')))
        finally:e.dispose()

    def test_empty_database_and_idempotence(self):
        env,make=self.database()
        with patch('dotenv.dotenv_values',return_value=env):prepare_existing_database()
        e=make('migration_user')
        try:
            self.assertEqual(migrate(engine=e),'0004')
            self.assertEqual(migrate(engine=e,check=True),'0004')
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
        try:self.assertEqual(migrate(engine=e),'0004')
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
            self.assertEqual([f.result(timeout=30) for f in futures],['0004','0004'])


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



if __name__=='__main__':unittest.main()
