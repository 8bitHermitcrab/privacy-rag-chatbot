"""개인별 인물 검토 권한(auth.user_info.can_review_persons).

업무 관리자 화면의 '인물 검토' 메뉴는 원문 개인정보를 보여 주므로, 계정 관리 권한이 있는 관리자 중에서도
다른 관리자가 지정한 사람만 사용할 수 있게 합니다. 기본값은 false 라 기존 계정에는 권한이 없습니다.
"""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE auth.user_info ADD COLUMN can_review_persons boolean NOT NULL DEFAULT false')


def downgrade():
    raise RuntimeError('권한 기록 보존을 위해 자동 되돌리기는 제공하지 않습니다.')
