# RegRAG

Ein Compliance-RAG-Agent über **DORA** (Verordnung (EU) 2022/2554), der Fragen nur mit Quellenbeleg beantwortet — und ehrlich abbricht, wenn die Beleglage zu dünn ist, statt zu halluzinieren.

Gebaut, um RAG, LangGraph und LangChain praktisch zu verstehen. Kein Produktionssystem.

## Architektur

| Schicht | Baustein | Aufgabe |
|---|---|---|
| Retrieval | LlamaIndex | PDF → Markdown → Chunks → Embeddings → `retrieve` |
| Formulierung | LangChain | `ChatOpenAI` gegen LM Studio, PromptTemplate erzwingt Quellenangabe |
| Orchestrierung | LangGraph | Zustandsgraph: `retrieve` → `answer` |
| Speicher | ChromaDB | persistenter Vektor-Index, Cosine-Metrik |

Warum drei Frameworks und wo LangGraph seinen Platz noch nicht verdient: [ADR 0004](docs/adr/0004-rollenteilung-llamaindex-langchain-langgraph.md).

## Modelle: was läuft wo

| Rolle | Modell | Ort | Kosten |
|---|---|---|---|
| Embeddings | `BAAI/bge-m3` | lokal, im Prozess | 0 € |
| Generierung | `google/gemma-4-12b` | lokal, LM Studio (`localhost:1234`) | 0 € |
| Generierung (geplant, Issue #3) | frei wählbar | OpenRouter, gehostet | pay-as-you-go |

Embeddings laufen bewusst lokal: multilingual (DORA liegt auf Deutsch vor) und ohne Datenabfluss.
Die Generierung spricht ein OpenAI-kompatibles Interface — LM Studio und OpenRouter sind darüber austauschbar.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

DORA-PDF (DE) liegt unter `docs/`. Quelle: EUR-Lex, CELEX 32022R2554.

LM Studio starten, ein Chat-Modell laden, Modellnamen in `agent.py` (`LOKALES_CHAT_MODELL`) eintragen.

## Lauf

```bash
python convert.py   # PDF -> docs_md/dora.md
python rag.py       # Mini-RAG: Antwort + Quellen-Scores
python agent.py     # LangGraph-Agent mit Abstain-Pfad
```

Der Vektor-Index wird beim ersten Lauf gebaut (~5 min) und danach aus `chroma/` geladen (12 s).
Neuaufbau erzwingen: `REGRAG_INDEX_NEU_BAUEN=1 python agent.py`.

## Stand

- [x] **Step 1** — Mini-RAG über DORA, Antwort mit Belegstellen
- [x] **Step 2** — LangGraph-Agent, Abstain als bedingte Kante, Schwellwert kalibriert (14/14) ([#2](../../issues/2))
- [x] **Persistenz** — Chroma-Index, Warmstart 12 s statt 5 min ([#1](../../issues/1))
- [x] **Index-Integrität** — Fingerprint-Validierung, Neuaufbau bei geänderter Quelle ([#7](../../issues/7))
- [ ] **Step 3** — Web-UI + Docker ([#3](../../issues/3)), Dokumenten-Upload ([#4](../../issues/4))

## Evaluation

```bash
python -m evaluation.calibrate   # misst Score-Trennung, schlägt Schwellwert vor
python -m evaluation.run         # Guard-Entscheidungen (14/14) + Faithfulness (lokaler Judge)
```

Das Eval-Set (`evaluation/dataset.py`) enthält 8 beantwortbare DORA-Fragen und 6 themenfremde. Der Guard trennt sie 14/14. Die Faithfulness-Harness (`deepeval`, lokaler Judge über `with_structured_output`) ist gebaut und schema-korrekt, aber ein Volllauf ist auf dem M4 nicht praktikabel: schema-gebundene Extraktion über dichte Rechtstexte reißt lokal die Timeouts. Eine belastbare Faithfulness-Zahl braucht einen gehosteten Judge (Issue #3) — Details in [ADR 0005](docs/adr/0005-guard-kalibriert-abstain-als-bedingte-kante.md).

## Dokumentation

- [Architekturdokumentation (arc42)](docs/arc42.md) — Kontext, Bausteine, Laufzeit, und die technischen Schulden ungeschönt in Kapitel 11.

## Entscheidungen (ADRs)

- [0001](docs/adr/0001-pdf-nach-markdown-statt-pdf-direkt.md) — PDF nach Markdown, statt das PDF direkt zu indexieren
- [0002](docs/adr/0002-abstain-statt-raten.md) — Verweigern statt raten (Schwellwert **unkalibriert**)
- [0003](docs/adr/0003-persistenter-chroma-index-mit-cosine.md) — Persistenter Chroma-Index, erzwungen auf Cosine
- [0004](docs/adr/0004-rollenteilung-llamaindex-langchain-langgraph.md) — Rollenteilung der drei Frameworks (LangGraph-Schuld eingelöst)
- [0005](docs/adr/0005-guard-kalibriert-abstain-als-bedingte-kante.md) — Guard kalibriert, Abstain als bedingte Kante

## Gemessen

| Größe | Wert |
|---|---|
| Kaltstart (Index bauen, 351k Zeichen) | 5:04 min |
| Warmstart (Index laden) | 12.2 s |
| Retrieval (`similarity_top_k=3`) | 0.5 s |
| Score beantwortbarer DORA-Fragen | 0.67–0.78 |
| Score themenfremder Fragen | 0.48–0.57 |
| `MIN_RETRIEVAL_SCORE` (Lückenmitte) | 0.62 |
| Guard-Trennung über das Eval-Set | 14/14 |

Der Score ist `exp(-Distanz)`, nicht rohe Cosine-Similarity — nur innerhalb dieser Transformation interpretierbar (ADR 0003). Noch offen: Grenzfälle nahe der Schwelle (Finanzregulatorik außerhalb DORA) und die Qualität gegenüber naivem PDF-Parsing.

## Quellen

DORA-Text: EUR-Lex, CELEX 32022R2554. Wiederverwendung gemäß Beschluss 2011/833/EU.
ISO-Normtexte sind urheberrechtlich geschützt und werden bewusst nicht eingebettet.
