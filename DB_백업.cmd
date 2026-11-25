@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo 프로젝트의 Python 가상환경이 없습니다. 최초 설치 후 다시 실행하세요.
    pause
    exit /b 1
)
rem 인자 없이 실행하면 백업 1개를 만듭니다. (list / verify 도 가능)
"%~dp0.venv\Scripts\python.exe" -B -m starter.db_backup %*
set "BACKUP_EXIT_CODE=%ERRORLEVEL%"
rem 더블클릭(인자 없음)일 때만 결과를 볼 수 있게 멈춥니다. 예약 작업은 create 인자로 실행
if "%~1"=="" pause
exit /b %BACKUP_EXIT_CODE%
