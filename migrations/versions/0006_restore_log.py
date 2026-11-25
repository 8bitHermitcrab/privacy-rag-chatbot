"""채팅 화면 개인정보 복원 기록(auth.restore_log).

팀·직책 정책(0005)에 따라 채팅 답변의 토큰을 원래 값(전체 또는 일부)으로 보여줄 때마다 1행을 남깁니다.
- 누가(user_id, 당시 팀·직책) 어느 문서(doc_ids)의 어떤 토큰(tokens)을 어떤 수준(levels)으로 봤는지 기록합니다.
- 원래 값과 질문 원문은 저장하지 않습니다. 토큰은 가명이므로 감사 추적용으로만 남깁니다.
- 기록은 추가만 합니다. 업무 계정(auth_user)에는 SELECT·INSERT 만 주고 수정·삭제 권한은 주지 않습니다.
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE auth.restore_log (
        log_id bigint GENERATED ALWAYS AS IDENTITY CONSTRAINT restore_log_pkey PRIMARY KEY,
        user_id bigint NOT NULL CONSTRAINT restore_log_user_id_fkey REFERENCES auth.user_info(user_id),
        team_id text,
        position_code text,
        doc_ids text[] NOT NULL DEFAULT '{}',
        tokens text[] NOT NULL CONSTRAINT restore_log_tokens_check CHECK (cardinality(tokens) > 0),
        levels jsonb NOT NULL CONSTRAINT restore_log_levels_check CHECK (jsonb_typeof(levels) = 'object'),
        created_at timestamptz NOT NULL DEFAULT now()
    )''')
    op.execute('CREATE INDEX ix_restore_log_user_time ON auth.restore_log (user_id, created_at)')
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='auth_user') THEN
            GRANT SELECT,INSERT ON auth.restore_log TO auth_user;
        END IF;
    END $$""")


def downgrade():
    raise RuntimeError('복원 기록 보존을 위해 자동 되돌리기는 제공하지 않습니다.')
