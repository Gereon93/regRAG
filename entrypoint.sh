#!/bin/sh
set -e

if [ ! -f docs_md/.bootstrap ]; then
  if [ -f "docs/CELEX_32022R2554_DE_TXT.pdf" ] && ! ls docs_md/*.md >/dev/null 2>&1; then
    python convert.py
  fi
  mkdir -p docs_md && touch docs_md/.bootstrap
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if ! ls docs_md/*.md >/dev/null 2>&1; then
  echo "Hinweis: Der Korpus ist leer — jede Frage endet im Abstain, bis ein Dokument hochgeladen wird."
  echo "Für den DORA-Grundkorpus das PDF von https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554"
  echo "nach docs/CELEX_32022R2554_DE_TXT.pdf legen und das docs_md-Volume neu anlegen (docker compose down -v)."
fi

exec uvicorn web.main:app --host 0.0.0.0 --port 8000
