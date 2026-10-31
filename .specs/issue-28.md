# Spec: Gate: Laya als lokaler Answerability-Judge (System-One-Adapter)

**Issue:** #28 (https://github.com/Gereon93/regRAG/issues/28), Teil von #26
**Status:** approved
**Datum:** 2026-10-27
**Branch:** issue-28-laya-judge
**Architekt:** claude-sonnet-5-5
**Worker:** claude-sonnet-5-5

---

## 1. Problem & Fachlicher Intent
- **Problem:** Der Score-Guard (#27) sagt nur, dass ein Chunk thematisch passt, nicht, dass er die Frage beantwortet.
- **Nutzen:** `NOT_ANSWERABLE` wird als Gate-Grund möglich, lokal ohne externe API.
- **Ehrliche Grenze:** Ob Laya zero-shot auf deutschen Rechtstexten Evidenz von Nicht-Evidenz trennt, ist **unbelegt** (Model Card: Typed Decisions zero-shot 0.342 vs. 0.318 Zufall). Dieses Issue liefert den Adapter, nicht den Nachweis. Vor #31/#32 ist das Einschalten ein Experiment, kein Schutz.
- **Non-Goals:** kein Fine-Tuning, keine Kalibrierung, keine Zero-shot-Baseline-Messung (braucht das Eval-Set, #31/#32), keine `conflict`-Fragen (#30), kein `relevance` über Laya, keine Compose-Integration des Laya-Servers, keine Sicherheitsgrenze gegen Prompt-Injection.

## 2. Verifizierte Schnittstelle (Quelle: Model Card `convaiinnovations/laya-multilingual`, `docs/http-api.md`, `docs/questions-and-answers.md` im Laya-Repo, 2026-10-27; vom Prüfermodell gegengelesen)
- System-One-Protokoll = Jev-Wire-Protokoll `POST /v1/systemone`; `laya-serve` (`pip install "laya[serve]"`) spricht es lokal.
- Request: `state` (Text/JSON, serialisiert max. 50.000 Zeichen -> sonst 413), `questions` (id -> `{type, instructions, labels?}`), optional `lang`, `max_len`, `model`. `Authorization: Bearer` nur, wenn der Server `LAYA_API_KEY` setzt.
- Response `answers[id]["noul"]` = P(ja) als Zahl. Bei `LAYA_JEV_STRICT=1` fehlen `routing` und weite Teile von `usage`; ein fehlendes `usage.truncated` heißt "unbekannt", nicht `false`.
- Fehler laut Doku: 400, 401, 413, 422, 500, 503 (408 beim Body-Timeout). Ein einzelner Inference-Worker bedient Anfragen nacheinander; bei `LAYA_PRELOAD=0` oder Idle-Unload lädt die erste Anfrage den Checkpoint (Cold Load).
- Model Card: unkalibriert, `noul` kann "true" untermelden; Doku nennt `labels` als Gegenmittel, wenn `noul` den State ignoriert (Laya-Issue #1244).
- **Nicht verifiziert:** Verhalten auf deutschen Rechtstexten; ob die Antwort überhaupt von der Evidenz abhängt (-> Live-Test); ob die gehostete Jev-API identisch antwortet.

## 3. Funktionsweise und Entscheidungen
1. **`laya.py`** (httpx, synchron): `SystemOneClient.entscheide(state, fragen, **optionen) -> dict` kennt nur das Wire-Protokoll. `LayaGate` implementiert `Gate`. `baue_gate(einstellungen)` setzt die Kaskade aus der Config zusammen (testbar ohne `agent.py`, das llama-index/langchain importiert).
2. **Frage:** `noul` `answerable`, Instruktion "Enthält die Evidenz die Antwort auf die Frage?" (bewusst ohne "vollständig": Top-3-Chunks beantworten Rechtsfragen selten vollständig und das Modell untermeldet "ja" ohnehin). `state = {"frage": ..., "evidenz": "<Chunks>"}` in dieser Reihenfolge, **`frage` zuerst**, damit eine Kürzung am Ende nicht die Frage trifft (Test auf Schlüsselreihenfolge). Request pinnt `model: "multilingual"` und `lang` (Default `de`); `routing.model` wird, wenn vorhanden, geprüft und bei Abweichung als Warnung geloggt.
3. **Entscheidung:** `P(ja) < min_answerable` -> ABSTAIN/`NOT_ANSWERABLE`, `scores.answerable = P(ja)`. Gleichstand = ANSWER.
4. **`Kaskade(gates)`:** leere Liste -> `ValueError`. Gates laufen der Reihe nach, das erste ABSTAIN gewinnt, Scores werden zusammengeführt, **der frühere Gate gewinnt bei gleichem Feld**. Score-Guard zuerst, Laya nur bei bestandener Relevanz. `answerable` fehlt damit bei frühem Abbruch (`None`), das ist gewollt und dokumentiert.
5. **Scores pro Entscheidung loggen** (INFO, strukturiert: Outcome, Reasons, relevance, answerable), auch bei ANSWER, weil #32 die Verteilungen zum Kalibrieren braucht und die Eskalationsdatei nur Abstains kennt.
6. **Parsing streng:** Wert muss `int` oder `float` (kein `bool`), endlich und in [0, 1] sein. Jeder Verstoß, jeder Transport-/HTTP-/JSON-/Schlüssel-/Typfehler (`httpx.HTTPError`, `ValueError`, `KeyError`, `TypeError`) -> ABSTAIN mit neuer Reason **`JUDGE_UNAVAILABLE`**, nie ANSWER, nie Exception an den Aufrufer. Kein blindes `except Exception`. Die Erweiterung des Reason-Katalogs aus #26 begründet **ADR 0011**.
7. **Fehlerklassen:** 4xx (Konfigurations-/Vertragsfehler wie 401, 413, 422) werden als **ERROR** geloggt (Status, kein Body, kein Key), 5xx/Timeout/Verbindung als WARNING. Eine dauerhafte Fehlkonfiguration ist so im Log sichtbar; eine Start-Probe-Anfrage entfällt (YAGNI, `/health` meldet bei falschem Key trotzdem 200).
8. **Timeouts:** `httpx.Timeout` gilt pro Phase. `connect` 2 s, `read` `REGRAG_LAYA_TIMEOUT` (Default 30 s, deckt Cold Load und CPU). Kein Retry bei 503 (Verhalten dokumentiert, Entscheidung nach Messung in #32). `max_len` Default 4096 (Model Card: Genauigkeit fällt jenseits ~4000 Tokens), konfigurierbar. `follow_redirects` aus.
9. **Config (`REGRAG_LAYA_*`, getrennt von `JUDGE_*` für DeepEval):** `URL` (leer/Leerraum = aus), `API_KEY`, `TIMEOUT`, `MAX_LEN`, `LANG`, `MIN_ANSWERABLE` (Default 0.5, **ungemessen**, muss in [0,1] liegen, sonst Fehler beim Laden). URL mit Zugangsdaten (`user:pass@`) wird abgelehnt, damit kein Key über Fehlermeldungen leckt.
10. **Datenschutz:** Frage und Evidenz gehen an `REGRAG_LAYA_URL`. Das Akzeptanzkriterium "läuft lokal" gilt nur für einen lokalen Server. Zeigt die URL nicht auf `localhost`, `127.0.0.1`, `::1` oder `host.docker.internal`, loggt der Start eine WARNING. Blockiert wird nicht; die Abweichung (Jev gehostet) steht in ADR 0011.
11. **Prompt-Injection:** Chunk-Text kommt über `/upload` von Nutzern und kann das Modell Richtung "ja" schieben. Das Gate ist keine Sicherheitsgrenze; ADR 0011 und README sagen das. Ein Injection-Fall gehört in die Live-Fixtures.
12. **`web/main.py`** ruft das Gate bereits per `asyncio.to_thread` (#33). `retriever.retrieve` blockiert die Loop weiterhin (bestehend, nicht Teil dieses Issues).
13. **Abweichung zum Issue-Text:** "Laya-Server als Testcontainer" entfällt in CI (Torch + 640 MB Checkpoint). Ersatz: Contract-Tests gegen `httpx.MockTransport` plus Live-Test, der nur mit `REGRAG_LAYA_URL` läuft und prüft, dass `P(ja)` sich zwischen passender und unpassender Evidenz deutlich verschiebt, plus ein Injection-Fall. `httpx` kommt explizit in `requirements.txt`.

## 4. Invarianten
- Ohne `REGRAG_LAYA_URL` ändert sich kein Verhalten gegenüber #27.
- Jeder Fehlerpfad liefert ABSTAIN mit Reason; kein stilles ANSWER; keine Exception an den Aufrufer.
- Der Bearer-Key steht in keinem Log (auch nicht auf DEBUG), keiner Fehlermeldung und keinem `repr`.
- Bei ABSTAIN wird das LLM nicht aufgerufen.
- Gate-Logik und deren Tests kennen weder httpx noch Laya.

## 5. Randfälle
- Keine Treffer: Score-Guard verweigert, kein Laya-Aufruf.
- Evidenz länger als 50.000 Zeichen serialisiert: Evidenz wird auf eine konfigurierbare Zeichengrenze gekürzt (Default 20.000), nie ein 413.
- `usage.truncated is True`: WARNING; fehlt `usage`: unbekannt, kein Fehler.
- Antwort ohne `answerable`, `noul` = `True`/`"0.9"`/NaN/inf/-0.1/1.1: `JUDGE_UNAVAILABLE`. `0`, `1`, `0.0`, `1.0` gültig.
- Body kein JSON / leer: `JUDGE_UNAVAILABLE`.
- Redirect (3xx): nicht gefolgt, `JUDGE_UNAVAILABLE`.
- `REGRAG_LAYA_URL` nur Leerraum: Laya aus. Trailing Slash wird normalisiert.

## 6. Akzeptanzkriterien und Testplan
- [ ] Contract (`tests/test_laya_client.py`, `httpx.MockTransport`): Request-Schema inkl. Pfad, Header nur mit Key, `state`-Schlüsselreihenfolge, `model`, `lang`, `max_len`; Response-Parsing; Fehler Timeout, Verbindung, 400/401/413/422/500/503, 3xx, kaputtes JSON, fehlender/ungültiger Wert (bool, NaN, inf, Bereich)
- [ ] Log-Hygiene: `caplog` auf DEBUG über alle Logger, Key kommt nirgends vor (Erfolg und jeder Fehlerpfad); 4xx -> ERROR, 5xx -> WARNING
- [ ] Unit (`tests/test_laya_gate.py`): Grenzfälle 0.499/0.5/0.501, Ausfall -> `JUDGE_UNAVAILABLE`, Kaskade (Reihenfolge, früherer Gate gewinnt, leere Liste, kein Laya-Aufruf nach ABSTAIN, `answerable` None bei Abbruch), Scores-Logging
- [ ] Verdrahtung (`baue_gate`): URL leer -> nur Score-Guard; URL gesetzt -> Kaskade Score-Guard vor Laya
- [ ] Config: Defaults, `MIN_ANSWERABLE` außerhalb [0,1] -> Fehler, URL mit Zugangsdaten -> Fehler, Warnung bei Nicht-Loopback-Host
- [ ] Integration (`tests/test_web_gate.py`): Web-Strom mit echter Kaskade und Mock-Transport -> Abstain `NOT_ANSWERABLE`, Datensatz mit `answerable`; Laya-Ausfall -> Abstain `JUDGE_UNAVAILABLE`
- [ ] Live, optional (`tests/test_laya_live.py`, übersprungen ohne `REGRAG_LAYA_URL`): passende vs. unpassende Evidenz verschiebt `P(ja)`, Injection-Fall dokumentiert das Ergebnis
- [ ] Austausch des Adapters ohne Änderung an Gate-Logik oder deren Tests
- [ ] Vor Push: gepinntes `ruff@0.16.3 check .`, `compileall` inkl. `laya.py`, `pytest` (auch `TZ=UTC`)

## 7. Adversarial Grill-Protokoll
- **Ausgeführt:** Fremdmodell-Review (Opus-Subagent, frischer Kontext, gegen Laya-Doku gegengelesen), kein `/grill`-Skill.
- **Runde 1, Lücken:** (1) `noul`-Frage nicht durch Doku gedeckt, Abhängigkeit von Evidenz unbelegt; (2) "vollständig" verzerrt Richtung Nein; (3) Fehlerliste unvollständig, 4xx als Dauerfehler getarnt; (4) Timeout pro Phase, Cold Load, Single-Worker; (5) Kürzungs-Erkennung hängt am Server-Modus, `frage` muss zuerst; (6) Kaskade unterspezifiziert, `answerable` bei ANSWER nirgends erfasst; (7) Parsing: bool, inf, breiter Fang, ADR für neue Reason; (8) Key-Leak über URL/`repr`/DEBUG; (9) Jev widerspricht "lokal"; (10) Prompt-Injection; (11) Parallelität des Eskalations-Writes; (12) Testlücken (Verdrahtung, Routing, Config, `httpx` in requirements).
- **Eingearbeitet:** alle, siehe §3 (Punkte 2, 4, 5, 6, 7, 8, 9, 10, 13), §5, §6. (11) war in #33 bereits gelöst (`os.write` auf `O_APPEND`). Nicht eingearbeitet: Start-Probe-Anfrage (YAGNI, begründet in §3.7), Allowlist statt Warnung (Betreiber-URL, SSRF kein echtes Risiko).
- **Abschluss-Verdikt:** APPROVED (2026-10-27, Prüfer Opus-Subagent, Einarbeitung Sonnet 5.5). Offen bleibt der Nachweis der Nützlichkeit (#31/#32).
