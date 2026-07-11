#!/bin/sh
set -e

if [ ! -f docs_md/dora.md ]; then
  python convert.py
fi

exec uvicorn web.main:app --host 0.0.0.0 --port 8000
