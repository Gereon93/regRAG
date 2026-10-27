# 0011 — Laya als optionaler, lokaler Answerability-Judge hinter dem Gate-Port

Status: akzeptiert als Adapter, **Nutzen unbelegt** (Issue [#28](../../../../issues/28), Teil von #26)

## Kontext

Der Score-Guard ([0010](0010-decision-gate-port-und-eskalationsdatensatz.md)) sagt nur, dass ein
Chunk thematisch passt, nicht, dass er die Frage beantwortet. Ein Judge soll das prüfen, lokal
und ohne externe API. Gewählt ist Laya (`convaiinnovations/laya-multilingual`, Apache 2.0).

## Entscheidung

- `laya.py` spricht das **Jev-Wire-Protokoll** `POST /v1/systemone`, das `laya-serve` lokal
  anbietet. Der Adapter ist ein HTTP-Client gegen dieses Protokoll, nicht gegen Laya-Interna;
  eine gehostete Jev-Instanz ließe sich über `REGRAG_LAYA_URL` austauschen, ohne die Gate-Logik
  oder deren Tests anzufassen. Dass die gehostete API identisch antwortet, ist nicht geprüft.
- Eine `noul`-Frage `answerable` ("Enthält die Evidenz die Antwort auf die Frage?"), State
  `{"frage", "evidenz"}` mit der Frage zuerst. Relevanz bleibt beim Retrieval-Score.
- `Kaskade`: Score-Guard zuerst, Laya nur bei bestandener Relevanz. Der frühere Gate gewinnt bei
  gleichem Score-Feld; bei frühem Abbruch bleibt `answerable` leer.
- **Neue Reason `JUDGE_UNAVAILABLE`** ergänzt die fünf Reasons aus #26. Ein Ausfall (Timeout,
  Verbindung, HTTP-Fehler, ungültige Antwort) führt zu ABSTAIN, nie zu ANSWER, und ist von einem
  fachlichen `NOT_ANSWERABLE` unterscheidbar. 4xx werden als ERROR geloggt (dauerhafte
  Fehlkonfiguration), 5xx und Timeouts als WARNING.
- Opt-in: ohne `REGRAG_LAYA_URL` bleibt das Verhalten wie in 0010.

## Konsequenzen und Grenzen

- **Zero-shot ist nicht belegt.** Die Model Card nennt für Typed Decisions 0.342 gegen 0.318 Zufall
  und ein unkalibriertes Modell. `REGRAG_LAYA_MIN_ANSWERABLE = 0.5` ist ein ungemessener Startwert.
  Vor dem gelabelten Eval-Set (#31) und Fine-Tuning samt Kalibrierung (#32) kann das Einschalten
  viele Anfragen unnötig eskalieren oder fälschlich durchlassen; es ist ein Experiment, kein Schutz.
  Die Zahl darf nicht als gemessenes Ergebnis auftreten.
- **Keine Sicherheitsgrenze.** Chunk-Text stammt auch aus Nutzer-Uploads. Ein Satz im Dokument kann
  ein Klassifikationsmodell Richtung "ja" schieben; Laya trennt Anweisung und Daten nicht.
- **Datenschutz:** Frage und Evidenz gehen an `REGRAG_LAYA_URL`. "Lokal" gilt nur für einen
  lokalen Server; bei jedem anderen Host loggt der Start eine Warnung (blockiert wird nicht, die
  URL kommt vom Betreiber). Zugangsdaten in der URL werden abgelehnt, der Key gehört in
  `REGRAG_LAYA_API_KEY`.
- Der Server arbeitet mit einem Inference-Worker; ein Cold Load oder CPU-Betrieb kann Sekunden
  kosten. `REGRAG_LAYA_TIMEOUT` (Default 30 s) gilt pro Phase, der Connect-Timeout liegt bei 2 s.
  Kein Retry bei 503; ob das nötig ist, zeigt die Messung in #32.
- `max_len` Default 4096 (Genauigkeit fällt laut Model Card jenseits ~4000 Tokens). Ein gekürzter
  State wird geloggt; fehlt `usage` (z. B. `LAYA_JEV_STRICT=1`), gilt das als unbekannt.
- Pro Entscheidung wird das Score-Paar geloggt (auch bei ANSWER), weil die Eskalationsdatei nur
  Verweigerungen kennt und #32 die Verteilungen zum Kalibrieren braucht.
- Offen: Nützlichkeit auf deutschen Rechtstexten (Live-Tests in `tests/test_laya_live.py`, nur mit
  laufendem `laya-serve`), `labels` für `noul`, falls die Antwort den State ignoriert.
