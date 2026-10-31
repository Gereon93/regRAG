# Graph Report - regRAG  (2026-10-01)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 196 nodes · 318 edges · 11 communities (9 shown, 2 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.85)
- Token cost: 78,761 input · 161 output

## Graph Freshness
- Built from commit: `68e136a4`
- Run `git diff 68e136a4 --stat -- ':!graphify-out' ':!.graphify*' ':!CLAUDE.md'` to check if indexed source files have changed since the build.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Upload Validation & Indexing
- Agent Answer Graph
- Web Delete Tests
- Document Index & Fingerprint
- FastAPI Endpoints
- Embedding Calibration & Latency
- PDF to Markdown Conversion
- Config Loading Tests
- Node Validator Tests
- Docker Entrypoint

## God Nodes (most connected - your core abstractions)
1. `indexiere()` - 10 edges
2. `lade_oder_baue_index()` - 10 edges
3. `diff()` - 9 edges
4. `JudgeLLM` - 8 edges
5. `upload()` - 8 edges
6. `loesche_dokument()` - 8 edges
7. `UploadFehler` - 7 edges
8. `pruefe_pdf()` - 7 edges
9. `saeubere_dateiname()` - 7 edges
10. `saeubere_md_name()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `lade_oder_baue_index()` --calls--> `diff()`  [EXTRACTED]
  rag.py → dokumente.py
- `test_diff_erkennt_geaenderte_datei()` --calls--> `diff()`  [EXTRACTED]
  tests/test_dokumente.py → dokumente.py
- `test_diff_erkennt_geloeschte_datei()` --calls--> `diff()`  [EXTRACTED]
  tests/test_dokumente.py → dokumente.py
- `test_diff_erkennt_neue_datei()` --calls--> `diff()`  [EXTRACTED]
  tests/test_dokumente.py → dokumente.py
- `test_leerer_fingerprint_indexiert_alles()` --calls--> `diff()`  [EXTRACTED]
  tests/test_dokumente.py → dokumente.py

## Import Cycles
- None detected.

## Communities (11 total, 2 thin omitted)

### Community 0 - "Upload Validation & Indexing"
Cohesion: 0.08
Nodes (36): diff(), ohne_dokument(), pruefe_groesse(), pruefe_pdf(), Fingerprint ohne den Eintrag für `name` — der Rest bleibt unangetastet., (zu_indexieren, zu_loeschen, voll_rebuild) — eine geänderte Datei zählt in…, saeubere_dateiname(), saeubere_md_name() (+28 more)

### Community 1 - "Agent Answer Graph"
Cohesion: 0.10
Nodes (19): abstain(), answer(), _baue_graph(), beleglage_zu_schwach(), naechster_schritt(), retrieve(), S, deepeval_metrics (+11 more)

### Community 2 - "Web Delete Tests"
Cohesion: 0.10
Nodes (22): fastapi_testclient, sys, client(), dokumente_verzeichnis(), _fake_loesche_dokument(), _lege_dokument_an(), main(), fixture (+14 more)

### Community 3 - "Document Index & Fingerprint"
Cohesion: 0.16
Nodes (22): chromadb, contextlib, datei_hash(), fingerprint(), leerer_fingerprint(), Upload-Validierung und Fingerprint-Logik — ohne schwere Imports, damit die CI…, hashlib, llama_index_core (+14 more)

### Community 4 - "FastAPI Endpoints"
Cohesion: 0.12
Nodes (21): BaseModel, concurrent_futures, delete, fastapi, fastapi_responses, get, post, pydantic (+13 more)

### Community 5 - "Embedding Calibration & Latency"
Cohesion: 0.14
Nodes (12): argparse, create_embed_model(), main(), messen(), nearest_rank_p95(), itertools, math, platform (+4 more)

### Community 6 - "PDF to Markdown Conversion"
Cohesion: 0.16
Nodes (12): dokument_titel(), pdf_nach_markdown(), Konvertiert eine PDF nach <ausgabe>/<stamm>.md samt Quellen-Sidecar, gibt den…, pdf_nach_text(), Baseline für ADR 0001: extrahiert dieselbe PDF mit pypdf statt pymupdf4llm.…, Seitenweiser Text, wie ihn ein naiver PDF-Reader liefert — ohne…, json, pathlib (+4 more)

### Community 7 - "Config Loading Tests"
Cohesion: 0.25
Nodes (8): importlib, os, pytest, geladen(), fixture, test_gesetzte_judge_variablen_gewinnen(), test_judge_erbt_app_endpunkt_ohne_eigene_werte(), test_leere_judge_variablen_fallen_auf_app_endpunkt_zurueck()

### Community 8 - "Node Validator Tests"
Cohesion: 0.20
Nodes (9): ref_node_assert, ref_node_child_process, ref_node_path, ref_node_test, assert, path, { spawnSync }, test (+1 more)

## Knowledge Gaps
- **6 isolated node(s):** `assert`, `path`, `{ spawnSync }`, `test`, `validator` (+1 more)
  These have ≤1 connection - possible missing edges. (Counts symbols only; 76 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `diff()` connect `Upload Validation & Indexing` to `Document Index & Fingerprint`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Why does `upload()` connect `Upload Validation & Indexing` to `Document Index & Fingerprint`, `FastAPI Endpoints`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **What connects `assert`, `path`, `{ spawnSync }` to the rest of the system?**
  _6 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Upload Validation & Indexing` be split into smaller, more focused modules?**
  _Cohesion score 0.08108108108108109 - nodes in this community are weakly interconnected._
- **Should `Agent Answer Graph` be split into smaller, more focused modules?**
  _Cohesion score 0.09523809523809523 - nodes in this community are weakly interconnected._
- **Should `Web Delete Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.09971509971509972 - nodes in this community are weakly interconnected._
- **Should `FastAPI Endpoints` be split into smaller, more focused modules?**
  _Cohesion score 0.12121212121212122 - nodes in this community are weakly interconnected._