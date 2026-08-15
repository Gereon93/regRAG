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

## Gemessen

Der Vergleichslauf existiert inzwischen: `evaluation/baseline_pypdf.py` extrahiert
dieselbe PDF mit pypdf in einen zweiten Korpus, der über dieselbe Pipeline
indexiert wird. `evaluation.calibrate` läuft gegen beide Indizes, gleiches
Eval-Set, gleiches Embedding-Modell, gleiche Cosine-Metrik.

| Größe (Top-1-Score, `similarity_top_k=3`) | pypdf | Markdown |
|---|---|---|
| 8 beantwortbare DORA-Fragen, Ø | 0.678 | **0.724** |
| dieselben, Minimum | 0.634 | **0.652** |
| 6 themenfremde Fragen, Maximum | 0.581 | **0.567** |
| Trennlücke (Minimum beantwortbar − Maximum themenfremd) | 0.053 | **0.085** |
| Guard-Entscheidungen | 14/14 | 14/14 |

Die Trennlücke ist die Spanne, in der ein Schwellwert liegen darf, ohne einen Fall falsch zu entscheiden: `0.634 − 0.581 = 0.053` gegen `0.652 − 0.567 = 0.085`. Nicht die Differenz der Mittelwerte — für den Guard zählt der schlechteste beantwortbare Treffer gegen den besten themenfremden, nicht der Durchschnitt.

Der Gewinn ist real, aber anders gelagert als erwartet: die Markdown-Variante
zieht die beantwortbaren Fragen um Ø 0.046 nach oben *und* die themenfremden
leicht nach unten. Zusammen ergibt das eine **1.6-fach breitere Trennlücke**
(0.085 statt 0.053) — also mehr Sicherheitsabstand für den Schwellwert aus
[0005](0005-guard-kalibriert-abstain-als-bedingte-kante.md).

Ehrlich dazu: **die pypdf-Variante trennt das Eval-Set ebenfalls 14/14.** Die
Markdown-Konvertierung rettet hier kein kaputtes System, sie verbreitert die
Marge. Bei 0.053 Lücke liegt der Schwellwert deutlich enger an beiden Gruppen;
Grenzfälle nahe der Schwelle kippen dort früher.

Qualitativ zeigt der pypdf-Text den erwarteten Schaden — Wörter zerfallen an
Zeichenabstandsgrenzen (`Mechanis mus`, `Identifizier ung`), Kopfzeilen und
Fußnotenziffern stehen mitten im Fließtext. Dass die Scores trotzdem tragen,
liegt an der Robustheit von bge-m3 gegen genau solches Rauschen.

## Offen

Gemessen ist die **Retrieval-Trennung**, nicht die Antwortqualität. Ob die
zerrissenen Chunks der pypdf-Variante die Faithfulness senken, ist offen — dafür
müsste `evaluation.run` mit Judge gegen beide Korpora laufen.

## Reproduzieren

```bash
python -m evaluation.baseline_pypdf
python -m evaluation.calibrate                 # Markdown-Korpus
REGRAG_DOCS_DIR=docs_md_pypdf REGRAG_CHROMA_DIR=chroma_pypdf \
  REGRAG_COLLECTION=dora_pypdf python -m evaluation.calibrate
```
