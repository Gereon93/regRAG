# RegRAG

Ein Compliance-RAG-Agent über **DORA** (Verordnung (EU) 2022/2554), der Fragen nur mit Quellenbeleg beantwortet — und ehrlich abbricht, wenn die Beleglage zu dünn ist, statt zu halluzinieren.

Gebaut, um RAG, LangGraph und LangChain praktisch zu verstehen. Kein Produktionssystem.

## Architektur

| Schicht | Baustein | Aufgabe |
|---|---|---|
| Retrieval | LlamaIndex | PDF → Markdown → Chunks → Embeddings → `retrieve` |
| Formulierung | LangChain | `ChatOpenAI` gegen LM Studio, PromptTemplate erzwingt Quellenangabe |
| Orchestrierung | LangGraph | Zustandsgraph: `retrieve` → `answer` \| `abstain` |

Embeddings laufen rein lokal (`BAAI/bge-m3`, multilingual — DORA liegt auf Deutsch vor).
Die Generierung geht gegen **LM Studio** (`localhost:1234`, OpenAI-kompatibel).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

DORA-PDF (DE) von EUR-Lex nach `docs/` legen:
<https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554>

LM Studio starten, ein Chat-Modell laden, Modellnamen in `agent.py` (`LOKALES_CHAT_MODELL`) eintragen.

## Lauf

```bash
python convert.py   # PDF → docs_md/dora.md (pymupdf4llm erhält die Artikelstruktur)
python rag.py       # Step 1: Mini-RAG, Antwort + Quellen-Scores
python agent.py     # Step 2: LangGraph-Agent mit Abstain-Pfad
```

## Stand

- [x] **Step 1** — Mini-RAG über DORA, Antwort mit Belegstellen
- [x] **Step 2** — LangGraph-Agent, verweigert die Antwort unter `MIN_RETRIEVAL_SCORE`
- [ ] **Step 3** — DeepEval (Faithfulness), lokaler Judge
- [ ] **Step 4** — Model-Router: lokal ↔ OpenRouter, nach Kosten/Latenz

Bekannte Baustelle: Der Index wird bei jedem Lauf neu eingebettet (~6 min). Persistenz via ChromaDB steht aus.

## Entscheidungen

**PDF → Markdown statt PDF direkt.** Der Zweispaltensatz von EUR-Lex zerlegt naives PDF-Parsing; `pymupdf4llm` erhält die Artikel- und Absatzstruktur, die für sinnvolle Chunks nötig ist.

**Abstain statt raten.** Liegt der beste Retrieval-Score unter `MIN_RETRIEVAL_SCORE`, antwortet der Agent „Nicht eindeutig in DORA belegt." In einem regulierten Umfeld ist eine verweigerte Antwort billiger als eine erfundene. Der Schwellwert ist noch nicht empirisch kalibriert.

## Quellen

DORA-Text: EUR-Lex, CELEX 32022R2554. Wiederverwendung gemäß Beschluss 2011/833/EU.
