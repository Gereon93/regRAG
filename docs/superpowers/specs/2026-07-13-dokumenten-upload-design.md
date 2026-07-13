# Dokumenten-Upload über die Web-UI mit inkrementeller Re-Indexierung

Issue: [#4](https://github.com/Gereon93/regrag/issues/4) · Datum: 2026-07-13

## Ziel

Über die Web-UI ein PDF hochladen (weitere Regulatorik, z. B. MaRisk, EBA ICT-Guidelines).
Das Dokument wird im Hintergrund indexiert und danach mitdurchsucht. Quellenangaben nennen
das Dokument. Der Zustand überlebt `docker compose restart`.

## Akzeptanz

PDF hochladen → Job-Status wird `ready` → eine Frage, die nur dieses Dokument beantwortet,
wird korrekt und mit Quellenangabe beantwortet → nach `docker compose restart` gilt beides
weiterhin, ohne Neu-Indexierung.

## Ausgangslage

- `rag.py` baut den Index beim Import (`index = lade_oder_baue_index()`), `agent.py` und
  `web/main.py` hängen daran.
- `_fingerprint` hasht **alle** Markdown-Dateien in einen einzigen Hash. Jede Korpus-Änderung
  löscht die Collection und baut alles neu (`_baue`).
- `docs_md/` liegt **nicht** auf einem Volume; nur `chroma:` ist persistent.
- `convert.py` löscht beim Lauf alle `*.md` in `docs_md/`.
- `_quelle_metadata` setzt bereits `quelle` aus `<stamm>.source.json`; `web/main.py` gibt sie aus.
- CI installiert nur `ruff` und `pytest`. Tests dürfen `llama_index`, `chromadb` oder `fastapi`
  nicht importieren.

## Entscheidungen

| Entscheidung | Gewählt | Warum |
|---|---|---|
| Index-Aktualisierung | Per-Datei-Fingerprint, inkrementeller Merge | Voll-Rebuild wächst linear mit dem Korpus (~5 min/Dokument) |
| Job-Status | In-Memory-Dict, Dokumentliste von Platte | Ein laufender Job ist nach Neustart ohnehin tot; die Akzeptanz braucht nur die Dokumentliste |
| Hochgeladene PDF | Nach Konvertierung verworfen | Markdown + `.source.json` reichen für Retrieval und Quellenangabe |
| Testbarkeit | Reine Logik in stdlib-only-Modul | CI hat keine schweren Dependencies |

## Architektur

### `dokumente.py` (neu, nur stdlib)

Die gesamte prüfbare Logik, ohne schwere Imports:

- `saeubere_dateiname(name) -> str` — nimmt den Basename, ersetzt alles außerhalb
  `[A-Za-z0-9._-]` durch `_`, erzwingt die Endung `.pdf`. Damit ist Path-Traversal
  (`../../etc/passwd`) ausgeschlossen.
- `pruefe_pdf(daten: bytes) -> None` — wirft bei fehlender Magic `%PDF-` oder bei Überschreiten
  des Größenlimits (`REGRAG_UPLOAD_MAX_MB`, Default 25).
- `fingerprint(md_dateien) -> dict` — `{"embedding_modell", "metrik", "dokumente": {name: sha256}}`.
  Der Hash je Datei deckt Markdown und Sidecar ab.
- `diff(alt, neu) -> (zu_indexieren, zu_loeschen, voll_rebuild)` — `voll_rebuild` nur, wenn sich
  Embedding-Modell oder Distanzmetrik geändert haben; sonst Mengen-Differenz über die Dateinamen,
  geänderte Datei = löschen + neu indexieren.

### `rag.py`

- `lade_oder_baue_index()` lädt die Collection, bildet den Fingerprint-Diff und fährt ihn ab:
  entfernte oder geänderte Dateien per `collection.delete(where={"file_name": ...})` raus,
  neue über `indexiere()` rein. Voll-Rebuild nur bei `REGRAG_INDEX_NEU_BAUEN=1` oder Modellwechsel.
- `indexiere(md_pfad)` (neu, öffentlich) — liest Markdown plus Sidecar, `index.insert(dokument)`
  in die bestehende Collection, schreibt den Fingerprint fort. Upload und Startup benutzen
  denselben Pfad; es gibt nur eine Merge-Implementierung.
- Der Retriever in `agent.py` liest den Vector Store live, neue Nodes sind sofort sichtbar.

### `convert.py`

- `pdf_nach_markdown(pdf_pfad, titel_fallback) -> Path` wird herausgezogen und schreibt
  `<stamm>.md` und `<stamm>.source.json` nach `docs_md/`.
- Der Script-Teil (DORA) ruft die Funktion auf.
- Das Löschen aller `*.md` entfällt — es würde hochgeladene Dokumente beim Start vernichten.

### `web/main.py`

- `POST /upload` (multipart) — validiert über `dokumente.py`, `409` bei Namenskollision in
  `docs_md/`, sonst `202` mit Job-ID. Die PDF liegt nur in einem `tempfile` und wird nach der
  Konvertierung gelöscht.
- Die Indexierung läuft in einem `ThreadPoolExecutor(max_workers=1)`: sie ist CPU-gebunden und
  darf weder den Event-Loop blockieren noch bei parallelen Uploads die CPU zerreißen.
- `GET /jobs/{id}` → `{"status": "pending"|"indexing"|"ready"|"failed", "dateiname", "fehler"}`.
  In-Memory-Dict, `404` bei unbekannter ID.
- `GET /documents` → Liste aus `docs_md/*.source.json` (`{"datei", "titel"}`). Von Platte gelesen,
  überlebt damit den Neustart.
- `ABSTAIN_ANTWORT` wird zu `"Nicht eindeutig in den indexierten Dokumenten belegt."` — mit
  mehreren Korpora ist „in DORA" falsch. `evaluation/run.py` vergleicht gegen die Konstante und
  bleibt grün.

### UI (`web/static/index.html`)

Upload-Feld, Statuszeile mit Polling alle 2 s auf `GET /jobs/{id}`, Liste der indexierten
Dokumente aus `GET /documents`, sowie der Hinweis, keine urheberrechtlich geschützten Normtexte
(ISO, DIN) hochzuladen.

### Docker

Zweites Volume `docs_md:/app/docs_md`. Zusammen mit dem bestehenden `chroma:`-Volume ist damit
sowohl der Korpus als auch der Index persistent — das ist die Persistenz-Akzeptanz.
`entrypoint.sh` konvertiert die DORA-PDF weiterhin nur, wenn noch kein Markdown vorliegt.

## Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Keine PDF / falsche Magic | `400`, Upload abgelehnt |
| Größer als Limit | `413` |
| Dateiname existiert bereits | `409` |
| Konvertierung oder Embedding schlägt fehl | Job wird `failed`, Fehlertext im Status, Markdown-Fragmente werden entfernt |
| Prozess stirbt während der Indexierung | Beim Start fällt die Datei über den Fingerprint-Diff auf und wird nachindexiert |

## Tests

`tests/test_dokumente.py` (stdlib-only, läuft in der bestehenden CI):

- `saeubere_dateiname` gegen `../../etc/passwd`, absolute Pfade, Leerzeichen, fehlende Endung
- `pruefe_pdf` gegen Nicht-PDF-Bytes und Übergröße
- `fingerprint` erkennt geänderte Sidecars
- `diff`: neue Datei, geänderte Datei, gelöschte Datei, Modellwechsel → `voll_rebuild`

Die Akzeptanz (Upload → `ready` → Antwort mit Quelle → `docker compose restart`) wird manuell
gegen den laufenden Container geprüft und im PR belegt.

## Nicht enthalten (YAGNI)

- Löschen von Dokumenten über die UI
- Job-Status über Neustart hinweg
- Mehrere gleichzeitige Indexierungen
- Authentifizierung
