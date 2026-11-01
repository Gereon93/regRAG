# Laya kalibrieren, Segmentschwellen, Manifest, Metriken Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Laya-Judge bekommt kalibrierte Scores, Schwellen pro Segment aus einem versionierten Manifest und Eskalationsmetriken über `/metrics`.

**Architecture:** Reine stdlib-Bausteine (`gate.py`-Segmentfunktionen, `evaluation/kalibrierung.py`, `manifest.py`, `metrics.py`) werden vom `LayaGate`, vom Eval-Report und vom Web-Service gemeinsam genutzt. Report und Gate bestimmen das Segment mit derselben Funktion `segment_von(nodes)`. Das Manifest ist die einzige Quelle für Schwellen zur Laufzeit; ein gesetztes, aber ungültiges Manifest lässt den Start fehlschlagen.

**Tech Stack:** Python, FastAPI, pytest, ruff==0.16.3 (wie CI), nur stdlib für neue Module.

**Spec:** `.specs/issue-32.md` (Nummern in Klammern = Spec-Punkte). Fine-Tuning selbst ist nicht Teil dieses Plans.

## Global Constraints
- Neue Module nur stdlib, keine schweren Imports (CI muss sie testen können): `manifest.py`, `metrics.py`, `evaluation/kalibrierung.py`.
- `SEGMENTE = ("gesetz", "verordnung", "richtlinie", "leitlinie", "auslegung")`, `UNBEKANNT = "unbekannt"` (nie in `SEGMENTE`, nie in `schwellen`).
- `EPS = 1e-6`; Temperatur `0.05 <= T <= 20`; Manifest `schema == 1`; alle Schwellen endlich in `[0, 1]`; `fallback_schwelle >= max(schwellen.values())`.
- Defaults von `evalset.py` behalten das Verhalten aus #31 (bestehende Tests bleiben grün).
- Metriken enthalten weder Fragetext noch Evidenz noch Schwellen noch Manifest-Pfad.
- Zeitstempel UTC, tz-aware. Mehrzeilige Texte (PR-Body) per Datei übergeben.
- Vor jedem Push: `ruff check .` (0.16.3), `python -m pytest -q tests`, einmal mit `TZ=UTC`. Schärfere Lintregeln werden gefixt, nicht ignoriert.

## Review Focus
- Node ohne `segment` im Bestandsindex, Segment `Verordnung ` (Groß/Leerzeichen) → muss `unbekannt` bzw. normalisiert sein, nie lockerer (Task 1).
- Gemischte Evidenz (Treffer aus `gesetz` und `verordnung`) → strengste Schwelle (Task 1, 5).
- Manifest gesetzt, Datei fehlt / `Infinity` / Schwelle > 1 / falsches Modell → Start schlägt fehl, kein Rückfall auf 0.5 (Task 4, 5).
- Judge liefert anderes `routing.model` unter Manifest → ABSTAIN `JUDGE_UNAVAILABLE`, nie ANSWER (Task 5).
- `/metrics` nach `LOW_RELEVANCE`-Abbruch (kein Laya-Score) → `none`-Zähler und Segment vorhanden, kein Fragetext in der Antwort (Task 6).

---

### Task 1: Segmentfunktionen, Config, Indexierung, Eval-Set-Validierung

**Files:**
- Modify: `gate.py` (Konstanten und Funktionen oben), `rag.py:37-45` (`_quelle_metadata`), `config.py`, `evaluation/evalset.py` (`_pruefe`), `env.example`
- Test: `tests/test_gate.py`, `tests/test_config.py`, `tests/test_evalset.py`

**Interfaces:**
- Produces (`gate.py`): `SEGMENTE: tuple[str, ...]`, `UNBEKANNT: str`, `normalisiere_segment(wert) -> str`, `segment_von(nodes) -> str`, `kalibriere(p: float, T: float) -> float` (= sigmoid(logit(clip(p, EPS, 1-EPS)) / T)).
- Produces (`config.py`): `GATE_MANIFEST: str` (`REGRAG_GATE_MANIFEST`, getrimmt, Default `""`), `LAYA_MODELL: str` (`REGRAG_LAYA_MODELL`, Default `"multilingual"`, leer → `ValueError`).

