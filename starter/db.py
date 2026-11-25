"""DB 연결. 검색용(rag_user)과 매핑용(vault_user) 계정을 따로 씁니다.

데이터 버전(config.DATA_VERSION)
- 코드의 SQL 은 기본 이름(rag.chunks, vault.token_map, vault.person, vault.person_mention)으로 씁니다.
- 이 파일의 두 연결은 실행 직전에 그 네 표 이름만 현재 데이터 버전의 스키마로 바꿉니다.
  (v1 이면 그대로, v2 면 rag_v2.chunks 처럼). 문서 권한 rag.document_info 와 auth.* 는 바꾸지 않습니다.

- rag_conn()   : rag.chunks 읽기/쓰기. vault 스키마에는 접근할 수 없습니다.
- vault_conn() : vault.token_map 읽기/쓰기. 적재(가명처리) 단계에서만 사용합니다.
  검색·답변 단계 코드는 vault_conn() 을 쓰지 않아야 합니다. (실명 복원은 종합에서 다룸)
"""
import re

import psycopg
from pgvector.psycopg import register_vector

from . import config

_DATA_TABLE_RE = re.compile(r"\b(rag)\.(chunks)\b|\b(vault)\.(token_map|person_mention|person)\b")


def data_sql(query, version=None):
    """SQL 문자열의 데이터 표 4개 이름을 해당 버전 스키마로 바꿉니다. 다른 이름은 건드리지 않습니다."""
    rag, vault = config.data_schemas(version) if version else (config.RAG_DATA_SCHEMA, config.VAULT_DATA_SCHEMA)
    if (rag, vault) == ("rag", "vault") or not isinstance(query, str):
        return query
    return _DATA_TABLE_RE.sub(lambda m: f"{rag}.{m.group(2)}" if m.group(1) else f"{vault}.{m.group(4)}", query)


class DataVersionCursor(psycopg.Cursor):
    def execute(self, query, params=None, **kwargs):
        return super().execute(data_sql(query), params, **kwargs)

    def executemany(self, query, params_seq, **kwargs):
        return super().executemany(data_sql(query), params_seq, **kwargs)


def rag_conn():
    conn = psycopg.connect(config.RAG_DSN, autocommit=False, cursor_factory=DataVersionCursor)
    register_vector(conn)
    return conn


def vault_conn():
    return psycopg.connect(config.VAULT_DSN, autocommit=False, cursor_factory=DataVersionCursor)
