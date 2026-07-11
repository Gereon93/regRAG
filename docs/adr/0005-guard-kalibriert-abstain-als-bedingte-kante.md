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
- Die LLM-Konfiguration liegt in `config.py` (Umgebungsvariablen), damit der DeepEval-Judge
  denselben lokalen Endpunkt nutzen kann und Issue #3 das Backend ohne Codeänderung tauscht.

## Offen

- Das Eval-Set ist klein (14 Fälle) und die Off-Topic-Fragen sind bewusst *klar* fremd.
  Grenzfälle — Finanzregulatorik, die *nicht* in DORA steht (MaRisk, EBA-Guidelines) —
  sind noch nicht abgedeckt und könnten näher an der Schwelle liegen.
- Faithfulness der beantworteten Fälle wird über einen lokalen Judge gemessen
  (`evaluation/run.py`), ist aber wegen der Inferenzlatenz von `gemma-4-12b` auf dem M4
  langsam; im Alltag läuft sie über eine Stichprobe, nicht über das ganze Set.