- [ ] **Step 1: Failing tests** in `tests/test_gate.py`: `test_normalisiere_segment` (parametrisiert: `"Verordnung "`→`"verordnung"`, `"gesetz"`, `"foo"`→`"unbekannt"`, `None`, `""`, `5`), `test_segment_von_einheitlich` (alle Nodes `verordnung` → `"verordnung"`), `test_segment_von_gemischt_leer_und_ohne_feld_ist_unbekannt` (gemischt, `[]`, Node ohne `segment`, Node mit `"unbekannt"`), `test_kalibriere_identitaet_bei_t1_und_clip` (`kalibriere(0.3, 1.0) ≈ 0.3`, `kalibriere(0.0, 1.0)` und `kalibriere(1.0, 1.0)` liegen strikt in `(0, 1)`, `kalibriere(0.8, 2.0) < 0.8`). Nodes als `SimpleNamespace(metadata={...}, score=..., text=...)`.
  In `tests/test_config.py`: Defaults und leeres `REGRAG_LAYA_MODELL` → `ValueError` (Muster der bestehenden Laya-Config-Tests). In `tests/test_evalset.py`: `ausserhalb` mit `answer` → `ValueError`, Segment `"foo"` → `ValueError`, Seed-Set bleibt gültig und `eval_version` des Seed-Sets ist unverändert (Wert vor der Änderung in den Test schreiben).
- [ ] **Step 2: Run** `pytest tests/test_gate.py tests/test_config.py tests/test_evalset.py -q` → FAIL (ImportError/fehlende Funktionen).
- [ ] **Step 3: Implement** die Funktionen in `gate.py` (Spec 2.1/1–2, 5; 2.3/15 für `kalibriere`). Unbekannter nicht leerer String → einmaliges WARNING (Dateiname-Parameter optional: `normalisiere_segment(wert, quelle=None)`). `config.py`: Variablen wie in 23a. `rag._quelle_metadata`: `"segment": normalisiere_segment(daten.get("segment"), p.name)`. `evalset._pruefe`: `segment in SEGMENTE` oder (`segment == "ausserhalb"` und `erwartet == "abstain"`), sonst `ValueError` mit Zeilennummer. `env.example`: beide neuen Variablen kommentiert.
- [ ] **Step 4: Run** dieselben Tests → PASS; zusätzlich `pytest -q tests` ohne Regression.
- [ ] **Step 5: Commit** `feat(gate): Segmentvokabular, segment_von und kalibriere (#32)`

### Task 2: Temperature Scaling, ECE, Cluster-Bootstrap

**Files:**
- Create: `evaluation/kalibrierung.py`
- Test: `tests/test_kalibrierung.py`

**Interfaces:**
- Consumes: `gate.kalibriere`.
- Produces: `fitte_temperatur(p: list[float], y: list[int]) -> tuple[float, list[str]]`, `ece(p: list[float], y: list[int], bins: int = 5) -> dict | None` (`{"ece": float, "bins": [{"n", "konfidenz", "trefferquote"}]}`, `None` bei leerer Eingabe), `vergleiche(a: dict, b: dict, seed: int = 0, b_resamples: int = 10_000) -> dict` (`a`/`b` sind Report-Dicts mit `eval_version`, `min_retrieval_score`, `kosten`, `test_faelle=[{id, gruppe, kosten}]`; Ergebnis `{"differenz": float, "ci95": [lo, hi], "signifikant": bool | None, "grund": str | None}`).

