# 0001 — PDF nach Markdown konvertieren, statt das PDF direkt zu indexieren

Status: akzeptiert

## Kontext

Der DORA-Text kommt von EUR-Lex als PDF (CELEX 32022R2554). Amtsblatt-PDFs sind
zweispaltig gesetzt, mit Kopf-/Fußzeilen, Randnummern und Fußnoten.

Ein naiver PDF-Reader (pypdf, wie im ursprünglichen Bauplan) liest solche Seiten
zeilenweise über beide Spalten hinweg. Das Ergebnis vermischt zwei inhaltlich
unabhängige Textspalten in einem Absatz. Chunks, die daraus entstehen, enthalten
Satzfragmente aus zwei verschiedenen Artikeln.

## Entscheidung

`convert.py` wandelt das PDF mit `pymupdf4llm` nach Markdown (`docs_md/dora.md`).
Indexiert wird das Markdown, nicht das PDF.

## Konsequenzen

- Die Artikel- und Absatzstruktur der Verordnung bleibt erhalten. Chunks fallen
  entlang inhaltlicher Grenzen statt entlang von Seitenlayout.
- Ein frischer Clone braucht einen `python convert.py`-Lauf, bevor `rag.py`
  funktioniert. `docs_md/` ist ein Generat und liegt daher nicht im Repo.
- Zusätzliche Abhängigkeit: `pymupdf4llm`.

## Offen

Der Qualitätsgewinn ist plausibel, aber **nicht gemessen**. Es existiert kein
Vergleichslauf gegen die pypdf-Variante. Wer diese Zahl braucht, muss sie
erheben — siehe [0002](0002-abstain-statt-raten.md) und Issue #2.
