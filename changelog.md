# Changelog

## 2026-11-12
### Neu
- **Rate-Limit für `/chat`:** `REGRAG_CHAT_MAX_PRO_MINUTE` (Default 10, ungemessen) Anfragen je Client-IP und Minute, darüber 429 mit `Retry-After`. Der Zähler gilt je Prozess (#44, ADR 0016).
- **Body-Grenze für `/chat`:** über 32 000 Bytes 413, bevor der Body gelesen oder geparst wird (#44).
### Fix
- Die UI zeigt eine abgelehnte Chat-Anfrage (413, 422, 429) als Fehler statt eines hängenden Cursors (#44).

## 2026-11-11
### Geändert
- **Gate-Manifest Schema 2:** Pflichtfeld `index_fingerprint`; Schema-1-Manifeste lassen den Start fehlschlagen (neu erzeugen). Ohne `chroma/fingerprint.json` schreibt der Report kein Manifest (#41, ADR 0013).
- Beim Start warnt `baue_gate`, wenn das Manifest auf einem anderen Index kalibriert wurde; kein Startfehler (#41).
- `index_fingerprint` ist SHA-256 über den kanonischen Inhalt von `fingerprint.json`; ältere Reports sind mit neuen nicht mehr vergleichbar (#41).
### Fix
- Calibration-Segmente nur mit `answer` bekommen keine eigene Laya-Schwelle mehr: die Kostenfunktion hätte sie auf den niedrigsten gesehenen Score gesetzt. Sie fallen auf `fallback_schwelle` zurück, das Maximum über alle Segmente einschließlich der ausgeschlossenen (#40, ADR 0013).
- Bleibt kein belastbares Segment übrig, schreibt der Report kein Manifest und warnt `Kein Manifest: alle Segmente einseitig …` (#40).

## 2026-11-04
### Neu
- **Claim-Level-Grounding:** Der Prompt verlangt Marker `[n]` je Aussage. Fehlt ein Marker, ist er ungültig oder steht eine Zahl, Norm-Nummer oder Zahl-Einheit-Angabe nicht in den zitierten Chunks, wird die Antwort verworfen (`UNGROUNDED_CLAIM`, Eskalationsdatensatz mit `antwort`, `aussagen_gesamt`, `aussagen_ohne_beleg`). Aussagen ohne Zahl bleiben inhaltlich ungeprüft (#29, ADR 0014).
- `python -m evaluation.befolgung` misst, wie oft das lokale Modell das Markerformat einhält (#29).
- SSE-Event `status`, Feld `nr` in `sources`; die UI zeigt den Wartetext und die Nummern der Fundstellen (#29).
- **Gültigkeitsmetadaten:** `gueltig_ab`, `gueltig_bis`, `fassung` im Sidecar und am `POST /upload`; ein kaputter Sidecar nimmt nur dieses Dokument aus dem Index (#30, ADR 0015).
- **`OUTDATED_SOURCE`:** Das Gate prüft gegen heute (Europe/Berlin). Abgelaufene Treffer verschwinden neben einer gültigen Fassung aus Kontext und Quellenliste; ohne gültige Fassung verweigert es (#30).
- **`AMBIGUOUS_VERSION`:** Zwei nutzbare Fassungen desselben Titels verweigern; neue Reason, kein Normkonflikt (#30).
- **Konflikt-Judge:** `REGRAG_LAYA_KONFLIKT_SCHWELLE` schaltet `CONFLICTING_PROVISIONS` über Laya ein. Default aus, kein gemessener Wert (#30).
### Geändert
- `/chat` streamt keine Tokens mehr: Die Antwort wird gepuffert und erst nach bestandener Prüfung als ein `token`-Event ausgeliefert; ein LLM-Fehler zeigt keinen Teiltext (#29).
- Der Systemprompt ist neu; der Satz "Reicht der Kontext nicht, sag das ehrlich" entfällt zugunsten von "lass alles andere weg" (#29).
- `baue_gate` liefert immer eine Kaskade: Score, Gültigkeit, Score, Fassung, optional Laya und Konflikt-Judge (#30).
- Der Eskalationsdatensatz trägt `stichtag`, je Evidenz `gueltigkeit` samt gesetzten Feldern und `scores.konflikt`; `segment` und Metriken beziehen sich auf die genutzten Treffer (#30).
- `tzdata` ist feste Abhängigkeit (#30).
### Hinweis
- Faithfulness Ø 0.93 gilt für den alten Prompt, kein Neulauf.
- **Offen:** Die Befolgungsquote des Modells ist nicht gemessen; befolgt es das Format selten, verweigert das System fast alles.
- **Offen:** Ohne gepflegte Daten bleibt alles `unbekannt` und der Filter wirkt nie. Ohne Konflikt-Judge erkennt eine Standardinstallation keine Normkonflikte; der Fake-Judge-Test belegt nur die Mechanik. Fragen zur früheren Rechtslage werden aus der heute gültigen Fassung beantwortet.

## 2026-11-01
### Neu
- **Eskalationsdatei begrenzt:** Rotation ab `REGRAG_ESKALATION_MAX_BYTES` (5 MiB) oder halber Frist, Löschung nach `REGRAG_ESKALATION_AUFBEWAHRUNG_TAGE` (90) und über `REGRAG_ESKALATION_MAX_ARCHIVE` (5), Bereinigung auch beim Serverstart; Löschweg in ADR 0010 (#34).
- **Laya-Kalibrierung:** Temperature Scaling auf dem Calibration-Split, ECE auf dem Test-Split; `python -m evaluation.eval_report` erzeugt einen Laya-Teil, wenn `REGRAG_LAYA_URL` gesetzt ist (#32).
- **Segmentschwellen für Laya:** Segment im `.source.json`-Sidecar (`gesetz`, `verordnung`, `richtlinie`, `leitlinie`, `auslegung`), sonst `unbekannt` mit strengstem Fallback (#32).
- **Gate-Manifest:** `--checkpoint-revision` schreibt Schwellen, Temperatur, Checkpoint und `eval_version` nach `evaluation/manifest/`; `REGRAG_GATE_MANIFEST` aktiviert es, `REGRAG_LAYA_MODELL` wählt den Checkpoint (#32).
- **Vergleich:** `python -m evaluation.vergleich` vergleicht zwei Reports per Cluster-Bootstrap über die Gruppen (#32).
- **Eskalationsmetriken:** `GET /metrics` mit Raten pro Reason und Segment sowie Score-Histogrammen; der Eskalationsdatensatz trägt `segment` (#32).
### Geändert
- `/chat` lehnt leere und mehr als 2000 Zeichen lange Fragen mit 422 ab; die UI prüft vorher und zeigt eine Meldung (#34).
- Ein gesetztes, aber fehlendes oder ungültiges Manifest lässt den Start fehlschlagen; unter Manifest führt ein abweichendes oder malformtes `routing` bzw. `routing.model` zu ABSTAIN `JUDGE_UNAVAILABLE`; explizites `null` zählt dabei als malformt; fehlt der Schlüssel `routing.model` ganz, warnt das Gate einmalig "Checkpoint nicht verifizierbar".
- Der Temperatur-Fit rechnet die NLL aus dem skalierten Logit statt aus einer geklemmten Wahrscheinlichkeit; ein sicher falsches Calibration-Sample zieht T nicht mehr an den unteren Rand (#32).
- Manifest und Laya-Report heißen nach einem Hash der Checkpoint-Identität (Modell, Revision); ein anderer Checkpoint auf demselben Eval-Set überschreibt die Baseline nicht mehr, überschrieben wird nur beim Neulauf desselben Checkpoints (#32).
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
