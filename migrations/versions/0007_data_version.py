"""데이터 버전 등록부(auth.data_version) — HMAC 키 무중단 교체용.

키가 바뀌면 토큰·가명 본문·임베딩·매핑이 모두 달라지므로 새 키 데이터는 별도 스키마(rag_v2, vault_v2 …)에 만들고,
.env 의 HMAC_KEY 와 DATA_VERSION 을 함께 바꾼 뒤 서버를 한 대씩 재시작해 넘어갑니다.
이 표는 버전마다 어느 스키마를 쓰는지와 키 지문(키 자체가 아닌 HMAC 확인값 16자리)을 기록해,
서버가 시작할 때 '키와 데이터 버전의 짝'이 맞지 않으면 시작을 막는 데 씁니다.
- 상태: BUILDING(적재 중) → READY(적재 완료) → ACTIVE(서버가 사용) → RETIRED(사용 종료)
- v1(기본 스키마 rag·vault)은 서버가 처음 시작할 때 현재 키 지문으로 자동 등록합니다.
"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE auth.data_version (
        version text CONSTRAINT data_version_pkey PRIMARY KEY
            CONSTRAINT data_version_version_check CHECK (version ~ '^v[1-9][0-9]{0,2}$'),
        rag_schema text NOT NULL,
        vault_schema text NOT NULL,
        key_fingerprint text NOT NULL CONSTRAINT data_version_key_fingerprint_check CHECK (key_fingerprint ~ '^[0-9a-f]{16}$'),
        status text NOT NULL CONSTRAINT data_version_status_check CHECK (status IN ('BUILDING','READY','ACTIVE','RETIRED')),
        note text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT data_version_schema_check CHECK (
            (version = 'v1' AND rag_schema = 'rag' AND vault_schema = 'vault')
            OR (version <> 'v1' AND rag_schema = 'rag_' || version AND vault_schema = 'vault_' || version))
    )''')
    op.execute('''CREATE TRIGGER trg_data_version_updated_at BEFORE UPDATE ON auth.data_version
        FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at()''')


def downgrade():
    raise RuntimeError('키 교체 이력 보존을 위해 자동 되돌리기는 제공하지 않습니다.')
