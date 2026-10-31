# Spec: Gate: Eval-Set (>= 200), Splits, Kostenfunktion, Report

**Issue:** #31 (https://github.com/Gereon93/regRAG/issues/31), Teil von #26
**Status:** approved
**Datum:** 2026-10-31
**Branch:** issue-31-eval-set-splits-kostenfunktion-report
**Architekt:** claude-sonnet-5-5
**Worker:** claude-sonnet-5-5

---

## 1. Problem & Fachlicher Intent
- **Problem:** Das Eval-Set hat 14 Fälle im Python-Modul, ohne Segmente, Splits, Version oder Kostenfunktion. Der Schwellwert wird über Accuracy gewählt (`evaluation/calibrate.py`).
- **Nutzen:** Threshold-Wahl über Kosten, pro Segment, auf getrenntem Calibration-Split, Bericht auf Test-Split mit Konfidenzintervall, jeder Wert auf eine Eval-Version rückführbar.
- **Ehrliche Grenze:** Die >= 200 gelabelten Fragen liefert der Maintainer (Issue: "Labeln liegt beim Maintainer"). Dieses Issue liefert Format, Validierung, Splits, Kosten, Report und migriert die 14 Seed-Fälle. Das Akzeptanzkriterium "200 Fälle" ist damit **nicht** erfüllt; der Lauf warnt, solange n < 200.
- **Non-Goals:** keine erfundenen Labels, kein Conformal/Selective Prediction (im Issue "optional"), kein Fine-Tuning (#32), keine Verdrahtung der Schwellen in die Laufzeit-Config, kein Laya-Score als Eingabe (nur Retrieval-Score; Report ist über `top`-Feld erweiterbar).

## 2. Funktionsweise und Entscheidungen
1. `evaluation/evalset/v1.jsonl`: ein Fall pro Zeile: `id`, `frage`, `erwartet` (`answer`|`abstain`), `segment` (Quellentyp), `kategorie` (beantwortbar, unbeantwortbar, grenzfall, veraltet, widerspruechlich), `gruppe` (Splitting-Einheit).
2. `evaluation/evalset.py` (reine Funktionen, keine schweren Imports, CI-testbar): `lade_faelle`, `eval_version`, `teile`, `Kosten`, `schwellen_pro_segment`, `wilson`, `bericht`.
3. **Version:** `sha256:` über kanonisches JSON der nach `id` sortierten Fälle. Inhaltsabhängig, reihenfolgeunabhängig.
4. **Split:** 60/20/20 train/calibration/test per Hash über `gruppe` (Fallback `id`). Deterministisch, stabil beim Hinzufügen neuer Fälle. Ganze Gruppen landen in einem Split; `gruppe` fasst Fragen zur selben Norm/Normfassung zusammen (Annahme zu "kein Leakage zwischen Normfassungen").
5. **Kosten:** `Kosten(falschantwort, eskalation)`, Config `REGRAG_KOSTEN_FALSCHANTWORT` (Default 10) und `REGRAG_KOSTEN_ESKALATION` (Default 1). Falschantwort = erwartet `abstain`, Gate antwortet. Unnötige Eskalation = erwartet `answer`, Gate verweigert.
6. **Schwelle pro Segment:** Kandidaten = Mittelpunkte benachbarter Scores plus "alles beantworten" (< min) und "alles verweigern" (> max). Minimiert Gesamtkosten im Segment; bei Gleichstand die höhere (konservativere) Schwelle. Antwort bei `top >= schwelle` (wie `RetrievalScoreGate`).
7. **Report:** pro Segment und gesamt: `n`, `beantwortet`, `falschantworten`, `eskaliert`; Falschantwortrate = Falschantworten / beantwortet (Selective Risk), Eskalationsrate = eskaliert / n, Coverage = beantwortet / n; je Wert mit 95%-Wilson-Intervall. Enthält `eval_version`, `schwellen`, Kosten, Split.
8. **Runner** `python -m evaluation.eval_report`: holt Top-Scores, wählt Schwellen auf **calibration**, berichtet auf **test**, schreibt JSON-Artefakt nach `evaluation/reports/` (gitignored). Getrennt von CI-Tests (braucht Index).
9. `evaluation/dataset.py` lädt `FAELLE` aus dem JSONL (Rückwärtskompatibilität für `run.py`/`calibrate.py`).

## 3. Invarianten
- Schwellen werden nie auf dem Test-Split gewählt.
- Jeder Report und jede Schwellen-Ausgabe trägt `eval_version`.
- Änderung an Frage, Label, Segment oder Gruppe ändert die Version.
- Ein Segment im Report ohne Schwelle ist ein Fehler, kein stiller Default.

## 4. Randfälle
- Leere Segmente/Splits: Segment ohne Fälle im Calibration-Split erhält keine Schwelle; Runner bricht mit klarer Meldung ab, statt zu raten.
- `wilson(0, 0)` = (0, 1) (keine Information).
- Doppelte `id`, unbekanntes `erwartet`, leeres `segment`/`frage`: `ValueError` beim Laden.
- Fall ohne Retrieval-Treffer: `top = 0.0` (wie `calibrate.py`).
- Sehr kleine Segmente: breites CI macht das sichtbar; keine Sonderlogik.

## 5. Akzeptanzkriterien & Testplan
- [ ] Laden/Validierung, Version, Split (deterministisch, stabil, gruppentreu): `tests/test_evalset.py`
- [ ] Schwellen minimieren Kosten, nicht Accuracy; Gewichtung ist Config: `tests/test_evalset.py`
- [ ] Report je Segment mit Wilson-CI und `eval_version`: `tests/test_evalset.py`
- [ ] Gesamtes Seed-Set (`v1.jsonl`) ist valide und splittbar: `tests/test_evalset.py`
- [ ] Config-Defaults/Validierung der Kosten: `tests/test_config.py`
- [ ] Lokal vor Push: `ruff check .` (0.16.3), `python -m compileall -q ... evaluation`, `python -m pytest -q tests`, auch mit `TZ=UTC`.
- [ ] Schnittstellen: nur stdlib (`hashlib`, `json`, `math`); keine externen APIs.

## 6. Adversarial Grill-Protokoll
- **Ausgeführter Skill:** Selbst-Grill gegen Issue-Text und bestehenden Code (kein Subagent).
- **Runde 1:**
  - *Lücken:* (a) "Falschantwort" ist ohne Generator nur die Gate-Entscheidung, nicht die inhaltliche Korrektheit. (b) Schwelle auf Calibration und Report auf Test könnten bei Segment-Mangel auseinanderlaufen. (c) Hash-Split bei kleinem n (14) kann einen Split leer lassen. (d) Gleichstand der Kosten.
  - *Korrekturen:* (a) in Abschnitt 1/5 als Definition festgehalten; Report-Feld `definition`. (b) Runner bricht bei fehlendem Calibration-Segment ab. (c) `teile` ist für n ≥ ~30 sinnvoll; der Runner warnt bei leerem Split, Unit-Test prüft mit n=100. (d) Tie-Break auf höhere Schwelle.
- **Abschluss-Verdikt:** APPROVED (2026-10-31, claude-sonnet-5-5)
