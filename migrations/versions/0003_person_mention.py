"""문서별 인물 등장 기록(vault.person_mention).

vault.person 이 '사람 명부'라면 person_mention 은 '어느 문서에 누가 어떤 정보와 함께 나왔고
어떻게 판정했는지'를 남기는 근거 장부입니다. 자동 판정과 관리자 수동 판정을 함께 기록합니다.
- 문서 안 인물 묶음 1개 = 1행 (owner_key = '이름토큰|역할')
- 주인을 특정하지 못한 정보도 1행 (owner_key = 'orphan|유형|토큰|줄', name_token NULL)
- pid 는 vault.person 에 대한 실제 FK, doc_id·토큰은 계정 분리 설계상 논리 참조입니다.
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE vault.person_mention (
        mention_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        doc_id text NOT NULL CHECK (btrim(doc_id) <> ''),
        owner_key text NOT NULL CHECK (btrim(owner_key) <> ''),
        role text,
        name_token text,
        data1 text[] NOT NULL DEFAULT '{}',
        data2 text[] NOT NULL DEFAULT '{}',
        data3 text[] NOT NULL DEFAULT '{}',
        data4 text[] NOT NULL DEFAULT '{}',
        data5 text[] NOT NULL DEFAULT '{}',
        data6 text[] NOT NULL DEFAULT '{}',
        data7 text[] NOT NULL DEFAULT '{}',
        pid bigint REFERENCES vault.person(pid) ON DELETE SET NULL,
        status text NOT NULL CHECK (status IN ('linked','created','held','manual_linked','manual_created','shared','dismissed')),
        reason text NOT NULL CHECK (btrim(reason) <> ''),
        decided_by text NOT NULL DEFAULT 'auto' CHECK (decided_by IN ('auto','manual')),
        review_note text,
        reviewed_at timestamptz,
        policy_version text NOT NULL,
        sensitivity text NOT NULL CHECK (sensitivity IN ('strict','balanced','relaxed')),
        source_hash text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (doc_id, owner_key),
        CHECK ((name_token IS NULL) = (owner_key LIKE 'orphan|%')),
        CHECK (status NOT IN ('linked','created','manual_linked','manual_created') OR pid IS NOT NULL),
        CHECK ((decided_by = 'manual') = (status LIKE 'manual\\_%' OR status IN ('shared','dismissed')))
    )''')
    op.execute('CREATE INDEX person_mention_pid_idx ON vault.person_mention (pid)')
    op.execute('CREATE INDEX person_mention_name_token_idx ON vault.person_mention (name_token)')
    op.execute("CREATE INDEX person_mention_review_idx ON vault.person_mention (status) WHERE status = 'held'")
    op.execute('''CREATE TRIGGER trg_person_mention_set_updated_at BEFORE UPDATE ON vault.person_mention
        FOR EACH ROW EXECUTE FUNCTION vault.set_person_updated_at()''')
    # 다른 vault 테이블과 같이 매핑 계정이 소유합니다. 검색 계정(rag_user)에는 권한을 주지 않습니다.
    op.execute('ALTER TABLE vault.person_mention OWNER TO vault_user')


def downgrade():
    raise RuntimeError('판정·검토 기록 보존을 위해 자동 되돌리기는 제공하지 않습니다.')
