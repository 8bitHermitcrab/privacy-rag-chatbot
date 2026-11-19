"""2026-11-18 정식 테이블 9개 기준 구조."""
from pathlib import Path
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # 매개변수 없는 고정 SQL: 함수 본문의 세미콜론을 분리하지 않고 전달합니다.
    script = (Path(__file__).resolve().parents[1] / "baseline.sql").read_text(encoding="utf-8")
    op.get_bind().connection.driver_connection.execute(script)

def downgrade():
    raise RuntimeError("기존 데이터 보호를 위해 최초 스키마 자동 삭제는 지원하지 않습니다.")
