# Graph Report - regRAG  (2026-10-01)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 212 nodes · 332 edges · 13 communities (11 shown, 2 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.85)
- Token cost: 78,862 input · 171 output

## Graph Freshness
- Built from commit: `68e136a4`
- Run `git diff 68e136a4 --stat -- ':!graphify-out' ':!.graphify*' ':!CLAUDE.md'` to check if indexed source files have changed since the build.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Upload Validation & Fingerprint
- Agent Answer Graph
- Web API Endpoints
- Web Delete Tests
- Index & Vector Store
- Embedding & Calibration
- PDF to Markdown Conversion
- Config Loading Tests
- A11y Package Scripts
- Validator Test Harness
- HTML Validate Rules
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

## Communities (13 total, 2 thin omitted)

### Community 0 - "Upload Validation & Fingerprint"
Cohesion: 0.09
Nodes (35): diff(), ohne_dokument(), pruefe_groesse(), pruefe_pdf(), Upload-Validierung und Fingerprint-Logik — ohne schwere Imports, damit die CI…, Fingerprint ohne den Eintrag für `name` — der Rest bleibt unangetastet., (zu_indexieren, zu_loeschen, voll_rebuild) — eine geänderte Datei zählt in…, saeubere_dateiname() (+27 more)

### Community 1 - "Agent Answer Graph"
Cohesion: 0.10
Nodes (19): abstain(), answer(), _baue_graph(), beleglage_zu_schwach(), naechster_schritt(), retrieve(), S, deepeval_metrics (+11 more)

### Community 2 - "Web API Endpoints"
Cohesion: 0.10
Nodes (27): BaseModel, concurrent_futures, contextlib, delete, fastapi, fastapi_responses, File, get (+19 more)

### Community 3 - "Web Delete Tests"
Cohesion: 0.10
Nodes (22): fastapi_testclient, sys, client(), dokumente_verzeichnis(), _fake_loesche_dokument(), _lege_dokument_an(), main(), fixture (+14 more)

### Community 4 - "Index & Vector Store"
Cohesion: 0.20
Nodes (18): chromadb, datei_hash(), fingerprint(), leerer_fingerprint(), llama_index_core, llama_index_llms_openai_like, llama_index_vector_stores_chroma, _embed_model() (+10 more)

### Community 5 - "Embedding & Calibration"
Cohesion: 0.15
Nodes (11): argparse, create_embed_model(), main(), messen(), nearest_rank_p95(), itertools, math, platform (+3 more)

### Community 6 - "PDF to Markdown Conversion"
Cohesion: 0.16
Nodes (12): dokument_titel(), pdf_nach_markdown(), Konvertiert eine PDF nach <ausgabe>/<stamm>.md samt Quellen-Sidecar, gibt den…, pdf_nach_text(), Baseline für ADR 0001: extrahiert dieselbe PDF mit pypdf statt pymupdf4llm.…, Seitenweiser Text, wie ihn ein naiver PDF-Reader liefert — ohne…, json, pathlib (+4 more)

### Community 7 - "Config Loading Tests"
Cohesion: 0.25
Nodes (8): importlib, os, pytest, geladen(), fixture, test_gesetzte_judge_variablen_gewinnen(), test_judge_erbt_app_endpunkt_ohne_eigene_werte(), test_leere_judge_variablen_fallen_auf_app_endpunkt_zurueck()

### Community 8 - "A11y Package Scripts"
Cohesion: 0.20
Nodes (9): devDependencies, html-validate, engines, node, private, scripts, check:a11y, test:a11y (+1 more)

### Community 9 - "Validator Test Harness"
Cohesion: 0.20
Nodes (9): ref_node_assert, ref_node_child_process, ref_node_path, ref_node_test, assert, path, { spawnSync }, test (+1 more)

### Community 10 - "HTML Validate Rules"
Cohesion: 0.33
Nodes (5): extends, rules, input-missing-label, wcag/h37, html-validate:standard

## Knowledge Gaps
- **15 isolated node(s):** `input-missing-label`, `wcag/h37`, `html-validate:standard`, `entrypoint.sh script`, `html-validate` (+10 more)
  These have ≤1 connection - possible missing edges. (Counts symbols only; 85 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `diff()` connect `Upload Validation & Fingerprint` to `Index & Vector Store`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Why does `upload()` connect `Web API Endpoints` to `Upload Validation & Fingerprint`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **What connects `input-missing-label`, `wcag/h37`, `html-validate:standard` to the rest of the system?**
  _15 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Upload Validation & Fingerprint` be split into smaller, more focused modules?**
  _Cohesion score 0.08819345661450925 - nodes in this community are weakly interconnected._
- **Should `Agent Answer Graph` be split into smaller, more focused modules?**
  _Cohesion score 0.09523809523809523 - nodes in this community are weakly interconnected._
- **Should `Web API Endpoints` be split into smaller, more focused modules?**
  _Cohesion score 0.09788359788359788 - nodes in this community are weakly interconnected._
- **Should `Web Delete Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.09971509971509972 - nodes in this community are weakly interconnected._