- [ ] **Step 1: Failing tests:** `fitte_temperatur` findet `T≈2.0` (±10 %) auf simulierten Daten (Seed, p aus `sigmoid(z)`, y aus `sigmoid(z/2)`); ein Label → `(1.0, [Warnung "Temperatur nicht schätzbar"])`; Randoptimum (perfekt trennbare Daten) → Randwert + Warnung. `ece`: bekannte Bins mit handgerechnetem Wert, `[]` → `None`. `vergleiche`: Kandidat überall billiger → `signifikant is True`; gleich → `False`; Seed-Stabilität (zweimal identisch); zwei Fälle derselben Gruppe werden gemeinsam gezogen (Konstruktion mit einer Gruppe, die alle Kosten trägt, ergibt breiteres CI als Einzelfall-Resampling); < 10 Gruppen → `signifikant is None` mit Grund; abweichende `eval_version` / Test-`id`-Menge → `ValueError`.
- [ ] **Step 2: Run** `pytest tests/test_kalibrierung.py -q` → FAIL.
- [ ] **Step 3: Implement** nach Spec 15, 17, 32–33. T per Goldenem Schnitt über `log T` in `[ln 0.05, ln 20]`, Toleranz `1e-4`; Bootstrap mit `random.Random(seed)`, Statistik = mittlere Kosten pro Testfall Kandidat minus Baseline, Perzentil-Intervall 2.5/97.5.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(eval): Temperature Scaling, ECE und Cluster-Bootstrap (#32)`

### Task 3: Kostenlogik parametrisieren

**Files:**
- Modify: `evaluation/evalset.py` (`_beantwortet`, `_kosten`, `schwellen_pro_segment`, `_kennzahlen`, `bericht`)
- Test: `tests/test_evalset.py`

**Interfaces:**
- Produces: alle genannten Funktionen bekommen `score_feld="top"` und `segment_feld="segment"`; `bericht(zeilen, schwellen, kosten, version, score_feld="top", segment_feld="segment", fallback=None)`: fehlt ein Segment in `schwellen`, gilt `fallback` (wenn `None` → `KeyError` wie bisher). `schwellen_pro_segment` ignoriert Segment `"unbekannt"` (bekommt nie eine Schwelle) und wirft `ValueError` bei Kandidat > 1 oder nicht endlich, wenn `score_feld != "top"`.

- [ ] **Step 1: Failing tests:** Zeile mit `laya_kal=None` zählt als Eskalation und verschiebt die Schwelle nicht; `schwellen_pro_segment(..., score_feld="laya_kal", segment_feld="segment_laufzeit")` liefert keinen Eintrag für `unbekannt`; `bericht(..., fallback=0.9)` bewertet ein Segment ohne Schwelle mit 0.9, ohne `fallback` weiterhin `KeyError`; alle bestehenden Tests unverändert grün.
- [ ] **Step 2: Run** → FAIL (neue Tests).
- [ ] **Step 3: Implement** (Spec 11, 12, 14, 18).
- [ ] **Step 4: Run** `pytest tests/test_evalset.py -q` → PASS.
- [ ] **Step 5: Commit** `refactor(eval): Score- und Segmentfeld der Kostenlogik parametrisieren (#32)`

### Task 4: Manifest laden, validieren, schreiben

**Files:**
- Create: `manifest.py`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: `gate.SEGMENTE`, `gate.UNBEKANNT`.
- Produces: `@dataclass(frozen=True) Manifest(checkpoint_modell: str, checkpoint_revision: str, eval_version: str, min_retrieval_score: float, temperatur: float, schwellen: dict[str, float], fallback_schwelle: float, kosten: dict[str, float], erzeugt: str)` mit `schwelle_fuer(segment: str) -> float`; `lade_manifest(pfad, einstellungen) -> Manifest` (`einstellungen` hat `LAYA_MODELL`, `MIN_RETRIEVAL_SCORE`; jede Verletzung aus Spec 23 → `ValueError` mit Feldname); `schreibe_manifest(manifest, verzeichnis) -> Path` (Dateiname `gate-manifest-<eval_version[7:19]>.json`, `json.dumps(allow_nan=False)`).

- [ ] **Step 1: Failing tests:** gültiges Manifest round-trip (`schreibe` → `lade`); je eine Verletzung pro Regel aus Spec 23 (parametrisiert: `schema=2`, fehlendes Feld, Schlüssel `unbekannt` in `schwellen`, Schwelle 1.2, `Infinity` im JSON-Text, `fallback < max`, `temperatur=0.01`, anderes Modell, anderer `min_retrieval_score`, leere `revision`, `eval_version` ohne `sha256:`) → `ValueError` mit Feldname in der Meldung; fehlende Datei und kaputtes JSON → `ValueError`; `schwelle_fuer("unbekannt")` und unbekanntes Segment liefern `fallback_schwelle`; leeres `schwellen` erlaubt.
- [ ] **Step 2: Run** `pytest tests/test_manifest.py -q` → FAIL.
- [ ] **Step 3: Implement** nach Spec 20, 23. Beim erfolgreichen Laden INFO mit `eval_version`, Checkpoint, Dateiname.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(gate): Manifest mit Schwellen, Temperatur und Checkpoint (#32)`

### Task 5: LayaGate mit Segmentschwellen, Checkpoint-Prüfung, drei Ladezustände

**Files:**
- Modify: `laya.py` (`LayaGate`, `_warne_bei_auffaelligkeiten`, `baue_gate`, `MODELL`-Konstante), `tests/test_laya_gate.py` (Helper `einstellungen()` um `GATE_MANIFEST=""`, `LAYA_MODELL="multilingual"` erweitern, Konstruktoraufrufe anpassen), `tests/test_laya_client.py` bei Bedarf
- Test: `tests/test_laya_gate.py`

**Interfaces:**
- Consumes: `gate.segment_von`, `gate.kalibriere`, `manifest.lade_manifest`, `Manifest.schwelle_fuer`.
- Produces: `LayaGate(client, schwelle_fuer, kalibriere_score=lambda p: p, max_len=4096, lang="de", max_evidenz_zeichen=MAX_JUDGE_EVIDENZ_ZEICHEN, modell="multilingual", modell_streng=False)`; `LayaGate.answerable_roh(frage, nodes) -> float` (State bauen, Request mit `model=self.modell`, `_lies_wahrscheinlichkeit`, Auffälligkeiten; wirft `JudgeFehler`; bei abweichendem `routing.model` und `modell_streng` ebenfalls `JudgeFehler`); `entscheide` nutzt `answerable_roh`, ANSWER genau dann, wenn `kalibriere_score(p) >= schwelle_fuer(segment_von(nodes))`; `GateScores.answerable` bleibt roh. `baue_gate(einstellungen, transport=None)`: Zustände aus Spec 22.

- [ ] **Step 1: Failing tests:** knapp drüber/drunter je bekanntes Segment (`schwelle_fuer={"verordnung": 0.4}`-Funktion); `unbekannt`, gemischte Nodes und Node ohne `segment` nutzen den Fallback; Kalibrierung wird vor dem Vergleich angewendet (`kalibriere_score=lambda p: p/2`); `scores.answerable` ist der Rohwert; `routing.model="english"` ohne Manifest → ANSWER/ABSTAIN wie bisher + WARNING, mit `modell_streng=True` → ABSTAIN `JUDGE_UNAVAILABLE`; `baue_gate`: Manifest leer → altes Verhalten; Manifest gesetzt, `LAYA_URL` leer → `ValueError`; Manifest gesetzt, Datei fehlt → `ValueError` (kein Rückfall); gültiges Manifest-Fixture (`tmp_path`) → Kaskade mit Segmentschwelle und `modell_streng=True`.
- [ ] **Step 2: Run** `pytest tests/test_laya_gate.py tests/test_laya_client.py -q` → FAIL.
- [ ] **Step 3: Implement** (Spec 9, 22, 24, 25). `MODELL` durch `einstellungen.LAYA_MODELL` ersetzen, Request-Modell und Routing-Vergleich nutzen `self.modell`.
- [ ] **Step 4: Run** `pytest -q tests` → PASS (inkl. bestehender Laya-Tests, `test_laya_live.py` bleibt übersprungen).
- [ ] **Step 5: Commit** `feat(gate): Laya-Schwellen pro Segment aus dem Manifest (#32)`

### Task 6: Metriken, Eskalationsdatensatz-Segment, `/metrics`

**Files:**
- Create: `metrics.py`
- Modify: `gate.py` (`eskalationsdatensatz`, `entscheide_und_eskaliere`), `web/main.py` (Endpunkt)
- Test: `tests/test_metrics.py`, `tests/test_gate.py`, `tests/test_web_gate.py`

**Interfaces:**
- Produces: `Metriken` mit `zaehle(entscheidung: GateDecision, segment: str) -> None` (thread-sicher), `schnappschuss() -> dict` (Struktur Spec 28), Objekt `METRIKEN`; `eskalationsdatensatz(frage, nodes, entscheidung, jetzt=None)` enthält `"segment": segment_von(nodes)`; `GET /metrics` gibt `METRIKEN.schnappschuss()` zurück.

- [ ] **Step 1: Failing tests:** Zähler pro Reason (mehrere Reasons einer Entscheidung zählen je einmal), pro Segment inkl. `unbekannt`, `eskalationsrate is None` bei 0; Histogrammgrenzen (`0.1` → Bucket `"0.1-0.2"`, `1.0` → `"0.9-1.0"`, `-0.2` → `unter_0`, `1.2` → `ueber_1`, `None` → `none`) für `answerable` und `relevance`; 8 Threads × 500 Zählungen = exakte Summe; Fehler im Zählen ändert die Entscheidung nicht (Monkeypatch wirft, `entscheide_und_eskaliere` liefert trotzdem `Pruefung`); Eskalationsdatensatz enthält `segment`; Web: nach `/chat`-Aufrufen mit Fake-Retriever/Fake-Laya ein `LOW_RELEVANCE`-Abbruch zählt mit `none` im `answerable`-Histogramm, und die `/metrics`-Antwort enthält weder Fragetext noch Schwellen noch Pfad (Textsuche im serialisierten JSON). Muster aus `tests/test_web_gate.py`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** nach Spec 27–30; `seit` = UTC-Prozessstart. `METRIKEN.zaehle` in `entscheide_und_eskaliere` nach der Entscheidung, in `try/except Exception` mit `log.exception`.
- [ ] **Step 4: Run** `pytest -q tests` → PASS.
- [ ] **Step 5: Commit** `feat(gate): Eskalationsmetriken über /metrics (#32)`

### Task 7: Eval-Report Laya-Teil, Manifest-Ausgabe, Vergleich-CLI

**Files:**
- Modify: `evaluation/eval_report.py`
- Create: `evaluation/vergleich.py` (dünner CLI-Wrapper um `kalibrierung.vergleiche`), `evaluation/manifest/.gitkeep`
- Test: `tests/test_eval_report.py`

**Interfaces:**
- Consumes: Tasks 1–5 (`segment_von`, `LayaGate.answerable_roh`, `fitte_temperatur`, `ece`, parametrisierte `schwellen_pro_segment`/`bericht`, `schreibe_manifest`).
- Produces: `hole_laya_zeilen(faelle, retriever, laya_gate, min_retrieval_score) -> list[dict]` (Felder `id`, `segment`, `gruppe`, `erwartet`, `top`, `segment_laufzeit`, `laya_roh`; Laya nur bei `top >= min_retrieval_score`, `JudgeFehler` → `SystemExit`); `erzeuge_laya_bericht(faelle, retriever, laya_gate, kosten, checkpoint_revision) -> tuple[dict, Manifest | None]` (Report mit `laya`-Teil: `temperatur`, `warnungen`, `schwellen`, `fallback_schwelle`, `ece`, `checkpoint`, `index_fingerprint`, `test_faelle`, `definition`; Manifest nur wenn `checkpoint_revision`); CLI-Option `--checkpoint-revision`.

- [ ] **Step 1: Failing tests** (synthetisches Set n=100, mehrere Gruppen/Segmente, Fake-Retriever, Fake-Laya-Transport): Laya wird bei `top < Guard` nicht aufgerufen; Temperatur/Schwellen nur aus Calibration-Zeilen mit `laya_roh`; ein `ausserhalb`-Fall landet unter `segment_laufzeit` seiner Treffer und macht dessen Schwelle strenger; `unbekannt`-Zeilen gehen in den Temperatur-Fit, aber nicht in die Schwellen; Manifest wird mit `--checkpoint-revision` geschrieben, besteht `lade_manifest` und trägt dieselbe `eval_version` wie der Report; ohne Revision kein Manifest; `JudgeFehler` → `SystemExit`; ohne `LAYA_URL` bleibt der Report byte-gleich zu #31 (Regressionstest gegen die bestehende `erzeuge_bericht`); Warnung bei `answer`-Fall mit abweichendem Segment.
- [ ] **Step 2: Run** `pytest tests/test_eval_report.py -q` → FAIL.
- [ ] **Step 3: Implement** nach Spec 8–14, 17, 21, 31. Report nutzt `LayaGate(..., modell_streng=True)` aus `config`. `index_fingerprint` = SHA-256 über `chroma/fingerprint.json`, fehlt die Datei → `null`.
- [ ] **Step 4: Run** `pytest -q tests` → PASS.
- [ ] **Step 5: Commit** `feat(eval): Laya-Kalibrierung, Manifest-Ausgabe und Vergleich (#32)`

### Task 8: ADR 0013, Verweise, README, Changelog, Abschluss

**Files:**
- Create: `docs/adr/0013-laya-kalibrierung-segmentschwellen-manifest.md`
- Modify: `docs/adr/0011-laya-als-lokaler-answerability-judge.md` (Verweis bei `REGRAG_LAYA_MIN_ANSWERABLE` und Routing-Check), `docs/adr/0012-eval-set-splits-kostenfunktion-report.md` (Satz zu "nicht automatisch in die Laufzeit-Config" durch Verweis auf 0013 ersetzen), `README.md` (Gate-Abschnitt: Segment im Sidecar, `REGRAG_INDEX_NEU_BAUEN=1`, Manifest, `/metrics`), `changelog.md`, `docs/arc42.md`

- [ ] **Step 1:** ADR 0013 schreiben mit den Punkten aus Spec 35 (Segmentvokabular und `segment_von`, Kalibrier-Segment ≡ Laufzeit-Segment, "T ändert keine Entscheidung", Manifest und drei Ladezustände, Checkpoint deklariert statt verifiziert, Metriken prozesslokal, Zielgröße-Einschränkung, Cluster-Bootstrap, Pflicht "Kandidat auf Train derselben `eval_version` trainiert").
- [ ] **Step 2:** Verweise in ADR 0011/0012, README, Changelog, arc42 nachziehen.
- [ ] **Step 3: Gesamtlauf:** `ruff check .` (0.16.3, in der Sandbox mit `uvx ruff@0.16.3`), `python -m pytest -q tests`, `TZ=UTC python -m pytest -q tests`. Erwartung: alles grün.
- [ ] **Step 4:** Rebase auf `origin/main`, Push, PR gegen `main` (Body per Datei, Keyword `Closes #32` nicht setzen: Akzeptanzkriterium "signifikant besser" bleibt offen — stattdessen `Refs #32`), im Body die "Entscheidung (Review)"-Punkte und die Manifest-Annahme aufführen. Issue #32 kommentieren: Segmentschwellen nur für Laya, kein Korpus-Signal, `relevance` nicht umgesetzt, Fine-Tuning wartet auf Labels. Danach `stabilize-pr`.
- [ ] **Step 5: Commit** `docs(gate): ADR 0013 zu Laya-Kalibrierung und Segmentschwellen (#32)`
