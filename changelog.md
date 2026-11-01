# Changelog

## 2026-11-01
### Neu
- **Laya-Kalibrierung:** Temperature Scaling auf dem Calibration-Split, ECE auf dem Test-Split; `python -m evaluation.eval_report` erzeugt einen Laya-Teil, wenn `REGRAG_LAYA_URL` gesetzt ist (#32).
- **Segmentschwellen für Laya:** Segment im `.source.json`-Sidecar (`gesetz`, `verordnung`, `richtlinie`, `leitlinie`, `auslegung`), sonst `unbekannt` mit strengstem Fallback (#32).
- **Gate-Manifest:** `--checkpoint-revision` schreibt Schwellen, Temperatur, Checkpoint und `eval_version` nach `evaluation/manifest/`; `REGRAG_GATE_MANIFEST` aktiviert es, `REGRAG_LAYA_MODELL` wählt den Checkpoint (#32).
- **Vergleich:** `python -m evaluation.vergleich` vergleicht zwei Reports per Cluster-Bootstrap über die Gruppen (#32).
- **Eskalationsmetriken:** `GET /metrics` mit Raten pro Reason und Segment sowie Score-Histogrammen; der Eskalationsdatensatz trägt `segment` (#32).
### Geändert
- Ein gesetztes, aber fehlendes oder ungültiges Manifest lässt den Start fehlschlagen; unter Manifest führt ein abweichendes oder malformtes `routing` bzw. `routing.model` zu ABSTAIN `JUDGE_UNAVAILABLE`; explizites `null` zählt dabei als malformt; fehlt der Schlüssel `routing.model` ganz, warnt das Gate einmalig "Checkpoint nicht verifizierbar".
- Der Temperatur-Fit rechnet die NLL aus dem skalierten Logit statt aus einer geklemmten Wahrscheinlichkeit; ein sicher falsches Calibration-Sample zieht T nicht mehr an den unteren Rand (#32).
- Manifest und Laya-Report tragen die Revision plus einen kurzen Hash der Originalrevision im Namen; Baseline und Kandidat auf demselben Eval-Set überschreiben sich nicht mehr, auch bei gleich bereinigten Revisionen (#32).
- Der Retrieval-Guard behandelt einen NaN-Score als zu niedrig (ABSTAIN `LOW_RELEVANCE`); `evaluation.vergleich` lehnt Reports mit abweichendem `laya.index_fingerprint` ab (#32).
### Hinweis
- Bestandsindizes kennen kein `segment` und gelten als `unbekannt`; ein ergänztes Segment im Sidecar indexiert das Dokument automatisch neu, `REGRAG_INDEX_NEU_BAUEN=1` wendet Segmente auf alle Dokumente auf einmal an. `segment` fließt nicht in den Embedding-Text.
- **Offen:** "Fine-getuntes Laya schlägt Zero-Shot signifikant" ist nicht gemessen; es fehlen Labels und ein trainierter Checkpoint. Nicht umgesetzt: Fine-Tuning, `relevance`-Judge, Raten-Warnung, Trainingsexport (ADR 0013).

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
