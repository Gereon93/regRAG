#!/bin/sh
set -e

if ! ls docs_md/*.md >/dev/null 2>&1; then
  python convert.py
fi

exec uvicorn web.main:app --host 0.0.0.0 --port 8000
