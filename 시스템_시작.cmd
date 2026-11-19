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
"%~dp0.venv\Scripts\python.exe" -B -m server_admin --start %*
set "START_EXIT_CODE=%ERRORLEVEL%"
if not "%START_EXIT_CODE%"=="0" pause
exit /b %START_EXIT_CODE%
