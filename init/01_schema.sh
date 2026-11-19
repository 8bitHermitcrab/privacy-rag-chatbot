#!/bin/bash
# DB 계정·pgvector 준비 전용. 테이블은 앱 기동 시 Alembic으로 생성·변경합니다.
# 과거 초기화 코드는 Git 기록과 별도 백업에서 관리합니다.
set -euo pipefail
: "${MIGRATION_PASSWORD:?MIGRATION_PASSWORD를 설정하세요}"
: "${RAG_PASSWORD:?RAG_PASSWORD를 설정하세요}"
: "${VAULT_PASSWORD:?VAULT_PASSWORD를 설정하세요}"
psql -X -v ON_ERROR_STOP=1 --single-transaction --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v migration_password="$MIGRATION_PASSWORD" -v rag_password="$RAG_PASSWORD" \
    -v vault_password="$VAULT_PASSWORD" -v bootstrap_db="$POSTGRES_DB" <<'BOOTSTRAP_SQL'
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
SELECT format('CREATE ROLE rag_user LOGIN PASSWORD %L', :'rag_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_user')
\gexec
SELECT format('CREATE ROLE vault_user LOGIN PASSWORD %L', :'vault_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vault_user')
\gexec
SELECT format('CREATE ROLE migration_user LOGIN PASSWORD %L', :'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='migration_user')
\gexec
GRANT rag_user, vault_user TO migration_user;
GRANT CONNECT, CREATE ON DATABASE :"bootstrap_db" TO migration_user;
GRANT CONNECT ON DATABASE :"bootstrap_db" TO rag_user, vault_user;
GRANT USAGE, CREATE ON SCHEMA public TO migration_user;
BOOTSTRAP_SQL
