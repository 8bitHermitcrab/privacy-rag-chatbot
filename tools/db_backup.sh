#!/usr/bin/env bash
# 서버 3(우분투)용 DB 백업 실행 파일. cron 에서 이 파일을 실행합니다. (절차: docs/DB_백업_복원.md)
#   - 저장소 폴더의 .venv 파이썬으로 python -m starter.db_backup create 실행
#   - .env 에 DB_BACKUP_COPY_TO 가 있으면 서버 2 로 복사
#   - 결과는 백업 폴더 옆 db_backup.log 에 덧붙임 (비밀번호·개인정보는 기록하지 않음)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
LOG="${DB_BACKUP_LOG:-$HOME/.local/share/rag-server-admin/db_backup.log}"
mkdir -p "$(dirname "$LOG")" && chmod 700 "$(dirname "$LOG")"
{
  echo "=== $(date '+%F %T') 백업 시작"
  .venv/bin/python -B -m starter.db_backup create
  code=$?
  echo "=== $(date '+%F %T') 종료코드 $code"
} >> "$LOG" 2>&1
exit $code
