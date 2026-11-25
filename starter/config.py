"""환경변수 설정. 값은 프로젝트 루트의 .env 에서 읽습니다."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name, default=None):
    return os.getenv(name, default)


DB_HOST = env("DB_HOST", "localhost")
DB_PORT = int(env("DB_PORT", "5432"))
DB_NAME = env("POSTGRES_DB", "pii_rag")
RAG_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=rag_user password={env('RAG_PASSWORD', '')}"
VAULT_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=vault_user password={env('VAULT_PASSWORD', '')}"

EMBED_PROVIDER = env("EMBED_PROVIDER", "local")  # api | local
EMBED_API_BASE = env("EMBED_API_BASE", "")
EMBED_API_KEY = env("EMBED_API_KEY", "")
EMBED_MODEL = env("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(env("EMBED_DIM", "1536"))

LLM_API_BASE = env("LLM_API_BASE", "")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_MODEL = env("LLM_MODEL", "gpt-4o-mini")

HMAC_KEY = env("HMAC_KEY", "")

# ---------------------------------------------------------------- 데이터 버전 (HMAC 키 교체용)
# 키가 바뀌면 토큰·가명 본문·임베딩·매핑이 모두 달라지므로, 새 키 데이터는 별도 스키마에 따로 만듭니다.
#   v1  → rag.chunks, vault.token_map, vault.person, vault.person_mention (처음부터 쓰던 기본 스키마)
#   v2~ → rag_v2.chunks, vault_v2.token_map ... (python -m starter.data_version create v2 로 생성)
# 문서 권한(rag.document_info)과 계정·정책(auth.*)은 버전과 관계없이 하나만 두고 함께 씁니다.
# 서버는 .env 의 DATA_VERSION 과 HMAC_KEY 를 함께 읽고, 시작할 때 두 값이 짝이 맞는지 확인합니다.
import re as _re

DATA_VERSION_PATTERN = _re.compile(r"^v[1-9][0-9]{0,2}$")
DATA_TABLES = ("rag.chunks", "vault.token_map", "vault.person", "vault.person_mention")


def data_schemas(version):
    """데이터 버전 → (검색 스키마, 매핑 스키마)"""
    if not DATA_VERSION_PATTERN.fullmatch(version or ""):
        raise ValueError("DATA_VERSION 은 v1, v2 같은 형식이어야 합니다: " + repr(version))
    return ("rag", "vault") if version == "v1" else (f"rag_{version}", f"vault_{version}")


def use_data_version(version):
    """이 프로세스가 읽고 쓸 데이터 버전을 정합니다. (적재 도구가 새 버전에 쓸 때 사용)"""
    global DATA_VERSION, RAG_DATA_SCHEMA, VAULT_DATA_SCHEMA
    RAG_DATA_SCHEMA, VAULT_DATA_SCHEMA = data_schemas(version)
    DATA_VERSION = version


DATA_VERSION = None
RAG_DATA_SCHEMA = VAULT_DATA_SCHEMA = None
use_data_version((env("DATA_VERSION", "v1") or "v1").strip())

DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "outputs"
