#!/bin/sh
# macOS 용 시스템 시작 (Windows 의 시스템_시작.cmd 와 같은 일). Finder 에서 더블클릭하거나 터미널에서 ./시스템_시작.command
cd "$(dirname "$0")" || exit 1
export PYTHONIOENCODING=utf-8
if [ ! -x ".venv/bin/python" ]; then
  echo "프로젝트의 Python 가상환경이 없습니다. 최초 설치 후 다시 실행하세요."
  printf "엔터를 누르면 창이 닫힙니다."; read -r _
  exit 1
fi
.venv/bin/python -B -m server_admin --start "$@"
code=$?
if [ "$code" -ne 0 ]; then printf "\n엔터를 누르면 창이 닫힙니다."; read -r _; fi
exit "$code"
