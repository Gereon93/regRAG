# Spec: Gate: Laya fine-tunen, kalibrieren, Thresholds und Metriken

**Issue:** #32 (https://github.com/Gereon93/regRAG/issues/32), Teil von #26
**Status:** Review eingearbeitet
**Datum:** 2026-10-31
**Branch:** Gereon93/gate-laya-fine-tunen-kalibrieren-thresholds-und
**Abhängigkeiten:** #28 (Laya-Adapter, gemergt), #31 (Eval-Set/Splits/Kosten, gemergt bis auf Labels)

Mit "Entscheidung (Review)" markierte Punkte stammen aus dem Spec-Review vom 2026-10-31 und wurden ohne
Rückfrage entschieden; sie sind reversibel und im PR-Body aufzuführen.

---

## 1. Problem & Intent
- **Problem:** Laya läuft zero-shot mit einem ungemessenen Startwert (`REGRAG_LAYA_MIN_ANSWERABLE = 0.5`, ADR 0011). Es gibt keine kalibrierten Wahrscheinlichkeiten, keine Thresholds pro Segment im Gate und keine Metriken zur Eskalationsrate.
- **Nutzen:** Der Laya-Score wird kalibriert, die Laya-Schwelle pro Segment per Kostenfunktion gewählt, Checkpoint + Eval-Version + Schwellen stehen in einem Manifest, und Eskalationen sind über `/metrics` beobachtbar.
- **Ehrliche Grenze:** Das Seed-Set hat 14 Fälle in 7 Gruppen (`dora-2022-2554` mit 8 Fällen, 6 Einzelgruppen `offtopic-*`). Der Split ergibt train = 12, calibration = 0, test = 2 (beide `abstain`). Fine-Tuning und das Kriterium "Fine-Tuned schlägt Zero-Shot signifikant" sind **nicht** erfüllbar, bis Labels (≥ 200, besser mehr), GPU und ein trainierter Checkpoint existieren. Dieses Issue liefert die Infrastruktur und testet sie mit synthetischen Scores. Mit dem Seed-Set bricht der Report weiterhin ab (leerer Calibration-Split), es entsteht also kein Manifest; das ist gewollt.
- **Non-Goals:**
  - kein Training in diesem Repo, keine erfundenen Labels, kein Conformal/Selective Prediction;
  - kein `relevance`-Judge und kein Fine-Tuning auf `relevance` (Relevanz bleibt beim Retrieval-Score, ADR 0011; Abweichung vom Issue-Scope, siehe Abschnitt 6);
  - **Entscheidung (Review):** kein Trainingsexport (`export_train`), solange das Laya-Trainingsformat nicht verifiziert ist;
  - **Entscheidung (Review):** keine Segmentschwellen für den Retrieval-Score; die bleibt global `REGRAG_MIN_SCORE`;
  - **Entscheidung (Review):** keine Raten-Warnung / kein "Korpus veraltet"-Signal mit Referenzrate (die Eval-Set-Rate ist mit echtem Traffic nicht vergleichbar); `/metrics` liefert die Rohdaten, die Bewertung bleibt beim Betreiber;
  - kein Dashboard, keine externe Metrik-Infrastruktur (Prometheus-Server o. Ä.).

## 2. Entscheidungen

