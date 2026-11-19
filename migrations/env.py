"""잠금·검증을 수행하는 공통 실행기에서 연결을 전달받습니다."""
from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("python -m starter.migrate를 사용하세요. 직접 실행은 지원하지 않습니다.")
context.configure(connection=connection, target_metadata=None, transactional_ddl=True, version_table_schema="public")
with context.begin_transaction():
    context.run_migrations()
