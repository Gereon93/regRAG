# 0002 — Bei dünner Beleglage verweigern, statt zu antworten

Status: akzeptiert, Schwellwert unkalibriert

## Kontext

RegRAG beantwortet Fragen zu einer Bankenregulierung. Eine erfundene Antwort auf
eine Compliance-Frage ist teurer als eine verweigerte Antwort: Die verweigerte
Antwort schickt den Nutzer in den Verordnungstext, die erfundene schickt ihn in
eine falsche Sicherheit.

## Entscheidung

`agent.py` prüft vor der Generierung den Retrieval-Score des besten Treffers.
Liegt er unter `MIN_RETRIEVAL_SCORE`, antwortet der Agent mit `ABSTAIN_ANTWORT`
("Nicht eindeutig in DORA belegt.") und ruft das LLM gar nicht erst auf.

## Konsequenzen

- Das LLM kann auf schwacher Beleglage nicht halluzinieren, weil es sie nie sieht.
- Ein zu hoher Schwellwert macht das System nutzlos, ein zu niedriger nutzlos
  vorsichtig-wirkend, aber tatsächlich ungeschützt.

## Offen — der Schwellwert ist geraten

`MIN_RETRIEVAL_SCORE = 0.4` ist ein Startwert aus dem Bauplan, **keine Messung**.

Beobachtet (Cosine, nach [0003](0003-persistenter-chroma-index-mit-cosine.md)):
ein **guter** Treffer auf eine beantwortbare Frage liegt bei **0.72–0.73**.
Der Abstand zur Schwelle ist also groß — was auch heißen kann, dass die Schwelle
zu tief hängt und mittelmäßige Treffer durchlässt.

Der Abstain-Pfad ist bislang **nie ausgelöst worden**. Er ist damit ungetestet,
und ungetestet ist er wertlos. Issue #2 kalibriert ihn gegen ein Eval-Set aus
beantwortbaren und nachweislich nicht beantwortbaren Fragen.

Bis dahin gilt: Die Zahl 0.4 darf in keiner Bewerbung, keinem README und keinem
Gespräch als gemessenes Ergebnis auftreten.
