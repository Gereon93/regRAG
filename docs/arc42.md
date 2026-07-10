# RegRAG — Architekturdokumentation (arc42)

Stand: Juli 2026. Gliederung nach [arc42](https://arc42.org).

Diese Dokumentation beschreibt ein **Lernprojekt**, kein Produktionssystem. Kapitel, die
für dieses System nichts Substanzielles hergeben, sind als solche markiert statt mit
Textbausteinen gefüllt.

---

## 1. Einführung und Ziele

RegRAG beantwortet Fragen zur **Verordnung (EU) 2022/2554 (DORA)** ausschließlich auf
Basis des Verordnungstextes und weist die Fundstellen aus. Findet das System keinen
belastbaren Beleg, verweigert es die Antwort, statt eine zu erfinden.

### Qualitätsziele

| # | Ziel | Warum | Wie geprüft |
|---|---|---|---|
| Q1 | **Nachvollziehbarkeit** — jede Antwort nennt ihre Fundstellen | Eine Compliance-Aussage ohne Beleg ist wertlos | Quellen + Scores werden ausgegeben |
| Q2 | **Keine erfundenen Antworten** — lieber schweigen | Eine falsche Aussage zu DORA ist teurer als keine | ⚠️ **ungeprüft**, siehe Kapitel 11 |
| Q3 | **Datenhoheit** — Dokumente verlassen den Rechner nicht | Reguliertes Umfeld; Embeddings laufen lokal | Embeddings im Prozess, LLM auf `localhost` |
| Q4 | **Nachvollziehbare Kosten und Latenz** | Grundlage für die Wahl lokal vs. gehostet | Kapitel 10, gemessene Werte |

Q2 ist das eigentliche Versprechen des Systems — und das einzige Qualitätsziel, für das
es bis heute **keinen Nachweis** gibt.

### Stakeholder

| Rolle | Erwartung |
|---|---|
| Entwickler (Gereon) | RAG, LangGraph und LangChain praktisch verstehen, nicht nur benutzen |
| Fachgespräch / Review | Nachvollziehbare Entscheidungen und ehrlich benannte Grenzen |
| Fachanwender (hypothetisch) | Schnelle, belegte Antwort auf eine DORA-Frage |

---

## 2. Randbedingungen

| Randbedingung | Konsequenz |
|---|---|
| Entwicklung auf einem Mac (Apple Silicon) | LM Studio ist eine Desktop-App, kein Serverdienst — siehe Kapitel 7 |
| Kein Budget für gehostete LLM-Inferenz im MVP | Generierung lokal, gehosteter Worker erst später |
| DORA liegt auf Deutsch vor | Embedding-Modell muss multilingual sein → `BAAI/bge-m3` |
| ISO-Normtexte sind urheberrechtlich geschützt | Werden bewusst **nicht** eingebettet. EU-Recht dagegen ist nach Beschluss 2011/833/EU frei verwendbar |
| Lernprojekt neben einer Bewerbung | Ehrlichkeit vor Vollständigkeit: keine Zahl im README, die nicht gemessen wurde |

---

## 3. Kontextabgrenzung

```mermaid
graph LR
    U[Nutzer] -->|Frage| R[RegRAG]
    R -->|Antwort + Fundstellen| U
    E[(EUR-Lex PDF<br/>CELEX 32022R2554)] -->|einmalig, manuell| R
    R <-->|OpenAI-kompatibel<br/>localhost:1234| L[LM Studio<br/>gemma-4-12b]
    R -.geplant, Issue #3.-> O[OpenRouter<br/>gehostet]
```

Fachlich: Der Nutzer stellt eine Frage in natürlicher Sprache und erhält entweder eine
belegte Antwort oder eine begründete Verweigerung.

Technisch: Das einzige verpflichtende externe System ist ein **OpenAI-kompatibler
Chat-Endpunkt**. Ob dahinter LM Studio oder OpenRouter steht, ist Konfiguration.
Das Embedding-Modell läuft **im Prozess**, nicht als Dienst.

---

## 4. Lösungsstrategie

| Qualitätsziel | Ansatz | Entscheidung |
|---|---|---|
| Q1 Nachvollziehbarkeit | Retrieval liefert Knoten mit Score und Dateiname; das Prompt-Template verpflichtet auf Quellenangabe | — |
| Q2 Keine Halluzination | Schwellwert auf dem Retrieval-Score. Reißt er, sieht das LLM den Kontext gar nicht erst | [ADR 0002](adr/0002-abstain-statt-raten.md) |
| Q1 Chunk-Qualität | PDF → Markdown, damit Chunks entlang Artikeln fallen, nicht entlang Seitenspalten | [ADR 0001](adr/0001-pdf-nach-markdown-statt-pdf-direkt.md) |
| Q4 Latenz | Vektor-Index persistieren statt bei jedem Start neu zu embedden | [ADR 0003](adr/0003-persistenter-chroma-index-mit-cosine.md) |
| Q3 Datenhoheit | Embeddings lokal, LLM über austauschbares OpenAI-Interface | [ADR 0004](adr/0004-rollenteilung-llamaindex-langchain-langgraph.md) |

---

## 5. Bausteinsicht

### Ebene 1

```mermaid
graph TD
    C[convert.py<br/>PDF → Markdown] -->|docs_md/dora.md| I
    I[rag.py<br/>Index laden oder bauen] -->|index| A[agent.py<br/>LangGraph-Agent]
    I <-->|persistent| DB[(chroma/)]
    A <-->|OpenAI-kompatibel| LLM[LM Studio]
```

| Baustein | Verantwortung | Kennt **nicht** |
|---|---|---|
| `convert.py` | PDF nach Markdown wandeln (`pymupdf4llm`) | Embeddings, LLM |
| `rag.py` | Embedding-Modell, Index bauen/laden, Persistenz | Den Ablauf, das LLM |
| `agent.py` | Ablaufsteuerung, Schwellwert, Prompt, LLM-Aufruf | Wie retrievt wird |

Die Trennung ist der Punkt: `rag.py` weiß nichts vom LLM, `agent.py` nichts vom Vektor-Store.
Ein Austausch von Chroma oder LM Studio berührt jeweils genau eine Datei.

### Ebene 2 — `agent.py`

| Element | Aufgabe |
|---|---|
| `retrieve` (Knoten) | Die drei ähnlichsten Chunks holen |
| `answer` (Knoten) | Beleglage prüfen, dann formulieren **oder** verweigern |
| `beleglage_zu_schwach()` | Der Schwellwert-Test, siehe ADR 0002 |
| `StateGraph` | Verbindet die Knoten, trägt den Zustand `S` |

⚠️ `answer` tut heute **zwei Dinge**: prüfen und formulieren. Siehe Kapitel 11.

---

## 6. Laufzeitsicht

### Anfrage mit ausreichender Beleglage

```mermaid
sequenceDiagram
    participant N as Nutzer
    participant G as LangGraph
    participant R as LlamaIndex-Retriever
    participant D as Chroma
    participant L as LM Studio

    N->>G: Frage
    G->>R: retrieve(frage)
    R->>D: Ähnlichkeitssuche (Cosine, top_k=3)
    D-->>R: 3 Chunks + Scores
    R-->>G: nodes
    G->>G: beleglage_zu_schwach(nodes)?
    G->>L: Prompt mit Kontext
    L-->>G: Antwort mit Quellenangabe
    G-->>N: Antwort + Fundstellen
```

### Anfrage ohne Beleg

Der Pfad endet vor dem LLM. `beleglage_zu_schwach()` liefert `True`, `answer` setzt
`ABSTAIN_ANTWORT` und kehrt zurück. **Das LLM wird nie aufgerufen** — es kann nicht
halluzinieren, was es nicht sieht. Genau darin liegt der Wert, und genau dieser Pfad
ist bislang nie ausgelöst worden.

### Indexaufbau (einmalig)

`convert.py` → Markdown → Chunks → bge-m3 → Chroma. Dauer: **5:04 min** für 351k Zeichen.
Danach lädt jeder Start den Index in **12.2 s**, wovon der Löwenanteil das Laden des
Embedding-Modells in den Speicher ist.

---

## 7. Verteilungssicht

### Heute

Alles auf einem Rechner: ein Python-Prozess (Embeddings im Prozess, Chroma auf Platte)
spricht über `localhost:1234` mit LM Studio.

### Geplant (Issue #3) — und die Falle darin

```mermaid
graph TD
    subgraph Container
        API[FastAPI + Index]
    end
    subgraph Host
        LMS[LM Studio]
    end
    API -.host.docker.internal.-> LMS
    API -->|Alternative| OR[OpenRouter]
```

**LM Studio ist eine Mac-App und kann nicht in den Container.** Der Container erreicht sie
nur über `host.docker.internal` — was auf einem Linux-Server oder in der Cloud nicht
funktioniert.

Damit ist die Dockerisierung der Moment, in dem der gehostete Worker (OpenRouter) vom
Bonus zur Notwendigkeit wird. Die Backend-URL muss konfigurierbar sein, nicht hartkodiert.
Zweitens: `bge-m3` wiegt ~2 GB und muss ins Image oder in ein Volume — bewusst zu entscheiden.

---

## 8. Querschnittliche Konzepte

**Entscheidungen gehören in ADRs, nicht in Kommentare.** Der Code trägt sprechende Namen
(`beleglage_zu_schwach`, `MIN_RETRIEVAL_SCORE`, `ABSTAIN_ANTWORT`) und verweist per
`# docs/adr/0002` auf die Begründung. Erklärende Kommentare sind projektweit unerwünscht:
Was der Code tut, sagt der Code; warum, sagt die ADR.

**Ehrlichkeit als Architekturprinzip.** Keine Zahl in README, ADR oder Bewerbung, die nicht
gemessen wurde. Nicht Gemessenes wird als „offen" markiert, nicht weggelassen.
Ein Score ist nur innerhalb seiner Distanzmetrik interpretierbar (ADR 0003).

**Austauschbarkeit über OpenAI-kompatible Schnittstellen.** LM Studio und OpenRouter
sprechen dasselbe Protokoll; der Wechsel ist Konfiguration, keine Codeänderung.

---

## 9. Architekturentscheidungen

| ADR | Entscheidung | Status |
|---|---|---|
| [0001](adr/0001-pdf-nach-markdown-statt-pdf-direkt.md) | PDF nach Markdown statt PDF direkt | akzeptiert; Nutzen **nicht gemessen** |
| [0002](adr/0002-abstain-statt-raten.md) | Verweigern statt raten | akzeptiert; Schwellwert **unkalibriert** |
| [0003](adr/0003-persistenter-chroma-index-mit-cosine.md) | Persistenter Chroma-Index, Cosine erzwungen | akzeptiert |
| [0004](adr/0004-rollenteilung-llamaindex-langchain-langgraph.md) | Rollenteilung der drei Frameworks | akzeptiert; LangGraph **unterfordert** |

---

## 10. Qualitätsanforderungen

### Gemessen

| Größe | Wert |
|---|---|
| Kaltstart (Index bauen, 351k Zeichen) | 5:04 min |
| Warmstart (Index laden) | 12.2 s |
| Retrieval, `similarity_top_k=3` | 0.5 s |
| Score eines guten Treffers (Cosine) | 0.72–0.73 |
| Kosten pro Anfrage (lokal) | 0 € |

### Szenarien

| Szenario | Erwartung | Status |
|---|---|---|
| Frage, die DORA beantwortet | Antwort mit Fundstellen | ✅ beobachtet |
| Frage außerhalb von DORA | `ABSTAIN_ANTWORT`, kein LLM-Aufruf | ❌ **nie getestet** |
| LM Studio nicht erreichbar | Verständliche Fehlermeldung, kein Absturz | ⚠️ `except Exception` fängt alles |
| Vektor-Store gewechselt | Schwellwert wird neu kalibriert | ✅ als Regel etabliert (ADR 0003) |

### Nicht gemessen

Faithfulness. Trefferqualität gegenüber naivem PDF-Parsing. Ob `MIN_RETRIEVAL_SCORE = 0.4`
richtig liegt. Latenz und Kosten eines gehosteten Backends. Alles Gegenstand von Issue #2 und #3.

---

## 11. Risiken und technische Schulden

| # | Schuld | Wirkung | Wo |
|---|---|---|---|
| 1 | **Abstain-Pfad nie ausgelöst** | Das zentrale Qualitätsversprechen (Q2) ist unbewiesen | ADR 0002, Issue #2 |
| 2 | **`MIN_RETRIEVAL_SCORE = 0.4` ist geraten** | Aus dem Bauplan übernommen, keine Messung. Gute Treffer liegen bei 0.72 — die Schwelle könnte zu tief hängen und Mittelmaß durchlassen | ADR 0002 |
| 3 | **LangGraph ist unterfordert** | Der Graph ist eine gerade Linie; die Verzweigung steckt als `if` im `answer`-Knoten. Ein bedingter Kanten-Übergang würde die Verweigerung sichtbar machen | ADR 0004 |
| 4 | **Keine automatisierten Tests** | Jede Änderung wird von Hand geprüft. `deepeval` steht in `requirements.txt`, wird von keiner Datei importiert | Issue #2 |
| 5 | **`except Exception` in `answer`** | Verschluckt jeden Fehler, auch Programmierfehler, und meldet dem Nutzer „bitte erneut versuchen" | `agent.py` |
| 6 | **Modellname hartkodiert** | `LOKALES_CHAT_MODELL` ist eine Konstante; für Docker muss sie aus der Umgebung kommen | Issue #3 |
| 7 | **Chunking ungetunt** | LlamaIndex-Defaults. Für einen Gesetzestext mit Artikelstruktur vermutlich nicht optimal | offen |

Schuld 1 bis 3 sind die interessanten. Sie betreffen nicht die Ausführung, sondern die
**Aussagekraft** des Systems — es tut, was es soll, aber niemand hat es bewiesen.

---

## 12. Glossar

| Begriff | Bedeutung |
|---|---|
| **DORA** | Digital Operational Resilience Act, Verordnung (EU) 2022/2554. Seit Januar 2025 in Kraft, löst u. a. die BAIT ab |
| **RAG** | Retrieval-Augmented Generation. Erst passende Textstellen suchen, dann das LLM nur auf deren Basis antworten lassen |
| **Chunk** | Ein Textabschnitt, in den ein Dokument zerlegt wird, um einzeln durchsuchbar zu sein |
| **Embedding** | Ein Vektor, der die Bedeutung eines Chunks repräsentiert. Ähnliche Bedeutung → nahe Vektoren |
| **Cosine / L2** | Zwei Arten, „Nähe" zwischen Vektoren zu messen. Ihre Zahlen sind **nicht** ineinander übersetzbar (ADR 0003) |
| **HNSW** | Der Suchindex in Chroma. Findet Nachbarn schnell, aber **näherungsweise** statt exakt |
| **Abstain** | Die bewusste Verweigerung einer Antwort bei zu dünner Beleglage |
| **Faithfulness** | Metrik: Steht die Antwort tatsächlich im gelieferten Kontext, oder wurde sie erfunden? |
