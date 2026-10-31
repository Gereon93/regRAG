# Changelog

## 2026-10-31
### Neu
- **Eval-Set versioniert:** `evaluation/evalset/v1.jsonl` mit Segment, Kategorie und Gruppe; Version als SHA-256 über den Inhalt (#31).
- **Schwellen über Kosten:** Falschantwort-/Eskalationsgewicht als Config, Schwelle pro Segment auf dem Calibration-Split (#31).
- **Segment-Report:** `python -m evaluation.eval_report` berichtet auf dem Test-Split Falschantwortrate, Eskalationsrate und Coverage mit 95%-Wilson-Intervall (#31).
### Hinweis
- Das Set hat 14 Fälle; die geforderten >= 200 gelabelten Fragen stehen aus (Labeln liegt beim Maintainer).

## 2026-09-30
### Neu
- **Query-Embedding messen:** Reproduzierbarer CPU-/MPS-Benchmark mit Rohmessungen dokumentiert (#10).
### Geändert
- **CPU-Threads im Container:** `OMP_NUM_THREADS` ist standardmäßig 8 und über die Umgebung überschreibbar (#10).
- **Alternative Embedding-Modelle:** Compose reicht `REGRAG_EMBEDDING_MODELL` durch; andere Modelle werden bei verfügbarer Netzverbindung geladen (#10).
### Behoben
- **Offline-Modellcache:** Der Container nutzt beim Start den bereits eingebetteten Hugging-Face-Cache (#10).
### Technik
- Die Query- und Indexinitialisierung verwenden dieselbe Embedding-Fabrik (#10).

## 2026-09-29
### Neu
- Lokaler Accessibility-Pre-Commit-Check für `web/static/index.html` mit `html-validate` (#23).
### Geändert
- Das Frage-Eingabefeld hat ein sichtbares Label (#23).
### Behoben
- Das Frageformular läuft auf schmalen Viewports nicht mehr horizontal über (#23).
