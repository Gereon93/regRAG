# 0013 — Laya-Kalibrierung, Segmentschwellen, Gate-Manifest und Eskalationsmetriken

Status: akzeptiert als Mechanik, **Nutzen unbelegt** (Issue [#32](../../../../issues/32), Teil von #26)

## Kontext

Laya läuft zero-shot mit einem ungemessenen Startwert (`REGRAG_LAYA_MIN_ANSWERABLE = 0.5`,
[0011](0011-laya-als-lokaler-answerability-judge.md)). Das Eval-Set mit Splits und Kostenfunktion
liefert [0012](0012-eval-set-splits-kostenfunktion-report.md). Offen waren kalibrierte
Wahrscheinlichkeiten, Schwellen pro Segment im Gate, eine Bindung von Checkpoint, Eval-Version und
Schwellen sowie Metriken zur Eskalationsrate.

Dieses Issue liefert **die Infrastruktur**, nicht das Ergebnis: Das Seed-Set hat 14 Fälle in 7 Gruppen
(train = 12, calibration = 0, test = 2). Es gibt weder Labels (>= 200) noch einen trainierten Checkpoint.
Mit dem Seed-Set bricht der Laya-Report weiterhin ab (leerer Calibration-Split); es entsteht kein Manifest.
Das ist gewollt.

## Entscheidung

### Segmente

- **Festes Vokabular** in `gate.py`: `SEGMENTE = ("gesetz", "verordnung", "richtlinie", "leitlinie", "auslegung")`
  und `UNBEKANNT = "unbekannt"`. `unbekannt` ist kein Element von `SEGMENTE`. Erweitern geht nur per Codeänderung.
- `normalisiere_segment(wert)`: `strip().lower()`; liegt der Wert in `SEGMENTE`, wird er übernommen, sonst
  `unbekannt` (fehlend, `None`, leer, kein String). Ein nicht leerer, unbekannter Wert wird einmalig als WARNING
  mit Dateiname geloggt.
- **Quelle der Segmente:** das optionale Feld `segment` im `.source.json`-Sidecar, nur vom Betreiber gepflegt.
  `rag._quelle_metadata` schreibt es in die Node-Metadaten. `convert.py` schreibt es nicht, Uploads
  landen daher immer in `unbekannt`.
- **Kein Einfluss auf Embeddings:** `segment` ist ausdrücklich aus `excluded_embed_metadata_keys` und
  `excluded_llm_metadata_keys` ausgenommen (`rag.indexiere`). Sonst stünde "segment: verordnung" im Embedding-Text,
  und ein Neuaufbau würde die Cosine-Scores verschieben, auf denen `REGRAG_MIN_SCORE` und die Kalibrierung gemessen wurden.
- **Bestandsindex:** Nodes ohne `segment` gelten als `unbekannt` (streng). Wird ein Segment im Sidecar ergänzt
  oder geändert, ändert sich der Datei-Hash des Dokuments (`dokumente.py` hasht den Sidecar mit) und genau dieses
  Dokument wird beim nächsten Start inkrementell neu indexiert. `REGRAG_INDEX_NEU_BAUEN=1 python agent.py`
  (`rag.NEU_BAUEN`, siehe README) braucht man nur, um Segmente auf **alle** bestehenden Dokumente auf einmal
  anzuwenden bzw. einen Bestandsindex ohne Segmente komplett umzustellen.
- **Segment einer Anfrage:** `segment_von(nodes)` (reine Funktion in `gate.py`) liefert das gemeinsame Segment,
  wenn **alle** Nodes dasselbe Segment aus `SEGMENTE` tragen; bei leerer Liste, gemischten Segmenten oder einem
  `unbekannt`-Node liefert sie `unbekannt`. Laya urteilt über die Evidenz aller Nodes, nicht nur den Top-Treffer;
  gemischte Evidenz bekommt deshalb die strengste Schwelle.
- **Kalibrier-Segment ≡ Laufzeit-Segment.** Gate, Metriken und Report verwenden dieselbe Funktion
  `segment_von`. Im Report heißt der Schlüssel `segment_laufzeit` (Segment der beim Lauf abgerufenen Nodes),
  nicht das Eval-Feld `segment`. Ein `ausserhalb`-Fall landet so in dem Segment, das sein Top-k zur Laufzeit hätte.
  Im Eval-Set ist `ausserhalb` nur zusammen mit `erwartet = "abstain"` erlaubt (`lade_faelle` prüft das).
- **`unbekannt` bekommt nie eine eigene Schwelle.** Es, gemischte und fehlende Segmente fallen auf
  `fallback_schwelle = max(schwellen)` zurück, also nie lockerer als irgendein Segment.

### Temperature Scaling

- `fitte_temperatur` (`evaluation/kalibrierung.py`, nur stdlib) minimiert die mittlere NLL per Goldenem Schnitt
  über `log T` in `[0.05, 20]`. Nur eine Klasse im Label: `T = 1.0` mit Warnung "Temperatur nicht
  schätzbar". Optimum am Rand: Randwert mit Warnung.
- `skaliere(p, T)` = `sigmoid(logit(clip(p, EPS, 1-EPS)) / T)`, `EPS = 1e-6`. Der **Fit** benutzt die ungeklemmte
  `skaliere`. `kalibriere` (Gate und Report) klemmt die **Ausgabe** zusätzlich auf `[EPS, 1-EPS]`, weil kleines T
  sonst auf exakt 0.0 bzw. 1.0 sättigt und Schwellenkandidaten außerhalb von `[0, 1]` oder nicht unterscheidbare
  Scores entstehen. Alle Schwellenkandidaten sind deshalb endliche Werte in `[0, 1]`.
- **Ehrliche Wirkung: T ändert (fast) keine Gate-Entscheidung.** Temperature Scaling ist streng monoton, und die Schwelle
  wird danach auf denselben Calibration-Daten gewählt; dieselbe Reihenfolge der Scores ergibt dieselbe Entscheidung.
  Einschränkung durch die Ausgabeklemmung `[EPS, 1-EPS]`: Gesättigte Scores (logit/T > ca. 13,8, z. B. T = 0,1 und
  p > ca. 0,8) werden ununterscheidbar, die Schwellenwahl kann dort gröber sein als auf der Rohskala. Sonst monoton.
  Ein T nahe `T_MIN` (0,05) verdient deshalb Aufmerksamkeit.
  T dient der Interpretierbarkeit (Score ≈ Wahrscheinlichkeit), der Vergleichbarkeit von Schwellen über Segmente
  und Checkpoints und dem ECE-Bericht (5 gleich breite Bins, auf dem Test-Split, vor und nach Kalibrierung).
- Temperatur und Schwellen werden nur auf Calibration-Zeilen mit Laya-Score gewählt, nie auf Train oder Test.
  Vom Guard verworfene Fälle zählen im Bericht als Eskalation, beeinflussen die Laya-Schwelle aber nicht.

### Gate-Manifest

`python -m evaluation.eval_report --checkpoint-revision REV` schreibt `evaluation/manifest/gate-manifest-<eval_version[7:19]>.json`
(eingecheckt; der Report bleibt gitignored unter `REGRAG_EVAL_BERICHT_DIR`). Format (UTF-8, JSON ohne `Infinity`/`NaN`):

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

`kosten` ist reine Herkunftsangabe und wird zur Laufzeit nicht ausgewertet. Der Betreiber kopiert die Datei ins
Deployment und setzt `REGRAG_GATE_MANIFEST`. Gültigkeitsregeln (`manifest.lade_manifest`, jede Verletzung
ist ein `ValueError` mit Feldname): `schema == 1`; alle Felder vorhanden und vom richtigen Typ; Schwellen-Schlüssel
⊆ `SEGMENTE` (nie `unbekannt`); Schwellen und `fallback_schwelle` endlich in `[0, 1]`; `fallback_schwelle >= max(schwellen)`;
`0.05 <= temperatur <= 20`; `checkpoint.modell == REGRAG_LAYA_MODELL`; `min_retrieval_score == REGRAG_MIN_SCORE`
(die Laya-Schwellen gelten nur unter diesem Guard); `checkpoint.revision` nicht leer; `eval_version` beginnt mit `sha256:`.

**Drei Ladezustände** (`laya.baue_gate`, beim Start):

| `REGRAG_GATE_MANIFEST` | `REGRAG_LAYA_URL` | Ergebnis |
|---|---|---|
| nicht gesetzt oder leer | beliebig | bisheriges Verhalten: `REGRAG_LAYA_MIN_ANSWERABLE` global auf dem Rohscore, keine Kalibrierung |
| gesetzt | leer | `ValueError`, Start schlägt fehl |
| gesetzt, Datei fehlt, unlesbar oder ungültig | gesetzt | `ValueError`, Start schlägt fehl |
| gesetzt, gültiges Manifest | gesetzt | Kaskade aus Score-Guard und Laya mit kalibrierten Segmentschwellen, Temperatur und strenger Modellprüfung (`modell_streng`) |

Es gibt **keinen** Rückfall auf 0.5: Ein gesetztes, aber kaputtes Manifest lockert das Gate nie.

Ohne `--checkpoint-revision` oder wenn die Temperatur nicht schätzbar ist (nur ein Label in der Calibration),
schreibt der Report **kein** Manifest (Hinweis bzw. Warnung auf stderr/im Report).

**Checkpoint: deklariert, nicht verifiziert.** `routing.model` der Jev-Antwort ist ein Modellname, keine Revision.
Die `revision` im Manifest ist die Angabe des Betreibers; nichts im Betrieb prüft, dass der Server wirklich
diese Revision lädt. Geprüft wird nur der Name:

- weicht `routing.model` ab: ohne Manifest nur WARNING (wie 0011); **mit Manifest** WARNING und ABSTAIN mit
  `JUDGE_UNAVAILABLE`, weil die kalibrierten Schwellen für diesen Checkpoint nicht gelten;
- ist `routing` vorhanden, aber kein Objekt, oder `routing.model` vorhanden, aber kein String, zählt das unter
  Manifest als **Mismatch** (ABSTAIN `JUDGE_UNAVAILABLE`), nicht als "kein routing";
- **fehlt `routing` bzw. `routing.model` in der Antwort ganz, wird sie auch unter Manifest akzeptiert.** Dann wird
  einmal pro Gate-Instanz (nicht pro Anfrage) gewarnt: "Checkpoint nicht verifizierbar; Schwellen gelten nur
  deklariert". Das ist eine bekannte Grenze: Der Jev-Strict-Modus (`LAYA_JEV_STRICT=1`) sendet kein `routing`; dort ist der Checkpoint vollständig
  unverifiziert. Wer sich auf das Manifest verlässt, darf den Server nicht im Strict-Modus betreiben, oder muss
  den Checkpoint operativ absichern.

### Eskalationsmetriken (`GET /metrics`)

- `metrics.py` (nur stdlib), ein Objekt `METRIKEN` mit Lock. Gezählt wird in `gate.entscheide_und_eskaliere`
  (Engstelle für Web und CLI) nach der Entscheidung; ein Zählfehler wird geloggt und ändert die Entscheidung nicht.
- Inhalt: Entscheidungen und Eskalationen gesamt, Rate (`null` bei 0), Eskalationen pro Reason, pro Segment
  (inkl. `unbekannt`), Histogramme der Rohwerte `answerable` und `relevance` (10 Buckets plus `unter_0`, `ueber_1`,
  `none`), `seit`.
- **Grenzen:** prozesslokal, beim Neustart leer, bei mehreren Workern pro Worker getrennt (heute ein
  uvicorn-Worker). `/metrics` ist wie der Rest der App nicht authentifiziert; die Antwort enthält deshalb nur
  Zähler: keine Schwellen, keine Pfade, keinen Fragetext, keine Evidenz.
- Der Eskalationsdatensatz trägt zusätzlich das Feld `segment`, damit neue Labels segmentiert anfallen.

### Zielgröße und ihre Einschränkung

Im Report ist `y = 1` genau dann, wenn `erwartet == "answer"`: "der Korpus beantwortet die Frage". Laya beurteilt
aber "die **gelieferte Evidenz** enthält die Antwort". Verfehlt das Retrieval den richtigen Chunk, ist das Label
für Laya falsch, und Temperatur wie Schwelle lernen Retrieval-Fehler mit. Das steht im Report-Feld `definition`.
Weil die Evidenz vom Index-Stand abhängt, speichert der Report `index_fingerprint` (SHA-256 über
`chroma/fingerprint.json`).

### Vergleich Baseline gegen Fine-Tuned

`python -m evaluation.vergleich <baseline.json> <kandidat.json> [--seed N]` vergleicht zwei Laya-Reports.
Der Report des aktuellen (zero-shot) Checkpoints ist die Baseline. Der Vergleich bricht ab, wenn `eval_version`,
`min_retrieval_score`, `kosten` oder die Menge der Test-`id`s abweichen.

- **Cluster-Bootstrap**, gepaart, Resampling über `gruppe` (nicht über Einzelfälle, sonst Leakage innerhalb einer
  Normfassung), Statistik = mittlere Kosten pro Testfall (Kandidat minus Baseline), 95%-Perzentil-Intervall,
  fester Seed. "Signifikant besser" heißt: Obergrenze des Intervalls < 0. Unter 10 Gruppen im Test-Split gibt es
  keine Aussage (`signifikant: null` mit Grund).
- **Pflicht (nicht maschinell prüfbar):** Der Kandidat-Checkpoint muss auf dem **Train-Split derselben
  `eval_version`** trainiert sein. Wurde er auf calibration oder test gesehen, ist der Vergleich wertlos.

## Konsequenzen und Grenzen

- **Akzeptanzkriterium "fine-getuntes Laya schlägt Zero-Shot signifikant": offen.** Die Mechanik (Report,
  Manifest, Vergleich) ist gebaut und mit synthetischen Scores getestet. Gemessen ist nichts: Es braucht
  gelabelte Fälle (>= 200, besser mehr), GPU und einen trainierten Checkpoint.
- Schwellen pro Segment gibt es **nur für den Laya-Judge**. Die Retrieval-Score-Schwelle bleibt global
  (`REGRAG_MIN_SCORE`); ohne Laya gibt es keine Segmentschwellen.
- **Prompt-Injection-Grenze aus 0011 unverändert.** Chunk-Text aus Uploads kann Laya Richtung "ja" schieben.
  Kalibrierung macht den Score nicht vertrauenswürdiger, nur besser interpretierbar. Uploads landen in `unbekannt`
  und damit beim strengsten Fallback; das mildert, schützt nicht.
- Die Schwellen werden weiterhin nicht automatisch in die Laufzeit-Config geschrieben; der Weg ist das Manifest,
  das der Betreiber bewusst ins Deployment legt und per `REGRAG_GATE_MANIFEST` aktiviert.
- Ein Judge-Ausfall oder ein abweichender Checkpoint unter Manifest führt zu ABSTAIN, nie zu ANSWER. Die Entscheidung
  ist NaN-sicher formuliert (`not (kalibriert >= schwelle)` führt zu ABSTAIN).

### Bekannte Grenzen

- Ein Segment, dessen Calibration-Fälle nur eine Klasse enthalten (einseitige Kalibrierung), bekommt eine lockere
  eigene Schwelle plus Warnung. Folgearbeit: solche Segmente ausschließen oder das Manifest verweigern.
- Das Manifest ist nicht an den Index-Fingerprint gebunden; nur der Report trägt `index_fingerprint`.
- Der Report bricht ab, wenn ein TEST-Segment keine Calibration-Fälle hat (z. B. das Eval-Segment `ausserhalb`),
  obwohl der Laya-Pfad dieses Segment gar nicht braucht.
- Gleiche `eval_version` mit anderer Revision überschreibt das eingecheckte Manifest (`git diff` macht es sichtbar).

## Bewusst nicht umgesetzt

- **Fine-Tuning selbst** und ein Trainingsexport (`export_train`): Das Laya-Trainingsformat (u. a. `labels` für
  `noul`) ist nicht verifiziert; ohne Labels und GPU gäbe es nichts zu trainieren.
- **`relevance`-Judge:** Relevanz bleibt beim Retrieval-Score (0011); `relevance` erscheint nur als Histogramm.
- **Raten-Warnung / "Korpus veraltet"-Signal:** Die Eval-Set-Rate ist mit echtem Traffic nicht vergleichbar, es gibt
  keine belastbare Referenzrate. `/metrics` liefert die Rohdaten, die Bewertung bleibt beim Betreiber.
- **Segmentschwellen für den Retrieval-Score:** bleibt global `REGRAG_MIN_SCORE`.
- Kein Dashboard, keine externe Metrik-Infrastruktur.
