"""직원 이름 토큰 의존 제거 및 팀·직책별 정책."""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    # 새 DB에도 계정 관리용 기본 역할을 준비합니다. 기존 역할 설정은 덮어쓰지 않습니다.
    op.execute("""INSERT INTO auth.role_info(role_code,role_name,search_scope,can_restore,can_manage_users)
        VALUES ('STAFF','사원','NONE',false,false),('TEAM_LEAD','팀장','NONE',false,false),('ADMIN','업무 관리자','NONE',false,true)
        ON CONFLICT(role_code) DO NOTHING""")
    op.execute('ALTER TABLE auth.employee_info DROP CONSTRAINT employee_info_check')
    op.execute("ALTER TABLE auth.employee_info ADD CONSTRAINT employee_info_check CHECK (verification_status <> 'CONFIRMED' OR team_id IS NOT NULL)")
    op.execute('''CREATE TABLE auth.team_position_policy (
        team_id text NOT NULL REFERENCES auth.team_info(team_id),
        position_code text NOT NULL CHECK (btrim(position_code) <> ''),
        search_scope text NOT NULL CHECK (search_scope IN ('OWN','TEAM','NONE')),
        can_restore boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (team_id,position_code),
        CHECK (NOT can_restore OR search_scope <> 'NONE')
    )''')
    op.execute('''CREATE TRIGGER trg_team_position_policy_updated_at BEFORE UPDATE
        ON auth.team_position_policy FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at()''')
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='auth_user') THEN
            GRANT SELECT,INSERT,UPDATE ON auth.team_position_policy TO auth_user;
        END IF;
    END $$""")


def downgrade():
    raise RuntimeError('직원·정책 데이터 보존을 위해 자동 되돌리기는 제공하지 않습니다.')
