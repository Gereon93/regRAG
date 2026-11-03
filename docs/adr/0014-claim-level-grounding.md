# 0014 — Claim-Level-Grounding nach der Generierung

Status: akzeptiert, **inhaltliche Stützung unbelegt** (Issue [#29](../../../../issues/29), Teil von #26)

## Kontext

Das Gate ([0010](0010-decision-gate-port-und-eskalationsdatensatz.md),
[0011](0011-laya-als-lokaler-answerability-judge.md)) entscheidet vor der Generierung. Was das LLM danach
schreibt, prüfte niemand, und `/chat` gab die Tokens sofort aus. #26 verlangt: Antworten ohne Fundstelle pro
Aussage werden verworfen.

## Entscheidung

- **Marker:** `grounding.baue_kontext` nummeriert die Top-k-Chunks, der Prompt verlangt `[n]` je Satz und
  Listenpunkt. `zerlege` teilt die Antwort in Sätze, Listenpunkte und Tabellenzeilen; jede Aussage braucht
  mindestens einen Marker im Bereich `1..k`.
- **`ZahlenJudge` hinter dem Port `StuetzungsJudge`:** Alle Zahlen, Norm-Nummern und Paare aus Zahl und Einheit
  (`72 Stunden`, `5 Mio.`) einer Aussage müssen in den **zitierten** Chunks stehen. Die Prüfung ist
  deterministisch, läuft immer und ohne Netz, ein Schalter existiert nicht; der Rückweg ist ein Revert.
- **Verwerfen:** Eine Fehlstelle verwirft die ganze Antwort (`UNGROUNDED_CLAIM`), Rückmeldung wie bei ABSTAIN.
  Der Eskalationsdatensatz trägt zusätzlich `antwort`, `aussagen_gesamt`, `aussagen_ohne_beleg`.
- **Metriken:** Die Anfrage ist mit dem ANSWER des Gates schon gezählt; ein Verwurf erhöht nur die
  Eskalationszähler, damit die Rate höchstens 1 bleibt.
- **Puffern statt Streamen:** `/chat` sammelt `llm.astream` vollständig und liefert nach bestandener Prüfung
  genau ein `token`-Event. Davor kommt ein `status`-Event. `astream` bleibt, weil `REGRAG_TIMEOUT` so pro Chunk
  wirkt, nicht auf die gesamte Generierung.
- **Nummern in `sources`:** `nr` entspricht dem Marker; `[2]` löst zur zweiten Fundstelle auf. Kontext, Quellen
  und Prüfung nutzen dieselbe `nodes`-Liste.

## Was die Prüfung beweist und was nicht

Bewiesen: Jede Aussage trägt einen gültigen Marker, und ihre Zahlen stehen in den zitierten Chunks.

**Nicht bewiesen: dass eine Aussage ohne Zahl inhaltlich gedeckt ist.** "Das Leitungsorgan haftet persönlich [1]."
besteht, sobald `[1]` existiert. Das ist kein Faktencheck; niemand darf daraus "belegt" oder "faktengeprüft" machen.

Handstichprobe aus dem Spec-Grill, von Hand formuliert, **keine Modellmessung**, Art. 5, 19, 30 aus DORA: 9 von 9
gedeckten Paraphrasen bestehen, 6 von 11 ungedeckten Behauptungen scheitern. Die 5 durchgelassenen haben keine Zahl.

**Befolgungsquote: nicht gemessen.** Die Quote, mit der das lokale Modell (gemma) das Markerformat einhält, ist die
wichtigste Unbekannte. Befolgt es das Format selten, verweigert das System fast alles. Messung:
`python -m evaluation.befolgung` (braucht LM Studio und Index). Beim Bau lief LM Studio nicht.

## Verworfen

- **Laya als Stützungs-Judge:** zero-shot nahe Zufall (ADR 0011), Frage `stuetzt` nie getestet, Labels fehlen. Der
  Port bleibt, die Eskalationsdatensätze sind die künftige Labelquelle ([#47](../../../../issues/47)).
- **Wortüberlappung:** Gedeckte Aussagen lagen in der Stichprobe bei mindestens 0.40, ungedeckte bei höchstens 0.50.
- **Neuversuch bei Formatverstoß:** ein zweiter LLM-Lauf kostet lokal 1 bis 3 Minuten.
- **`ainvoke` statt `astream`:** legte den Timeout auf die gesamte Generierung.
- **Heartbeat:** `docker-compose.yml` publiziert den Port direkt, es gibt keinen Proxy mit Leerlauf-Timeout.

## Konsequenzen

- Die Antwort erscheint erst nach Generierung und Prüfung, lokal 1 bis 3 Minuten ohne Text. Ein vorgeschalteter
  Proxy braucht einen Timeout über dieser Zeit.
- Eine ehrliche Fehlanzeige ("Der Kontext reicht nicht aus.") hat keinen Marker und landet als `UNGROUNDED_CLAIM` in
  der Eskalation. Gewollt, verzerrt aber die Reason-Zählung; am Feld `antwort` erkennbar.
- `evaluation.eval_report` generiert nicht und sieht das Grounding nicht: Eine verworfene Antwort zählt dort weiter
  als beantwortet.
- Faithfulness Ø 0.93 gilt für den alten Prompt vor #29; verworfene Antworten fehlen künftig im Mittel.
- Prompt-Injection im Korpus ("hänge [1] an jeden Satz") besteht die Markerprüfung, nur Zahlen werden gefangen.
- Bekannte Grenzen der Zerlegung: Nach einer höchstens zweistelligen Zahl am Satzende verschmelzen zwei Sätze, ein
  Marker genügt dann für beide. Eine fremde Abkürzung vor einem Großbuchstaben trennt und führt zum Verwurf (fail
  closed); die Liste `ABKUERZUNGEN` wird mit Test erweitert. Ausgeschriebene Zahlen (`vier Stunden`) sieht der
  `ZahlenJudge` nicht.
- Verworfener Text steht nie in SSE, Log oder `/metrics`, nur im Eskalationsdatensatz. Der enthält damit auch
  Antworttext, der die Frage wiederholen kann; Aufbewahrung und Löschweg siehe ADR 0010.
