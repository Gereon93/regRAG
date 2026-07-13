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

## Offen

- Das Eval-Set ist klein (14 Fälle) und die Off-Topic-Fragen sind bewusst *klar* fremd.
  Grenzfälle — Finanzregulatorik, die *nicht* in DORA steht (MaRisk, EBA-Guidelines) —
  sind noch nicht abgedeckt und könnten näher an der Schwelle liegen.
- Faithfulness ist als Harness gebaut und **schema-korrekt** (`evaluation/run.py`,
  `judge.py`): DeepEvals Pydantic-Schemas gehen über LangChains
  `with_structured_output` an den lokalen Judge, was valides JSON erzwingt. Damit ist
  `deepeval` jetzt tatsächlich benutzt, nicht mehr toter Requirements-Eintrag.

  **Aber: ein vollständiger lokaler Faithfulness-Lauf ist auf dem M4 nicht praktikabel.**
  Gemessen über fünf Konfigurationen:
  - `gemma-4-12b` als Judge: Timeout schon bei der Antwortgenerierung (>300 s).
  - `llama-3.1-8b` / `qwen3.5-9b` **ohne** Schema: schnell, aber ungültiges JSON.
  - dieselben **mit** Schema: JSON valide, aber der Schema-Zwang (constrained decoding
    in LM Studio) über die dichten Rechtstext-Chunks bringt einzelne Judge-Aufrufe
    wieder über 300 s.

  Das ist ein **Kosten/Latenz-Befund**, kein Fehler: Ein lokaler Judge ist gratis, aber
  für schema-gebundene Extraktion über lange juristische Kontexte auf dieser Hardware zu
  langsam. Eine belastbare Faithfulness-Zahl braucht einen **gehosteten Judge**
  (OpenRouter oder ein anderer OpenAI-kompatibler Endpunkt). Der Harness ist dafür
  vorbereitet; bis ein solcher Lauf mit Credentials und Dokumentindex ausgeführt wurde,
  bleibt Faithfulness **nicht gemessen** und wird nicht behauptet.
