#!/bin/sh
set -e

if ! ls docs_md/*.md >/dev/null 2>&1 && [ -f "docs/CELEX_32022R2554_DE_TXT.pdf" ]; then
  python convert.py
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if ! ls docs_md/*.md >/dev/null 2>&1; then
  echo "Fehler: docs/CELEX_32022R2554_DE_TXT.pdf nicht gefunden."
  echo "Bitte das DORA-PDF von https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554 herunterladen und nach docs/CELEX_32022R2554_DE_TXT.pdf speichern (oder per Docker-Volume einbinden)."
  exit 1
fi

exec uvicorn web.main:app --host 0.0.0.0 --port 8000
