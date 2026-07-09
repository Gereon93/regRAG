# 0003 — Persistenter Chroma-Index, erzwungen auf Cosine

Status: akzeptiert

## Kontext

Jeder Prozessstart embeddete die ~351k Zeichen aus `docs_md/dora.md` neu durch
`BAAI/bge-m3`. Gemessen: **5:04 min** bis zur ersten Antwort.

Das blockierte alles Weitere. Ein Eval-Set mit zehn Fragen (Issue #2) hätte
zehnmal diesen Preis gekostet, ein Webserver (Issue #3) wäre nach jedem Start
minutenlang tot gewesen.

## Entscheidung

`rag.py` legt den Index in einem persistenten `chromadb.PersistentClient`
(`chroma/`) ab. Beim Start wird geladen statt gebaut, sobald die Collection
Einträge hat. `REGRAG_INDEX_NEU_BAUEN=1` erzwingt den Neuaufbau.

Die Collection wird explizit mit `{"hnsw:space": "cosine"}` erzeugt.

## Warum Cosine explizit

Beim Umstieg fielen die Retrieval-Scores derselben Frage auf demselben Text von
**0.67–0.69 auf 0.51–0.53**. Ursache war nicht die Persistenz, sondern die
Metrik: LlamaIndex' In-Memory-Store rechnet Cosine-Ähnlichkeit, Chroma verwendet
per Default **L2**.

Der Wechsel hätte den Sicherheitsabstand von `MIN_RETRIEVAL_SCORE = 0.4`
([0002](0002-abstain-statt-raten.md)) stillschweigend von 0.29 auf 0.11
zusammengedrückt — ohne dass eine Zeile am Schwellwert geändert worden wäre.
Ein Score ist nur innerhalb einer Metrik interpretierbar.

## Konsequenzen

- Warmstart: **12.2 s** (davon der Löwenanteil das Laden von bge-m3 in den
  Speicher), Retrieval **0.5 s**. Vorher 5:04 min.
- `chroma/` ist ein Generat und liegt nicht im Repo.
- Ein Wechsel des Vector Stores muss künftig immer die Metrik mitprüfen.

## Offen

Die Scores nach dem Wechsel sind **0.72–0.73**, nicht exakt die 0.67–0.69 des
In-Memory-Laufs. Gleiche Metrik, gleiche Größenordnung, aber nicht identisch —
vermutlich approximative HNSW-Suche gegen exakte Brute-Force-Suche, verifiziert
ist das nicht. Konsequenz: Schwellwerte gelten immer nur für den Store, gegen den
sie kalibriert wurden.
