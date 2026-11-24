"""팀·직책 정책별 개인정보 7종 복원 수준(auth.team_position_policy.restore_*).

마킹(가명처리)은 항상 7종 모두 수행하고, 화면에 보여줄 때 어느 종류를 원래 값으로 되돌릴지를 정합니다.
값은 NONE(안 함) / PARTIAL(일부) / FULL(전체)이며 기본은 NONE 이라 기존 정책의 동작은 바뀌지 않습니다.
주민번호·계좌·카드는 PARTIAL 까지만 허용합니다. 기존 can_restore 는 전체 스위치로, 꺼져 있으면 7종 모두 NONE 이어야 합니다.

열 순서를 '정책 값 → 복원 7종 → 생성·수정 일시'로 맞추기 위해 표를 다시 만들고 기존 행을 그대로 옮깁니다.
(PostgreSQL 은 ADD COLUMN 을 항상 맨 뒤에 붙이므로) 제약 이름·트리거·소유자·권한은 기존과 같게 유지합니다.
"""
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

FULL_ALLOWED = ('person', 'phone', 'email', 'address')
PARTIAL_ONLY = ('rrn', 'account', 'card')
KINDS = FULL_ALLOWED + PARTIAL_ONLY


def upgrade():
    op.execute('LOCK TABLE auth.team_position_policy IN ACCESS EXCLUSIVE MODE')
    op.execute('ALTER TABLE auth.team_position_policy RENAME TO team_position_policy_v4')
    op.execute('ALTER TABLE auth.team_position_policy_v4 RENAME CONSTRAINT team_position_policy_pkey TO team_position_policy_v4_pkey')
    op.execute('DROP TRIGGER IF EXISTS trg_team_position_policy_updated_at ON auth.team_position_policy_v4')

    restore_cols = []
    for kind in FULL_ALLOWED:
        restore_cols.append(f"restore_{kind} text NOT NULL DEFAULT 'NONE' "
                            f"CONSTRAINT ck_policy_restore_{kind} CHECK (restore_{kind} IN ('NONE','PARTIAL','FULL'))")
    for kind in PARTIAL_ONLY:
        restore_cols.append(f"restore_{kind} text NOT NULL DEFAULT 'NONE' "
                            f"CONSTRAINT ck_policy_restore_{kind} CHECK (restore_{kind} IN ('NONE','PARTIAL'))")
    switch = ' AND '.join(f"restore_{k} = 'NONE'" for k in KINDS)
    op.execute(f'''CREATE TABLE auth.team_position_policy (
        team_id text NOT NULL CONSTRAINT team_position_policy_team_id_fkey REFERENCES auth.team_info(team_id),
        position_code text NOT NULL CONSTRAINT team_position_policy_position_code_check CHECK (btrim(position_code) <> ''),
        search_scope text NOT NULL CONSTRAINT team_position_policy_search_scope_check CHECK (search_scope IN ('OWN','TEAM','NONE')),
        can_restore boolean NOT NULL DEFAULT false,
        {', '.join(restore_cols)},
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT team_position_policy_pkey PRIMARY KEY (team_id, position_code),
        CONSTRAINT team_position_policy_check CHECK (NOT can_restore OR search_scope <> 'NONE'),
        CONSTRAINT ck_policy_restore_needs_switch CHECK (can_restore OR ({switch}))
    )''')
    # 기존 행을 그대로 옮깁니다. 생성·수정 일시도 보존하고, 복원 7종은 NONE 으로 시작합니다.
    op.execute('''INSERT INTO auth.team_position_policy(team_id,position_code,search_scope,can_restore,created_at,updated_at)
        SELECT team_id,position_code,search_scope,can_restore,created_at,updated_at FROM auth.team_position_policy_v4''')
    # 기존 표에 있던 권한(소유자 제외)을 새 표에 그대로 다시 부여합니다.
    op.execute('''DO $$ DECLARE r record; BEGIN
        FOR r IN SELECT g.rolname, a.privilege_type
                 FROM pg_class c, aclexplode(c.relacl) a JOIN pg_roles g ON g.oid = a.grantee
                 WHERE c.oid = 'auth.team_position_policy_v4'::regclass AND a.grantee <> c.relowner LOOP
            EXECUTE format('GRANT %s ON auth.team_position_policy TO %I', r.privilege_type, r.rolname);
        END LOOP;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='auth_user') THEN
            GRANT SELECT,INSERT,UPDATE ON auth.team_position_policy TO auth_user;
        END IF;
    END $$''')
    op.execute('DROP TABLE auth.team_position_policy_v4')
    op.execute('''CREATE TRIGGER trg_team_position_policy_updated_at BEFORE UPDATE
        ON auth.team_position_policy FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at()''')


def downgrade():
    raise RuntimeError('정책 데이터 보존을 위해 자동 되돌리기는 제공하지 않습니다.')
