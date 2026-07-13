# 0005 — Abstain-Schwellwert kalibriert, Abstain als bedingte Graph-Kante

Status: akzeptiert

## Kontext

Zwei offene Schulden trafen sich hier:

- [0002](0002-abstain-statt-raten.md): `MIN_RETRIEVAL_SCORE = 0.4` war geraten und, wie
  die Korrektur dort zeigt, **de facto wirkungslos** — Off-Topic-Unsinnsfragen scorten
  über der Schwelle.
- [0004](0004-rollenteilung-llamaindex-langchain-langgraph.md): Die Verweigerung steckte
  als `if` im `answer`-Knoten; der LangGraph war eine gerade Linie.

## Entscheidung

**Schwellwert aus Messung statt Schätzung.** Ein Eval-Set aus 8 beantwortbaren DORA-Fragen
und 6 bewusst themenfremden Fragen (`evaluation/dataset.py`) wird durch den Retriever
geschickt. `evaluation/calibrate.py` misst die Top-Scores beider Gruppen:

| Gruppe | Score-Bereich |
|---|---|
| Beantwortbar (DORA) | 0.673 – 0.777 |
| Off-Topic | 0.481 – 0.568 |

Zwischen beiden liegt eine saubere Lücke `[0.568 .. 0.673]`. `MIN_RETRIEVAL_SCORE` steht
auf **0.62** (Mitte der Lücke). Damit trennt der Guard **14/14** Fälle korrekt.

**Abstain als eigener Knoten.** `agent.py` hat jetzt einen `abstain`-Knoten, erreicht über
`add_conditional_edges("retrieve", naechster_schritt, …)`. Der Graph verzweigt sichtbar:

```
retrieve ──▶ answer  ──▶ END
      └────▶ abstain ──▶ END
```

Damit ist die Verweigerung eine Eigenschaft des Graphen, nicht eine versteckte Zeile — und
LangGraph verdient seinen Platz (Schuld aus 0004 eingelöst).

## Konsequenzen

- Q2 (keine erfundenen Antworten) ist von „gemessen verletzt" auf „gemessen wirksam"
  gedreht: Der Schokoladenkuchen wird verweigert.
- Der Wert 0.62 gilt **nur** für diesen Store, diese Metrik, dieses Embedding-Modell und
  diese Score-Transformation (`exp(-Distanz)`, siehe 0003). Ein Wechsel erfordert
  Neukalibrierung — `evaluation/calibrate.py` ist dafür da.
- Die LLM-Konfiguration liegt in `config.py` (Umgebungsvariablen). Der DeepEval-Judge kann
  denselben Endpunkt wie das Antwortmodell nutzen oder über `REGRAG_JUDGE_BASE_URL`,
  `REGRAG_JUDGE_MODELL`, `REGRAG_JUDGE_API_KEY` und `REGRAG_JUDGE_TIMEOUT` getrennt auf
  einen gehosteten OpenAI-kompatiblen Endpunkt zeigen.

## Faithfulness: lokaler Judge verworfen, gehosteter Judge gemessen

**Ein lokaler Judge ist auf dem M4 nicht praktikabel.** Geprüft über fünf Konfigurationen:

- `gemma-4-12b` als Judge: Timeout schon bei der Antwortgenerierung (>300 s).
- `llama-3.1-8b` / `qwen3.5-9b` **ohne** Schema: schnell, aber ungültiges JSON.
- dieselben **mit** Schema: JSON valide, aber der Schema-Zwang (constrained decoding in
  LM Studio) über die dichten Rechtstext-Chunks bringt einzelne Judge-Aufrufe wieder über
  300 s.

Das ist ein Kosten/Latenz-Befund, kein Fehler: Ein lokaler Judge ist gratis, aber für
schema-gebundene Extraktion über lange juristische Kontexte auf dieser Hardware zu langsam.
Deshalb **trennt `config.py` Judge und Antwortmodell**: Die App generiert weiter lokal, nur
die Bewertung geht über `REGRAG_JUDGE_BASE_URL`, `REGRAG_JUDGE_MODELL`,
`REGRAG_JUDGE_API_KEY` und `REGRAG_JUDGE_TIMEOUT` an einen gehosteten OpenAI-kompatiblen
Endpunkt.

Der Lauf mit `openai/gpt-5.4-mini` über OpenRouter (Generierung lokal auf `gemma-4-12b`,
Index unverändert) ergibt über die 8 beantwortbaren Fälle:

| Fall | Faithfulness |
|---|---|
| IKT-Risikomanagement | 1.00 |
| Schwerwiegender IKT-Vorfall | 1.00 |
| IKT-Drittparteienrisiko | 1.00 |
| Tests der operationalen Resilienz | 0.82 |
| Rolle des Leitungsorgans | 1.00 |
| Meldepflichten | 1.00 |
| Ausstiegsstrategien | 0.92 |
| Bedrohungsgeleitete Penetrationstests | 0.71 |
| **Ø** | **0.93** |

Kein Fall wurde fälschlich verweigert; alle 8 liefen durch den `answer`-Pfad. Damit ist das
Akzeptanzkriterium aus #2 erfüllt: Off-Topic-Fragen verweigern reproduzierbar (14/14), und
die Treue der beantworteten Fälle ist gemessen statt behauptet.

## Offen

- Das Eval-Set ist klein (14 Fälle) und die Off-Topic-Fragen sind bewusst *klar* fremd.
  Grenzfälle — Finanzregulatorik, die *nicht* in DORA steht (MaRisk, EBA-Guidelines) —
  sind noch nicht abgedeckt und könnten näher an der Schwelle liegen.
- TLPT (0.71) ist der schwächste Fall: Die Antwort trägt Aussagen, die die drei abgerufenen
  Chunks nicht vollständig decken. Ob das am Retrieval (`similarity_top_k=3` zu eng) oder am
  Antwortmodell liegt, ist nicht auseinandergehalten.
- Die Zahl gilt für *diesen* Judge. Ein anderer Judge verschiebt sie; Faithfulness ist eine
  Judge-relative Größe, kein absoluter Messwert.
