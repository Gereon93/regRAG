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
  Frage und jeder Evidenztext sind auf 2000 Zeichen begrenzt. Die Zeile wird in einem einzigen
  `os.write` auf einen `O_APPEND`-Descriptor geschrieben (Modus `0600`), damit parallele Schreiber
  (Threadpool, mehrere Prozesse) keine Zeilen verschachteln.
- Schreibfehler (`OSError`, `TypeError`, `ValueError`) werden geloggt und kippen die Verweigerung
  nicht. `schreibe_eskalation` meldet, ob es gelang; nur dann verspricht die Antwort die Vormerkung
  zur menschlichen Prüfung, sonst lautet sie nur "keine belastbare Grundlage".

## Konsequenzen

- Weitere Judges (#28 Laya, #29 Grounding, #30 Gültigkeit) werden Adapter, die Gate-Logik bleibt.
- Der Datensatz enthält Fragen und Textauszüge im Klartext; die Datei liegt lokal, ist nicht im Repo
  und nur für den Besitzer lesbar. Aufbewahrung, Rotation und Löschweg: siehe
  Abschnitt "Aufbewahrung und Löschung" unten. Ein Rate-Limit fehlt noch (Issue #44).
- Evaluationsläufe (`evaluation/run.py`) verwerfen Eskalationsdatensätze (`os.devnull`), damit sie
  die echte Datei nicht füllen.
- Wohin eskaliert wird (Queue, Fachexperte) ist offen; bis dahin ist die Datei nur Export.
- Noch nicht umgesetzt: Nur `LOW_RELEVANCE` wird erzeugt, die übrigen Reasons sind Platzhalter
  für die Folge-Issues.

## Aufbewahrung und Löschung (Issue #34)

Fragen können personenbezogen sein; die Datei ist deshalb in Größe und Alter begrenzt
(`gate.EskalationsGrenzen`, Konfiguration über `REGRAG_ESKALATION_MAX_BYTES`, `_MAX_ARCHIVE`,
`_AUFBEWAHRUNG_TAGE`, siehe `env.example`).

- **Rotation:** Vor jedem Append wird die aktive Datei nach `<name>.<UTC-Zeitstempel>.<pid>` verschoben,
  wenn sie `max_bytes` (Default 5 MiB) erreicht oder ihr ältester Datensatz älter als die halbe Frist ist.
  Eine Datei bleibt damit höchstens `max_bytes` − 1 plus eine Zeile (ca. 50 KB) groß.
- **Frist:** Archive, deren ältester Datensatz die Aufbewahrungsfrist (Default **90 Tage**) überschreitet,
  werden gelöscht. Gelöscht wird die ganze Datei; jüngere Datensätze darin gehen mit. Jeder Datensatz lebt
  dadurch mindestens die halbe und höchstens die volle Frist, sofern geschrieben oder gestartet wird.
  Die 90 Tage sind ein **technischer Startwert, keine rechtliche Festlegung.**
- **Anzahl:** Höchstens `max_archive` (Default 5) Archive. Überzählige, älteste zuerst, werden gelöscht
  und mit WARNING geloggt. Das verdrängt Datensätze vor Fristende und ist ein Signal für eine Flut. Die
  Zusage "zur Prüfung vorgemerkt" gilt deshalb nur, solange die Datensätze nicht verdrängt wurden.
  Gesamtgröße höchstens (`max_archive` + 1) × (`max_bytes` + eine Zeile).
- **Start:** Beim Serverstart läuft dieselbe Bereinigung einmal. Ohne neue Verweigerung und ohne Neustart
  wird eine abgelaufene Datei nicht gelöscht; diese Lücke ist bekannt.
- **Archiv-Erkennung:** Nur Namen im Muster `<name>.<YYYYmmddTHHMMSSffffffZ>.<pid>` zählen. Fremde Dateien
  (`.bak`, Editor-Kopien) bleiben unangetastet. Sonderdateien wie `/dev/null` (Evaluation) werden nie rotiert.
- **Zeitstempel:** Ein Zeitstempel ohne Zeitzone gilt als UTC; ein unlesbarer Datensatz verhindert nie den Append.
- **Mehrere Prozesse:** Innerhalb eines Prozesses serialisiert ein Lock Rotation und Append. Mehrere
  Prozesse (z. B. CLI parallel zum Server) können gleichzeitig rotieren. Das ist akzeptiert: keine Zeile
  geht verloren, im ungünstigen Fall entsteht eine früh rotierte kleine Datei oder ein geloggter Rotationsfehler.
  Ein `flock` wäre nur auf POSIX verfügbar und brächte eine zusätzliche Datei im Volume.
- **Rotations- und Bereinigungsfehler** (`OSError`) werden geloggt und kippen weder Append noch Verweigerung.
- **Längenlimit:** `Frage.frage` ist auf 2000 Zeichen (Unicode-Zeichen, nicht Bytes) und mindestens ein
  Zeichen begrenzt; Verstöße liefern 422 statt stillem Abschneiden. Die UI setzt `maxlength="2000"`.
- **Manueller Löschweg:** Server stoppen, dann `rm chroma/eskalationen.jsonl chroma/eskalationen.jsonl.*`.
  Einzelne Personen entfernt man bei gestopptem Server mit einem `jq`-Filter auf die Zeilen in der aktiven Datei
  und in jedem Archiv.
- **Nicht umgesetzt (Issue #44):** Rate-Limit und Begrenzung der Body-Größe vor dem Parsen. Das Längenlimit
  greift erst, nachdem der Body gelesen wurde.
