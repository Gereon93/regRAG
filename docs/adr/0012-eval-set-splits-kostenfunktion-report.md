# 0012 — Versioniertes Eval-Set, Splits, Kostenfunktion und Segment-Report

## Status

Akzeptiert

## Kontext

Der Schwellwert wurde über Accuracy auf 14 Python-Listen-Fällen gewählt (`evaluation/calibrate.py`).
Bei regulatorischen Inhalten ist eine Falschantwort deutlich teurer als eine Eskalation
([0002](0002-abstain-statt-raten.md)); Accuracy bildet das nicht ab. Außerdem fehlten Segmente,
getrennte Splits und eine Version, auf die sich ein Schwellwert zurückführen lässt (#31, Teil von #26).

## Entscheidung

- **Format:** `evaluation/evalset/v1.jsonl`, ein Fall pro Zeile: `id`, `frage`, `erwartet`, `segment`
  (Quellentyp), `kategorie`, `gruppe`. `evaluation/dataset.py` leitet daraus die bisherigen Listen ab.
- **Version:** SHA-256 über die nach `id` sortierten Fälle. Jede Änderung an Frage, Label, Segment oder
  Gruppe ändert die Version. Jeder Bericht trägt sie.
- **Split:** 60/20/20 train/calibration/test per Hash über `gruppe` (Fallback `id`). Deterministisch und
  stabil beim Hinzufügen. `gruppe` fasst Fragen zur selben Norm bzw. Normfassung zusammen, damit sie
  nicht über Splits verteilt werden. Das ist unsere Lesart von "kein Leakage zwischen Normfassungen".
- **Kosten:** `REGRAG_KOSTEN_FALSCHANTWORT` (Default 10) und `REGRAG_KOSTEN_ESKALATION` (Default 1).
  Die Defaults sind eine Startannahme, keine gemessene Größe.
- **Schwelle pro Segment:** minimiert die Gesamtkosten auf dem Calibration-Split. Bei Kostengleichheit
  gewinnt die höhere, also vorsichtigere Schwelle.
- **Report:** `python -m evaluation.eval_report` wählt Schwellen auf calibration und misst auf test.
  Pro Segment und gesamt: Falschantwortrate (an den Beantworteten), Eskalationsrate, Coverage, je mit
  95%-Wilson-Intervall. Das Artefakt liegt unter `evaluation/reports/` und ist nicht eingecheckt.

## Konsequenzen

- Gemessen wird die **Gate-Entscheidung** auf dem Retrieval-Score, nicht die inhaltliche Korrektheit
  der generierten Antwort.
- Das Set hat heute 14 Fälle. Das Ziel von >= 200 gelabelten Fragen erfüllt dieses Issue **nicht**;
  Labeln liegt beim Maintainer. Der Runner warnt, solange n < 200. Bis dahin sind die Intervalle
  breit und die Schwellen nicht belastbar.
- Conformal/Selective Prediction (im Issue optional) ist nicht umgesetzt. Die Falschantwortrate an den
  Beantworteten ist die passende Zielgröße dafür, sobald genug Labels vorliegen.
- Die Schwellen werden nicht automatisch in die Laufzeit-Config geschrieben. Wer sie übernimmt,
  trägt die `eval_version` aus dem Bericht in den Commit ein.
