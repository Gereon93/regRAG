# RegRAG

Ein Compliance-RAG-Agent über **DORA** (Verordnung (EU) 2022/2554), der Fragen nur mit Quellenbeleg beantwortet — und ehrlich abbricht, wenn die Beleglage zu dünn ist, statt zu halluzinieren.

Gebaut, um RAG, LangGraph und LangChain praktisch zu verstehen. Kein Produktionssystem.

![RegRAG Web-UI: Antwort mit Artikelbezug und Fundstellen aus der DORA-Verordnung](docs/ui.png)

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

LM Studio starten, ein Chat- und optional ein Embedding-Modell laden. Endpoint und Modellname kommen aus Umgebungsvariablen (Defaults in `config.py`, Vorlage in `env.example`).

### DORA-Rechtstext beschaffen

Der DORA-Text wird nicht im Repository mitgeliefert. Er ist auf EUR-Lex frei verfügbar:

1. PDF herunterladen: https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554
2. Nach `docs/CELEX_32022R2554_DE_TXT.pdf` speichern.
3. `python convert.py` ausführen — das erzeugt `docs_md/CELEX_32022R2554_DE_TXT.md` und die `.source.json`.

Damit ist die Quelle nachvollziehbar reproduzierbar. Wiederverwendung gemäß Beschluss 2011/833/EU.

## Lauf

```bash
python convert.py   # PDF -> docs_md/CELEX_32022R2554_DE_TXT.md
python rag.py       # Mini-RAG: Antwort + Quellen-Scores
python agent.py     # LangGraph-Agent mit Abstain-Pfad
```

Der Vektor-Index wird beim ersten Lauf gebaut (~5 min) und danach aus `chroma/` geladen (12 s).
Neuaufbau erzwingen: `REGRAG_INDEX_NEU_BAUEN=1 python agent.py`.

## Web-UI

```bash
uvicorn web.main:app --reload      # http://localhost:8000
```

Chat-Seite mit token-für-token-Streaming (SSE). Jede Antwort nennt ihre Fundstellen mit Score; bei zu dünner Beleglage erscheint der Abstain-Zustand sichtbar statt einer erfundenen Antwort. `POST /chat` ist auch ohne UI nutzbar.

## Docker

Voraussetzung: das DORA-PDF liegt lokal unter `docs/CELEX_32022R2554_DE_TXT.pdf` (siehe [DORA-Rechtstext beschaffen](#dora-rechtstext-beschaffen)).

```bash
docker compose up --build
```

Das Image backt `BAAI/bge-m3` ein (offline lauffähig). Der Container spricht per Default über `host.docker.internal:1234` das LM Studio auf dem Host an — das funktioniert auf **macOS**. Für Linux/Cloud, wo eine Mac-Desktop-App nicht erreichbar ist, zeigt man `REGRAG_LLM_BASE_URL` auf einen gehosteten Worker (OpenRouter) oder einen headless lokalen Server; siehe `env.example` und [arc42 Kap. 7](docs/arc42.md). Der Chroma-Index liegt in einem Volume — erster Start baut ihn (~5 min), danach schnell.

## Stand

- [x] **Step 1** — Mini-RAG über DORA, Antwort mit Belegstellen
- [x] **Step 2** — LangGraph-Agent, Abstain als bedingte Kante, Schwellwert kalibriert (14/14) ([#2](../../issues/2))
- [x] **Persistenz** — Chroma-Index, Warmstart 12 s statt 5 min ([#1](../../issues/1))
- [x] **Index-Integrität** — Fingerprint-Validierung, Neuaufbau bei geänderter Quelle ([#7](../../issues/7))
- [x] **Web-UI + Docker** — FastAPI, SSE-Streaming, Container mit eingebackenem Embedding-Modell ([#3](../../issues/3))
- [ ] **Dokumenten-Upload** — PDF hochladen, Re-Indexierung im Hintergrund ([#4](../../issues/4))

## Evaluation

```bash
python -m evaluation.calibrate   # misst Score-Trennung, schlägt Schwellwert vor
python -m evaluation.run         # Guard-Entscheidungen (14/14) + Faithfulness
```

Das Eval-Set (`evaluation/dataset.py`) enthält 8 beantwortbare DORA-Fragen und 6 themenfremde. Der Guard trennt sie 14/14. Die Faithfulness-Harness (`deepeval`, Judge über `with_structured_output`) ist gebaut und schema-korrekt. Der Judge kann getrennt vom Antwortmodell konfiguriert werden, damit die App lokal laufen kann und nur die Faithfulness-Bewertung einen gehosteten OpenAI-kompatiblen Endpunkt nutzt:

```bash
REGRAG_JUDGE_BASE_URL=https://openrouter.ai/api/v1 \
REGRAG_JUDGE_MODELL=qwen/qwen-2.5-72b-instruct \
REGRAG_JUDGE_API_KEY=sk-or-... \
python -m evaluation.run
```

Ein rein lokaler Volllauf bleibt auf dem M4 nicht praktikabel: schema-gebundene Extraktion über dichte Rechtstexte reißt lokal die Timeouts. Details in [ADR 0005](docs/adr/0005-guard-kalibriert-abstain-als-bedingte-kante.md).

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
