#!/bin/sh
set -e
python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 'Python 3.12가 필요합니다. 지금: '+sys.version.split()[0])"
pip install -r requirements.txt
pip install --no-deps "git+https://github.com/kohjunseo1/AegisRAG.git@644ab9d56cd24d5386c739712488299448940d26#subdirectory=step1_pii_detector"
