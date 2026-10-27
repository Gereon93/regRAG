# 0010 — Decision Gate als Port, Verweigerung mit Begründung und Eskalationsdatensatz

Status: akzeptiert (Issue [#27](../../../../issues/27), Teil von #26)

## Kontext

Die Verweigerung war ein loses `if` auf dem besten Retrieval-Score (`beleglage_zu_schwach`,
[0002](0002-abstain-statt-raten.md), [0005](0005-guard-kalibriert-abstain-als-bedingte-kante.md)).
Weitere Prüfungen — ein Answerability-Judge, Grounding, Gültigkeit — hatten keinen Platz, und
eine Verweigerung hinterließ weder Begründung noch Spur.

## Entscheidung

- `gate.py` (nur Standardbibliothek) definiert `GateDecision(outcome, reasons, scores)`,
  `AbstainReason` und den Port `Gate.entscheide(frage, nodes)`. ABSTAIN ohne Reason ist
  im Konstruktor unmöglich.
- Der bisherige Score-Guard ist der erste Adapter (`RetrievalScoreGate`, Reason `LOW_RELEVANCE`).
  Entscheidung unverändert: ABSTAIN genau bei `score < MIN_RETRIEVAL_SCORE`; der Gate nimmt das
  Maximum statt `nodes[0]`.
- Jede Verweigerung hängt einen JSONL-Datensatz an `REGRAG_ESKALATION_DATEI` (Default:
  `chroma/eskalationen.jsonl`, im Volume): Frage, Top-k-Evidenz, Scores, Reasons, UTC-Zeitstempel.
  Schreibfehler werden geloggt und kippen die Verweigerung nicht.
- Die Nutzerantwort nennt "keine belastbare Grundlage" und den Hinweis auf menschliche Prüfung.

## Konsequenzen

- Weitere Judges (#28 Laya, #29 Grounding, #30 Gültigkeit) werden Adapter, die Gate-Logik bleibt.
- Der Datensatz enthält Fragen und Textauszüge im Klartext; die Datei liegt lokal und ist nicht im Repo.
- Wohin eskaliert wird (Queue, Fachexperte) ist offen; bis dahin ist die Datei nur Export.
- Noch nicht umgesetzt: Nur `LOW_RELEVANCE` wird erzeugt, die übrigen Reasons sind Platzhalter
  für die Folge-Issues.