### 2.1 Segmente
1. **Entscheidung (Review): Festes Vokabular.** `gate.py` definiert `SEGMENTE = ("gesetz", "verordnung", "richtlinie", "leitlinie", "auslegung")` und `UNBEKANNT = "unbekannt"`. `unbekannt` ist kein Element von `SEGMENTE`. Erweiterung nur per Codeänderung.
2. **Normalisierung:** `normalisiere_segment(wert) -> str` (in `gate.py`): `str`, `strip().lower()`; liegt das Ergebnis in `SEGMENTE`, wird es zurückgegeben, sonst `UNBEKANNT`. Fehlend, `None`, leer, kein `str` → `UNBEKANNT`. Ein nicht leerer, unbekannter Wert wird einmalig als WARNING mit Dateiname geloggt.
3. **Indexierung:** `_quelle_metadata` (`rag.py`) setzt `"segment": normalisiere_segment(daten.get("segment"))`. Der Sidecar `.source.json` bekommt das optionale Feld `segment`; es wird nur vom Betreiber gepflegt. `convert.py` schreibt es **nicht**, Uploads über `/upload` landen also immer in `unbekannt`.
4. **Bestandsindex:** Der Fingerprint ändert sich durch die neue Metadaten-Spalte nicht. Nodes aus einem Bestandsindex haben kein `segment` und werden wie `unbekannt` behandelt (Fallback, also streng). Wer Segmente nutzen will, baut einmal mit `REGRAG_INDEX_NEU_BAUEN=1` neu; das steht in ADR 0013 und im README-Abschnitt zum Gate.
5. **Entscheidung (Review): Segment einer Anfrage.** `segment_von(nodes) -> str` (in `gate.py`, reine Funktion) wird von Gate, Metriken **und** Report verwendet:
   - liefert das gemeinsame Segment, wenn **alle** übergebenen Nodes dasselbe Segment aus `SEGMENTE` tragen (gelesen über `normalisiere_segment(n.metadata.get("segment"))`);
   - liefert `UNBEKANNT` bei leerer Liste, gemischten Segmenten oder wenn ein Node `unbekannt` ist.
   Begründung: Laya urteilt über die Evidenz aller Nodes (`laya.py:78`), nicht nur über den Top-Treffer; gemischte Evidenz bekommt deshalb die strengste Schwelle (Fallback).
6. **Eval-Set:** `segment` eines Falls ist das Segment des erwarteten Quelldokuments, aus `SEGMENTE`. Zusätzlich ist `ausserhalb` erlaubt, nur zusammen mit `erwartet = "abstain"` (Frage hat kein Quelldokument im Korpus). `lade_faelle` prüft das und wirft sonst `ValueError`. Die heutigen Werte (`verordnung`, `ausserhalb`) sind gültig; `eval_version` ändert sich dadurch nicht.
7. **Entscheidung (Review): Kalibrier-Segment ≡ Laufzeit-Segment.** Für Schwellenwahl und Laya-Bericht ist der Schlüssel `segment_laufzeit = segment_von(nodes)` der beim Report-Lauf abgerufenen Nodes, nicht das Eval-Feld `segment`. Damit landen `ausserhalb`-Fälle in dem Segment, das ihr Top-k zur Laufzeit hätte, und machen genau dessen Schwelle strenger (fail-safe). Das Eval-Feld `segment` bleibt Report-Dimension. Weicht bei einem `answer`-Fall `segment_laufzeit` vom Eval-`segment` ab, steht eine Warnung im Report ("Retrieval traf anderes Segment").

