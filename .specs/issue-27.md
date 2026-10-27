# Spec: Gate: Port, Domänenmodell, Score-Guard-Adapter, Eskalationsdatensatz

**Issue:** #27 (https://github.com/Gereon93/regRAG/issues/27), Teil von #26
**Status:** approved
**Datum:** 2026-10-27
**Branch:** issue-27-gate-port
**Architekt:** claude-sonnet-5-5
**Worker:** claude-sonnet-5-5

---

## 1. Problem & Fachlicher Intent
- **Problem:** Die Verweigerung ist ein loses `if` auf dem Top-Score (`beleglage_zu_schwach`). Weitere Prüfungen (Laya-Judge #28, Grounding #29, Gültigkeit #30) haben keinen Platz, ABSTAIN hat keine Begründung, und es entsteht kein Datensatz für menschliche Prüfung.
- **Nutzen:** Ein Port, hinter dem Judges austauschbar sind; jede Verweigerung ist begründet und exportierbar (spätere Eval-Labels).
- **Non-Goals:** keine Laya-Anbindung, kein Grounding, keine Gültigkeitsmetadaten, keine Metriken, kein Dashboard, keine Änderung des Schwellwerts oder des Abstain-Verhaltens.

## 2. User Story
- **Als** Betreiber **möchte ich**, dass jede Verweigerung Reasons, Scores und Evidenz festhält, **damit** ich sie prüfen und als Label wiederverwenden kann.

### Funktionsweise
1. `gate.py` (nur Standardbibliothek, CI-testbar): `Outcome`, `AbstainReason`, `GateScores(relevance, answerable)`, `GateDecision`, Protocol `Gate.entscheide(frage, nodes) -> GateDecision`.
2. `RetrievalScoreGate(min_score)`: bester Score < Schwelle oder keine Treffer -> ABSTAIN/`LOW_RELEVANCE`.
3. `eskalationsdatensatz` / `schreibe_eskalation`: JSONL-Zeile mit UTC-Zeitstempel, Frage, Top-k-Evidenz, Scores, Reasons.
4. `agent.py` und `web/main.py` nutzen das Gate statt `beleglage_zu_schwach`; bei ABSTAIN wird der Datensatz geschrieben.
5. Abstain-Text: "keine belastbare Grundlage" plus Eskalationshinweis.

## 3. Invarianten
- ABSTAIN hat mindestens eine Reason; ANSWER hat keine (im Konstruktor erzwungen).
- Bei ABSTAIN wird das LLM nicht aufgerufen (unverändert zu ADR 0002).
- Score-Entscheidung identisch zu vorher: ABSTAIN genau bei `score < MIN_RETRIEVAL_SCORE`.
- Zeitstempel sind tz-aware UTC.

## 4. Randfälle
- **Keine Treffer:** ABSTAIN, `relevance=None`.
- **Reihenfolge der Treffer:** zählt der maximale Score, nicht `nodes[0]` (Chroma liefert sortiert, das Gate verlässt sich nicht darauf).
- **Schreibfehler beim Eskalationsdatensatz (Platte/Rechte):** darf die Antwort nicht kippen; Fehler wird geloggt, die Verweigerung wird trotzdem ausgeliefert.
- **Schreibpfad fehlt:** Verzeichnis wird angelegt.
- **Nebenläufigkeit:** ein `write` einer ganzen Zeile im Append-Modus; kein Locking nötig (Einzelprozess, Zeilen < PIPE_BUF-Größenordnung nicht garantiert, deshalb ein Schreibaufruf pro Zeile).
- **Datenschutz:** Fragen und Evidenz liegen im Klartext in einer lokalen Datei, die per `.gitignore` ausgeschlossen ist.
- **Naive Zeit:** wird abgelehnt.

## 5. Akzeptanzkriterien und Testplan
- [ ] Jede Anfrage liefert eine `GateDecision` -> `tests/test_gate.py`
- [ ] ABSTAIN nie ohne Reason -> `tests/test_gate.py`
- [ ] Threshold-Grenzfälle (0.619 / 0.62 / 0.621) -> `tests/test_gate.py`
- [ ] Eskalationsdatensatz-Inhalt, UTC, JSONL-Append -> `tests/test_gate.py`
- [ ] Integration Retrieval -> Gate -> Antwort/Abstain im Web-Strom, inkl. Datensatz -> `tests/test_web_loeschen.py`-Stil, neue `tests/test_web_gate.py`
- [ ] Bestehendes Verhalten 14/14 unverändert: `evaluation/run.py` nutzt dasselbe Gate (manuell mit Index, nicht CI)
- [ ] Vor Push: `ruff check .`, `python -m compileall ...`, `python -m pytest -q tests`, einmal mit `TZ=UTC`

## 6. Adversarial Grill-Protokoll
- **Ausgeführt:** Selbst-Review gegen die Spec (kein Skill-Aufruf `/grill`, kein Fremdmodell).
- **Runde 1, Lücken:** (a) Schreibfehler könnte die Antwort kippen -> als Randfall aufgenommen. (b) Verlass auf Sortierung von `nodes[0]` -> Maximum. (c) Rückwärtskompatibilität von `beleglage_zu_schwach` -> wird ersetzt, Aufrufer (`web/main.py`, `evaluation/run.py`, Test-Stub) werden mitgezogen. (d) Datenschutz der Datei -> gitignore.
- **Verdikt:** approved (2026-10-27, Selbst-Review). Wer ein Fremd-Grill will, hat vor dem Merge Gelegenheit.
