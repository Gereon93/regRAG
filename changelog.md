# Changelog

## 2026-09-30
### Neu
- **Query-Embedding messen:** Reproduzierbarer CPU-/MPS-Benchmark mit Rohmessungen dokumentiert (#10).
### Geändert
- **CPU-Threads im Container:** `OMP_NUM_THREADS` ist standardmäßig 8 und über die Umgebung überschreibbar (#10).
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
