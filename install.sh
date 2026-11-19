#!/bin/sh
set -e
pip install -r requirements.txt
pip install --no-deps "git+https://github.com/kohjunseo1/AegisRAG.git@644ab9d56cd24d5386c739712488299448940d26#subdirectory=step1_pii_detector"