### 2.2 Report (Laya-Teil)
8. **Wann:** `python -m evaluation.eval_report` erzeugt den Laya-Teil nur, wenn `REGRAG_LAYA_URL` gesetzt ist. Ohne Laya bleibt der Report exakt wie in #31.
9. **Zeilen:** Pro Fall werden die Nodes wie bisher abgerufen (`top_k=3`) und zusätzlich erfasst: `segment_laufzeit`, `laya_roh` (Rohwahrscheinlichkeit `answerable`). Laya wird **nur** aufgerufen, wenn `top >= config.MIN_RETRIEVAL_SCORE` (Kaskade wie zur Laufzeit), sonst `laya_roh = None`. Der Laya-Aufruf läuft über die neue Methode `LayaGate.answerable_roh(frage, nodes) -> float` (State bauen, Request, `_lies_wahrscheinlichkeit`, Auffälligkeiten prüfen; wirft `JudgeFehler`), die auch `LayaGate.entscheide` benutzt (keine Duplikation). Der Report baut dafür ein `LayaGate` ohne Manifest aus `config`. Ein abweichendes `routing.model` bricht den Report ab.
10. **Judge-Ausfall im Report:** Ein `JudgeFehler` bei einem Fall bricht den Report mit Meldung ab (`SystemExit`). Eine Messung mit Lücken wird nicht geschrieben.
11. **Kaskade in der Kostenlogik:** Die Report-Zeile bekommt das Feld `laya_kal = kalibriere(laya_roh, T)` bzw. `None`, wenn `laya_roh is None`. Weil `_beantwortet` `None` schon als Verweigerung zählt, bildet die Kostenlogik damit exakt "Guard bestanden **und** Laya ≥ Schwelle" ab. `_beantwortet`, `_kosten`, `schwellen_pro_segment`, `_kennzahlen` und `bericht` (`evaluation/evalset.py`) bekommen die Parameter `score_feld="top"` und `segment_feld="segment"`; die Defaults erhalten das Verhalten aus #31.
12. **Population:** Temperatur und Schwellen werden nur auf Calibration-Zeilen mit `laya_roh is not None` gefittet. Vom Guard verworfene Fälle zählen im Bericht als Eskalation, beeinflussen die Laya-Schwelle aber nicht. Calibration-Zeilen mit `segment_laufzeit == "unbekannt"` gehen in den Temperatur-Fit ein, aber **nicht** in die Schwellenwahl: `unbekannt` bekommt nie eine eigene Schwelle (siehe 18, 19).
13. **Zielgröße:** `y = 1` genau dann, wenn `erwartet == "answer"`. **Bekannte Einschränkung (Review):** Laya beurteilt "die gelieferte Evidenz enthält die Antwort", `y` sagt "der Korpus beantwortet die Frage". Verfehlt das Retrieval den richtigen Chunk, ist das Label für Laya falsch. Das steht im Report-Feld `definition` und in ADR 0013. Der Report speichert `index_fingerprint` (SHA-256 über `chroma/fingerprint.json`), weil die Evidenz vom Index-Stand abhängt.
14. **Abbruchregeln** (zusätzlich zu #31): kein Calibration-Segment aus `SEGMENTE` mit Laya-Score → Abbruch; Segment im Test-Split ohne Schwelle (inkl. `unbekannt`) → wird mit `fallback_schwelle` bewertet, nicht abgebrochen (gleiches Verhalten wie das Gate). Dafür bekommt `bericht` den Parameter `fallback=None`: `None` behält das #31-Verhalten (`KeyError` bei fehlender Schwelle), ein Wert wird für fehlende Segmente verwendet. Nur der Laya-Teil übergibt `fallback_schwelle`.

### 2.3 Temperature Scaling
15. **Funktion:** `fitte_temperatur(p, y) -> (T, warnungen)` in `evaluation/kalibrierung.py` (nur stdlib). `kalibriere(p, T) = sigmoid(logit(clip(p, EPS, 1-EPS)) / T)` mit `EPS = 1e-6`; `kalibriere` liegt in `gate.py`, damit Gate und Report dieselbe Formel nutzen. `T` minimiert die mittlere NLL per Goldenem Schnitt über `log T` im Intervall `[0.05, 20]`, Toleranz `1e-4` in `log T`.
   - Nur ein Label oder weniger als 2 Zeilen → `T = 1.0` und Warnung "Temperatur nicht schätzbar".
   - Optimum am Intervallrand (Abstand < Toleranz) → `T` = Randwert und Warnung.
16. **Ehrliche Wirkung (Review):** Temperature Scaling ist streng monoton. Da die Schwelle danach auf denselben Calibration-Daten gewählt wird, **ändert T keine einzige Gate-Entscheidung**. T dient nur der Interpretierbarkeit (Score ≈ Wahrscheinlichkeit), der Vergleichbarkeit von Schwellen über Segmente und Checkpoints und dem ECE-Bericht. Das steht so in ADR 0013.
17. **ECE:** auf dem **Test-Split** (Zeilen mit `laya_roh is not None`), vor und nach Kalibrierung, 5 gleich breite Bins auf `[0, 1]`, pro Bin `n`, mittlere Konfidenz und Trefferquote im Report. Keine solche Zeile → `ece: null`.

### 2.4 Schwellen und Manifest
18. **Schwelle pro Segment:** `schwellen_pro_segment(zeilen, kosten, score_feld="laya_kal", segment_feld="segment_laufzeit")` auf den Calibration-Zeilen aus 12. Kandidaten wie #31. Da `laya_kal` wegen des Clips strikt in `(0, 1)` liegt, sind alle Kandidaten endliche Werte in `[0, 1]`; ein Kandidat > 1 oder nicht endlich ist ein Programmierfehler (`ValueError`).
19. **Fallback:** `fallback_schwelle = max(schwellen.values())`. Gilt für `unbekannt` und jedes Segment ohne Eintrag.
20. **Entscheidung (Review): Manifest-Format** (`gate-manifest-<eval_version[7:19]>.json`, UTF-8, gültiges JSON ohne `Infinity`/`NaN`):
    ```json
    {
      "schema": 1,
      "checkpoint": {"modell": "multilingual", "revision": "<vom Betreiber angegeben>"},
      "eval_version": "sha256:…",
      "min_retrieval_score": 0.62,
      "temperatur": 1.0,
      "schwellen": {"verordnung": 0.41},
      "fallback_schwelle": 0.41,
      "kosten": {"falschantwort": 10.0, "eskalation": 1.0},
      "erzeugt": "2026-10-31T12:00:00+00:00"
    }
    ```
    `kosten` ist nur Herkunftsangabe und wird zur Laufzeit nicht ausgewertet; `REGRAG_KOSTEN_*` bleiben die Quelle für den Report.
21. **Wer schreibt es:** `python -m evaluation.eval_report --checkpoint-revision REV` schreibt das Manifest nach `evaluation/manifest/` (eingecheckt, siehe 6) und den Report nach `REGRAG_EVAL_BERICHT_DIR` (gitignored). Ohne `--checkpoint-revision` wird kein Manifest geschrieben (Hinweis auf stderr). `checkpoint.modell` = `config.LAYA_MODELL` (siehe 25), `min_retrieval_score` = `config.MIN_RETRIEVAL_SCORE`. Der Betreiber kopiert die Datei ins Deployment (z. B. `chroma/`) und setzt `REGRAG_GATE_MANIFEST`. Es gibt kein händisches Nachbearbeiten.
22. **Entscheidung (Review): Laden — drei Zustände**, geprüft in `laya.baue_gate` beim Start:
    - `REGRAG_GATE_MANIFEST` leer/nicht gesetzt → bisheriges Verhalten (`REGRAG_LAYA_MIN_ANSWERABLE` global auf Rohscore, keine Kalibrierung).
    - gesetzt, aber `REGRAG_LAYA_URL` leer → `ValueError`, Start schlägt fehl.
    - gesetzt und Datei fehlt, unlesbar oder ungültig (siehe 23) → `ValueError`, Start schlägt fehl. Es gibt **keinen** Rückfall auf 0.5.
23. **Validierung** (jede Verletzung → `ValueError` mit Feldname): `schema == 1`; alle Felder aus 20 vorhanden und vom richtigen Typ; `schwellen` ist ein Objekt, Schlüssel ⊆ `SEGMENTE` (also nie `unbekannt`); alle Schwellen und `fallback_schwelle` endlich in `[0, 1]`; `fallback_schwelle >= max(schwellen.values())` (leeres `schwellen` erlaubt); `0.05 <= temperatur <= 20`; `checkpoint.modell == config.LAYA_MODELL`; `min_retrieval_score == config.MIN_RETRIEVAL_SCORE` (die Laya-Schwellen gelten nur unter diesem Guard); `checkpoint.revision` nicht leerer String; `eval_version` beginnt mit `sha256:`. Beim erfolgreichen Laden wird INFO geloggt: `eval_version`, `checkpoint`, Datei.

23a. **Config** (`config.py`, Validierung beim Import wie bisher): `GATE_MANIFEST = os.getenv("REGRAG_GATE_MANIFEST", "").strip()`, `LAYA_MODELL = os.getenv("REGRAG_LAYA_MODELL", "multilingual").strip()`, leerer `LAYA_MODELL` → `ValueError`. Tests in `tests/test_config.py`.

### 2.5 Gate
24. **`LayaGate`** bekommt statt `min_answerable` zwei Funktionen: `schwelle_fuer(segment) -> float` und `kalibriere_score(p) -> float`.
    - Mit Manifest: `schwelle_fuer(s) = schwellen[s] if s in schwellen else fallback_schwelle` (`unbekannt` ist nie in `schwellen`), `kalibriere_score = lambda p: kalibriere(p, T)`.
    - Ohne Manifest: `schwelle_fuer = lambda s: LAYA_MIN_ANSWERABLE`, `kalibriere_score` = Identität.
    - Entscheidung: `segment = segment_von(nodes)`; ANSWER genau dann, wenn `kalibriere_score(p) >= schwelle_fuer(segment)`. `GateScores.answerable` bleibt der **Rohwert** `p` (für spätere Neukalibrierung und Histogramm).
25. **Entscheidung (Review): Checkpoint-Auswahl und -Prüfung.** Neue Config `REGRAG_LAYA_MODELL` (Default `multilingual`) ersetzt die Konstante `MODELL` in Request und Routing-Vergleich. `routing.model` ist nur ein Modellname, keine Revision; die Revision im Manifest ist **deklariert, nicht verifiziert**. Abweichendes `routing.model`:
    - ohne Manifest: WARNING wie bisher (ADR 0011);
    - mit Manifest: WARNING und ABSTAIN mit `JUDGE_UNAVAILABLE` (die kalibrierten Schwellen gelten für diesen Checkpoint nicht).
26. **Prompt-Injection (Grenze aus ADR 0011 bleibt):** Chunk-Text aus Uploads kann Laya Richtung "ja" schieben. Kalibrierung macht den Score nicht vertrauenswürdiger, nur besser interpretierbar. Uploads landen in `unbekannt` und damit beim strengsten Fallback; das mildert, schützt aber nicht.

### 2.6 Metriken
27. **Ort:** `metrics.py` (nur stdlib), ein Objekt `METRIKEN` mit `threading.Lock`. Gezählt wird in `gate.entscheide_und_eskaliere` (einzige Engstelle für Web und CLI) nach der Entscheidung: `segment = segment_von(nodes)`, dann `METRIKEN.zaehle(entscheidung, segment)`. Damit haben auch `LOW_RELEVANCE`-Abbrüche ein Segment. Ein Fehler beim Zählen wird geloggt und ändert die Entscheidung nicht.
28. **Inhalt** von `GET /metrics` (JSON):
    - `entscheidungen_gesamt`, `eskalationen_gesamt`, `eskalationsrate` (`null` bei 0 Entscheidungen);
    - `eskalationen_nach_reason` (jede Reason einer Entscheidung zählt einmal);
    - `nach_segment`: pro Segment (inkl. `unbekannt`) `entscheidungen`, `eskalationen`, `eskalationsrate`;
    - `histogramme.answerable` und `histogramme.relevance` auf Rohwerten: 10 gleich breite Buckets auf `[0, 1]` (Schlüssel `"0.0-0.1"` … `"0.9-1.0"`, obere Grenze exklusiv außer im letzten), dazu `unter_0` (Cosine-Score kann negativ sein), `ueber_1` und `none` (Score fehlt: Guard-Abbruch, Judge-Ausfall, Laya aus);
    - `seit` (UTC-Start des Prozesses).
29. **Entscheidung (Review): Eskalationsdatensatz** bekommt das Feld `segment` (aus `segment_von(nodes)`), damit Eskalationen als neue Labels segmentiert sind.
30. **Grenzen:** prozesslokal, beim Neustart leer, bei mehreren Workern pro Worker getrennt (heute ein uvicorn-Worker). `/metrics` ist wie der Rest der App nicht authentifiziert; es enthält deshalb nur Zähler, keine Schwellen, keinen Manifest-Pfad, keinen Fragetext, keine Evidenz.

### 2.7 Vergleich Baseline gegen Fine-Tuned
31. **Report-Artefakt:** Der Laya-Teil des Reports enthält `checkpoint` (`modell`, `revision` oder `null`) und `test_faelle: [{id, gruppe, kosten}]` (Kosten-Beitrag je Testfall unter den gewählten Schwellen; keine Fragetexte). Der Report des aktuellen Checkpoints (zero-shot) **ist** die Baseline.
32. **Vergleich:** `python -m evaluation.vergleich <baseline.json> <kandidat.json>` (reine Funktion `vergleiche(a, b, seed=0, b_resamples=10_000)` in `evaluation/kalibrierung.py`). Bricht ab, wenn `eval_version`, `min_retrieval_score`, `kosten` oder die Menge der Test-`id`s abweichen.
33. **Entscheidung (Review): Cluster-Bootstrap.** Gepaart, Resampling über `gruppe` (nicht über Einzelfälle), Statistik = mittlere Kosten pro Testfall Kandidat minus Baseline, 95%-Perzentil-Intervall, `random.Random(seed)`. "Signifikant besser" heißt: Obergrenze < 0. Unter 10 Gruppen im Test-Split gibt es keine Aussage (`signifikant: null`, Grund im Ergebnis).
34. Der Kandidat muss auf dem Train-Split **derselben** `eval_version` trainiert sein. Das ist nicht maschinell prüfbar und steht als Pflicht in ADR 0013.

### 2.8 Dokumentation
35. **ADR 0013** (Lieferumfang): Segment-Vokabular und `segment_von`, Kalibrier-Segment ≡ Laufzeit-Segment, Temperature Scaling samt "ändert keine Entscheidung", Manifest-Format und drei Ladezustände, Checkpoint deklariert statt verifiziert, Metriken prozesslokal, Zielgröße-Einschränkung, Cluster-Bootstrap. Sie **ersetzt** in ADR 0012 den Satz "Die Schwellen werden nicht automatisch in die Laufzeit-Config geschrieben" (dort Verweis auf 0013 ergänzen). ADR 0011 bekommt einen Verweis auf 0013 bei `REGRAG_LAYA_MIN_ANSWERABLE` und beim Routing-Check.

## 3. Invarianten
- Schwellen und Temperatur werden nie auf Test oder Train gewählt.
- Kalibrier-Segment ≡ Laufzeit-Segment: Report und Gate bestimmen das Segment mit derselben Funktion `segment_von(nodes)`.
- Ein gesetztes, aber fehlendes oder ungültiges Manifest lässt den Start fehlschlagen; es lockert das Gate nie.
- `fallback_schwelle >= max(schwellen)`: `unbekannt`, fehlende und gemischte Segmente werden nie lockerer behandelt als irgendein Segment.
- Uploads bekommen nie ein Segment außer `unbekannt`.
- Ein Judge-Ausfall oder ein abweichender Checkpoint unter Manifest führt zu ABSTAIN, nie zu ANSWER.
- Metriken enthalten weder Fragetext noch Evidenz noch Schwellen.
- Manifest und Report eines Laufs tragen dieselbe `eval_version`.

## 4. Tests
- **Unit:**
  - `normalisiere_segment` (Groß/Klein, Leerzeichen, unbekannter Wert, `None`, Nicht-String) und `segment_von` (einheitlich, gemischt, leer, Node ohne `segment`, Node mit `unbekannt`);
  - `lade_faelle`: `ausserhalb` nur mit `abstain`, Segment außerhalb des Vokabulars → `ValueError`; Seed-Set bleibt gültig, `eval_version` unverändert;
  - `kalibriere` (Clip bei p = 0/1, T = 1 ist Identität), `fitte_temperatur` (bekannte Temperatur aus simulierten Daten auf ±10 % wiederfinden, nur ein Label → `T = 1.0` + Warnung, Randoptimum → Warnung);
  - ECE (bekannte Bins, leere Eingabe → `null`);
  - Kostenlogik mit `score_feld="laya_kal"`: Zeile mit `laya_kal=None` zählt als Eskalation und verschiebt die Schwelle nicht; Defaults verhalten sich wie #31 (bestehende Tests bleiben grün);
  - `unbekannt`-Zeilen bekommen keine eigene Schwelle; `fallback_schwelle == max`;
  - Manifest: gültiges Manifest lädt; jede Regel aus 2.4/23 einzeln verletzt → `ValueError`; die drei Ladezustände aus 22;
  - `LayaGate` mit Segment-Schwelle und Fallback: knapp drüber/drunter je bekanntes Segment, `unbekannt`, gemischt, Node ohne `segment`; Kalibrierung wird vor dem Vergleich angewendet; `GateScores.answerable` ist roh;
  - Routing-Mismatch: ohne Manifest nur WARNING, mit Manifest ABSTAIN `JUDGE_UNAVAILABLE`;
  - Bootstrap: Seed-Stabilität, offensichtlich besser/schlechter/gleich, Resampling nach Gruppe (zwei Fälle derselben Gruppe bleiben zusammen), < 10 Gruppen → `null`, abweichende `eval_version` → Abbruch;
  - Metrikzähler: Reasons, Segmente, Histogramm-Buckets inkl. Grenzen, `unter_0`, `ueber_1`, `none`; Thread-Sicherheit mit parallelen Zählern; Eskalationsdatensatz enthält `segment`.
- **Integration:**
  - `/metrics` nach mehreren Gate-Entscheidungen über `/chat` (Fake-Retriever, Fake-Laya-Transport): Zähler stimmen, kein Fragetext in der Antwort;
  - `rag._quelle_metadata` mit Sidecar-`segment` → `segment_von` → Schwellenwahl im `LayaGate`;
  - Kaskade mit Manifest end-to-end gegen Fake-Laya-Transport (`baue_gate` mit `REGRAG_GATE_MANIFEST` auf Fixture-Datei);
  - Report-Laya-Teil mit Fake-Retriever und Fake-Laya auf synthetischem Set (n = 100, mehrere Gruppen und Segmente): Manifest wird geschrieben, ist gültig nach 23, und ein `ausserhalb`-Fall landet unter `segment_laufzeit`.
- **Nicht in CI:** echter Laya-Server, Training (wie bisher `test_laya_live.py`).
- **Lokal vor Push:** `ruff check .` (0.16.3), `python -m pytest -q tests`, auch mit `TZ=UTC`.

## 5. Akzeptanz (Abgleich mit Issue)
- [ ] Fine-getuntes Laya schlägt Zero-Shot signifikant: **offen**. Mechanik gebaut (Report + `evaluation.vergleich`), Messung braucht Labels + Checkpoint.
- [x] Thresholds pro Segment konfigurierbar, nicht hartcodiert: **nur für den Laya-Judge** (Manifest). Die Retrieval-Score-Schwelle bleibt global; ohne Laya gibt es keine Segmentschwellen. Wird im Issue kommentiert.
- [x] Checkpoint + Eval-Version + Threshold gemeinsam versioniert: ein Manifest bindet sie; die Checkpoint-Revision ist deklariert, nicht verifiziert.
- [x] Zero-Shot-Laya als Baseline im Report.
- [x] Kalibrierung auf dem Calibration-Split (Temperature Scaling), danach Schwelle per Kostenfunktion; ECE auf Test.
- [x] Metriken: Eskalationsrate gesamt, pro Reason und pro Segment, Score-Verteilungen (`answerable`, `relevance`).
- [ ] Steigende Rate als Signal für veralteten Korpus: **bewusst nicht umgesetzt** (keine belastbare Referenzrate); `/metrics` liefert die Rohdaten. Wird im Issue kommentiert.
- [ ] Fine-Tuning auf `relevance`: **bewusst nicht umgesetzt** (Relevanz bleibt beim Retrieval-Score, ADR 0011). Wird im Issue kommentiert.
- [ ] Fine-Tuning auf `answerable`: **nicht in diesem Repo**; kein Trainingsexport, bis das Laya-Trainingsformat verifiziert ist.

## 6. Offene Punkte (nur durch den Maintainer entscheidbar)
- Wie viele Labels realistisch? ≥ 200 ist Untergrenze für saubere Splits.
- ~~Segment-Vokabular~~ **Entschieden (Maintainer, 01.11.2026):** `gesetz`, `verordnung`, `richtlinie`, `leitlinie`, `auslegung`, wie in 2.1/1.
- ~~Manifest einchecken~~ **Entscheidung (Plan):** Das Manifest wird eingecheckt unter `evaluation/manifest/` (nicht im gitignorierten Report-Verzeichnis); der Report bleibt gitignored. Der Maintainer hat keinen Einwand erhoben; im PR-Body als Annahme vermerken.
- Laya-Trainingstooling (Format, `labels` für `noul`) ist nicht verifiziert.
