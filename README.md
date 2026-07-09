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
- [x] **Step 2** — LangGraph-Agent, verweigert unter `MIN_RETRIEVAL_SCORE`
- [x] **Persistenz** — Chroma-Index, Warmstart 12 s statt 5 min ([#1](../../issues/1))
- [ ] **Step 3** — DeepEval: Faithfulness messen, Schwellwert kalibrieren ([#2](../../issues/2))
- [ ] **Step 4** — Web-UI + Docker ([#3](../../issues/3)), Dokumenten-Upload ([#4](../../issues/4))

`deepeval` steht in `requirements.txt`, wird aber noch von keiner Datei importiert. Das ist bekannt und Gegenstand von [#2](../../issues/2).

## Entscheidungen (ADRs)

- [0001](docs/adr/0001-pdf-nach-markdown-statt-pdf-direkt.md) — PDF nach Markdown, statt das PDF direkt zu indexieren
- [0002](docs/adr/0002-abstain-statt-raten.md) — Verweigern statt raten (Schwellwert **unkalibriert**)
- [0003](docs/adr/0003-persistenter-chroma-index-mit-cosine.md) — Persistenter Chroma-Index, erzwungen auf Cosine
- [0004](docs/adr/0004-rollenteilung-llamaindex-langchain-langgraph.md) — Rollenteilung der drei Frameworks

## Gemessen

| Größe | Wert |
|---|---|
| Kaltstart (Index bauen, 351k Zeichen) | 5:04 min |
| Warmstart (Index laden) | 12.2 s |
| Retrieval (`similarity_top_k=3`) | 0.5 s |
| Score eines guten Treffers (Cosine) | 0.72–0.73 |

Nicht gemessen: Faithfulness, die Qualität gegenüber naivem PDF-Parsing, und ob `MIN_RETRIEVAL_SCORE = 0.4` der richtige Schwellwert ist. Siehe [#2](../../issues/2).

## Quellen

DORA-Text: EUR-Lex, CELEX 32022R2554. Wiederverwendung gemäß Beschluss 2011/833/EU.
ISO-Normtexte sind urheberrechtlich geschützt und werden bewusst nicht eingebettet.
