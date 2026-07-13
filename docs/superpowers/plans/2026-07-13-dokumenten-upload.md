# Dokumenten-Upload mit inkrementeller Re-Indexierung — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF über die Web-UI hochladen, im Hintergrund inkrementell in den bestehenden Chroma-Index mergen, Status abfragbar, Zustand überlebt `docker compose restart`.

**Architecture:** Reine Logik (Dateinamen, Validierung, Fingerprint-Diff) liegt in `dokumente.py` ohne schwere Imports und ist damit CI-testbar. `rag.py` bekommt `indexiere(md_pfad)` — ein Merge-Pfad, den sowohl der Startup-Abgleich als auch der Upload benutzen. `web/main.py` nimmt den Upload an, gibt eine Job-ID zurück und indexiert in einem `ThreadPoolExecutor(max_workers=1)`.

**Tech Stack:** FastAPI, LlamaIndex, ChromaDB, pymupdf4llm, pytest, Docker Compose.

Spec: `docs/superpowers/specs/2026-07-13-dokumenten-upload-design.md`

## Global Constraints

- Sprache im Code: deutsche Bezeichner, wie im Bestand (`saeubere_dateiname`, `zu_indexieren`).
- `tests/` darf **nicht** `llama_index`, `chromadb`, `fastapi` oder `pymupdf` importieren — CI installiert nur `ruff` und `pytest`.
- Kein Voll-Rebuild bei Korpus-Änderung; nur bei Wechsel von Embedding-Modell oder Distanzmetrik bzw. `REGRAG_INDEX_NEU_BAUEN=1`.
- Distanzmetrik bleibt `cosine` (ADR 0003).
- Upload-Limit: `REGRAG_UPLOAD_MAX_MB`, Default `25`.
- Ruff muss grün bleiben: `ruff check .`

---

### Task 1: `dokumente.py` — Validierung und Fingerprint-Diff

**Files:**
- Create: `dokumente.py`
- Test: `tests/test_dokumente.py`

**Interfaces:**
- Consumes: nichts.
- Produces:
  - `class UploadFehler(ValueError)` mit Attribut `status: int`
  - `saeubere_dateiname(name: str) -> str`
  - `pruefe_pdf(daten: bytes) -> None`
  - `datei_hash(md_pfad) -> str`
  - `leerer_fingerprint(embedding_modell: str, metrik: str) -> dict`
  - `fingerprint(md_dateien, embedding_modell: str, metrik: str) -> dict`
  - `diff(alt: dict, neu: dict) -> tuple[list[str], list[str], bool]` → `(zu_indexieren, zu_loeschen, voll_rebuild)`

- [ ] **Step 1: Testdatei schreiben**

```python
# tests/test_dokumente.py
import pytest

import dokumente


def test_traversal_wird_auf_basename_reduziert():
    assert dokumente.saeubere_dateiname("../../etc/passwd.pdf") == "passwd.pdf"
    assert dokumente.saeubere_dateiname("/abs/pfad/marisk.pdf") == "marisk.pdf"
    assert dokumente.saeubere_dateiname("C:\\temp\\eba.pdf") == "eba.pdf"


def test_sonderzeichen_werden_ersetzt():
    assert dokumente.saeubere_dateiname("EBA ICT-Guidelines (2019).pdf") == "EBA_ICT-Guidelines__2019_.pdf"


def test_nicht_pdf_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.saeubere_dateiname("schaden.exe")
    assert e.value.status == 400


def test_leerer_name_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler):
        dokumente.saeubere_dateiname(".pdf")


def test_pruefe_pdf_akzeptiert_magic():
    dokumente.pruefe_pdf(b"%PDF-1.7\n...")


def test_pruefe_pdf_lehnt_fremde_bytes_ab():
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.pruefe_pdf(b"PK\x03\x04zip")
    assert e.value.status == 400


def test_pruefe_pdf_lehnt_uebergroesse_ab(monkeypatch):
    monkeypatch.setattr(dokumente, "MAX_MB", 0.001)
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.pruefe_pdf(b"%PDF-" + b"x" * 2000)
    assert e.value.status == 413


def _schreibe(tmp_path, name, md, quelle=None):
    pfad = tmp_path / name
    pfad.write_text(md, encoding="utf-8")
    if quelle is not None:
        pfad.with_suffix(".source.json").write_text(quelle, encoding="utf-8")
    return pfad


def test_fingerprint_erfasst_sidecar(tmp_path):
    pfad = _schreibe(tmp_path, "a.md", "text", '{"titel": "A"}')
    vorher = dokumente.fingerprint([pfad], "bge-m3", "cosine")

    pfad.with_suffix(".source.json").write_text('{"titel": "B"}', encoding="utf-8")
    nachher = dokumente.fingerprint([pfad], "bge-m3", "cosine")

    assert vorher["dokumente"]["a.md"] != nachher["dokumente"]["a.md"]


def test_diff_erkennt_neue_datei():
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1", "b.md": "2"}}

    assert dokumente.diff(alt, neu) == (["b.md"], [], False)


def test_diff_erkennt_geaenderte_datei():
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "2"}}

    assert dokumente.diff(alt, neu) == (["a.md"], ["a.md"], False)


def test_diff_erkennt_geloeschte_datei():
    alt = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1", "b.md": "2"}}
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    assert dokumente.diff(alt, neu) == ([], ["b.md"], False)


def test_modellwechsel_erzwingt_voll_rebuild():
    alt = {"embedding_modell": "alt", "metrik": "cosine", "dokumente": {"a.md": "1"}}
    neu = {"embedding_modell": "neu", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    zu_indexieren, zu_loeschen, voll = dokumente.diff(alt, neu)
    assert voll and zu_indexieren == ["a.md"] and zu_loeschen == []


def test_leerer_fingerprint_indexiert_alles():
    neu = {"embedding_modell": "m", "metrik": "cosine", "dokumente": {"a.md": "1"}}

    assert dokumente.diff({}, neu) == (["a.md"], [], True)
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `python -m pytest tests/test_dokumente.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'dokumente'`

- [ ] **Step 3: `dokumente.py` schreiben**

```python
"""Reine Upload- und Fingerprint-Logik — bewusst ohne schwere Imports, damit die CI sie testen kann."""

import hashlib
import os
import re
from pathlib import Path

MAX_MB = float(os.getenv("REGRAG_UPLOAD_MAX_MB", "25"))
PDF_MAGIC = b"%PDF-"
_UNERLAUBT = re.compile(r"[^A-Za-z0-9._-]")


class UploadFehler(ValueError):
    def __init__(self, meldung, status):
        super().__init__(meldung)
        self.status = status


def saeubere_dateiname(name):
    roh = Path(str(name).replace("\\", "/")).name
    sauber = _UNERLAUBT.sub("_", roh).lstrip(".")
    if not sauber.lower().endswith(".pdf"):
        raise UploadFehler("Nur PDF-Dateien werden angenommen.", 400)
    if len(sauber) <= len(".pdf"):
        raise UploadFehler("Dateiname fehlt.", 400)
    return sauber


def pruefe_pdf(daten):
    if len(daten) > MAX_MB * 1024 * 1024:
        raise UploadFehler(f"Datei ist größer als {MAX_MB:g} MB.", 413)
    if not daten.startswith(PDF_MAGIC):
        raise UploadFehler("Datei ist kein PDF.", 400)


def datei_hash(md_pfad):
    md_pfad = Path(md_pfad)
    h = hashlib.sha256()
    h.update(md_pfad.read_bytes())
    sidecar = md_pfad.with_suffix(".source.json")
    if sidecar.exists():
        h.update(sidecar.read_bytes())
    return h.hexdigest()


def leerer_fingerprint(embedding_modell, metrik):
    return {"embedding_modell": embedding_modell, "metrik": metrik, "dokumente": {}}


def fingerprint(md_dateien, embedding_modell, metrik):
    fp = leerer_fingerprint(embedding_modell, metrik)
    fp["dokumente"] = {Path(p).name: datei_hash(p) for p in md_dateien}
    return fp


def diff(alt, neu):
    """(zu_indexieren, zu_loeschen, voll_rebuild) — geänderte Datei zählt beides."""
    alt = alt or {}
    neue_docs = neu["dokumente"]

    voll_rebuild = (
        alt.get("embedding_modell") != neu["embedding_modell"]
        or alt.get("metrik") != neu["metrik"]
    )
    if voll_rebuild:
        return sorted(neue_docs), [], True

    alte_docs = alt.get("dokumente", {})
    zu_indexieren = sorted(n for n, h in neue_docs.items() if alte_docs.get(n) != h)
    zu_loeschen = sorted(n for n, h in alte_docs.items() if neue_docs.get(n) != h)
    return zu_indexieren, zu_loeschen, False
```

- [ ] **Step 4: Tests laufen lassen**

Run: `python -m pytest tests/test_dokumente.py -q && ruff check dokumente.py tests/test_dokumente.py`
Expected: alle Tests PASS, ruff ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add dokumente.py tests/test_dokumente.py
git commit -m "feat: Upload-Validierung und Per-Datei-Fingerprint (#4)"
```

---

### Task 2: `convert.py` — Konvertierung als wiederverwendbare Funktion

**Files:**
- Modify: `convert.py` (komplett ersetzen)

**Interfaces:**
- Consumes: nichts.
- Produces: `pdf_nach_markdown(pdf_pfad, ausgabe=AUSGABE) -> pathlib.Path` (Pfad der geschriebenen `.md`); schreibt zusätzlich `<stamm>.source.json`.

Zwei Bugs, die dieser Task beseitigt:
1. `for alt in AUSGABE.glob("*.md"): alt.unlink()` löscht alle hochgeladenen Dokumente.
2. Der `FileNotFoundError` auf Modulebene würde jeden Import aus `web/main.py` sprengen — er muss unter `__main__`.

- [ ] **Step 1: `convert.py` ersetzen**

```python
import json
import pathlib
import re

import pymupdf
import pymupdf4llm

PDF = pathlib.Path("docs/CELEX_32022R2554_DE_TXT.pdf")
AUSGABE = pathlib.Path("docs_md")


def dokument_titel(pdf_pfad, fallback):
    text = pymupdf.open(str(pdf_pfad))[0].get_text()
    nummer = re.search(r"VERORDNUNG \(EU\)\s*([0-9]+/[0-9]+)", text)
    return f"Verordnung (EU) {nummer.group(1)} (DORA)" if nummer else fallback


def pdf_nach_markdown(pdf_pfad, ausgabe=AUSGABE):
    """Konvertiert eine PDF nach docs_md/<stamm>.md samt Quellen-Sidecar und gibt den Markdown-Pfad zurück."""
    pdf_pfad = pathlib.Path(pdf_pfad)
    ausgabe = pathlib.Path(ausgabe)
    ausgabe.mkdir(parents=True, exist_ok=True)

    stamm = pdf_pfad.stem
    md = pymupdf4llm.to_markdown(str(pdf_pfad))
    md_pfad = ausgabe / f"{stamm}.md"
    md_pfad.write_text(md, encoding="utf-8")
    (ausgabe / f"{stamm}.source.json").write_text(
        json.dumps({"titel": dokument_titel(pdf_pfad, stamm), "pdf": pdf_pfad.name},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_pfad


if __name__ == "__main__":
    if not PDF.exists():
        raise FileNotFoundError(
            f"{PDF} nicht gefunden. DORA-PDF von "
            "https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554 "
            "herunterladen und nach docs/CELEX_32022R2554_DE_TXT.pdf speichern."
        )
    pfad = pdf_nach_markdown(PDF)
    print("Titel:", dokument_titel(PDF, PDF.stem))
    print("Markdown:", pfad, "-", len(pfad.read_text(encoding='utf-8')), "Zeichen")
```

- [ ] **Step 2: Prüfen, dass Import ohne DORA-PDF funktioniert**

Run: `python -c "import convert; print(convert.pdf_nach_markdown.__name__)"`
Expected: `pdf_nach_markdown` — kein `FileNotFoundError`.

- [ ] **Step 3: Konvertierung gegen die vorhandene DORA-PDF prüfen (falls lokal vorhanden)**

Run: `test -f docs/CELEX_32022R2554_DE_TXT.pdf && python convert.py || echo "kein DORA-PDF lokal, Schritt übersprungen"`
Expected: entweder `Titel: Verordnung (EU) 2022/2554 (DORA)` plus geschriebener Pfad, oder die Übersprungen-Meldung.

- [ ] **Step 4: Commit**

```bash
git add convert.py
git commit -m "refactor: pdf_nach_markdown herausziehen, Korpus nicht mehr löschen (#4)"
```

---

### Task 3: `rag.py` — inkrementeller Merge statt Voll-Rebuild

**Files:**
- Modify: `rag.py` (Kopf bis `lade_oder_baue_index` ersetzen, `__main__`-Block bleibt)

**Interfaces:**
- Consumes: `dokumente.fingerprint`, `dokumente.diff`, `dokumente.datei_hash`, `dokumente.leerer_fingerprint` (Task 1).
- Produces:
  - `index` (Modulvariable, wie bisher — `agent.py` importiert sie)
  - `indexiere(md_pfad) -> None` — merged ein einzelnes Dokument in die bestehende Collection und schreibt den Fingerprint fort.
  - `loesche_nodes(dateiname: str) -> None`

Wichtig: `docs_md/` darf jetzt leer sein (frischer Container ohne DORA-PDF) — kein `FileNotFoundError` mehr auf Modulebene; ein leerer Index liefert `retrieve() == []` und damit den Abstain-Pfad.

- [ ] **Step 1: `rag.py` ersetzen (bis einschließlich `lade_oder_baue_index`)**

```python
import json
import os
from contextlib import suppress
from pathlib import Path

import chromadb
from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.vector_stores.chroma import ChromaVectorStore

import config
import dokumente

DISTANZMETRIK_WIE_IN_MEMORY = {"hnsw:space": "cosine"}  # docs/adr/0003
METRIK = DISTANZMETRIK_WIE_IN_MEMORY["hnsw:space"]
NEU_BAUEN = os.getenv("REGRAG_INDEX_NEU_BAUEN") == "1"
FINGERPRINT_DATEI = Path(config.CHROMA_VERZEICHNIS) / "fingerprint.json"

index = None
_collection = None


def _embed_model():
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    Settings.embed_model = HuggingFaceEmbedding(model_name=config.EMBEDDING_MODELL)


def _md_dateien():
    pfad = Path(config.DOKUMENTE_VERZEICHNIS)
    return sorted(pfad.glob("*.md")) if pfad.is_dir() else []


def _quelle_metadata(pfad):
    p = Path(pfad)
    sidecar = p.with_suffix(".source.json")
    daten = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    return {
        "file_name": p.name,
        "quelle": daten.get("titel", p.name),
        "pdf": daten.get("pdf"),
    }


def _fingerprint_lesen():
    if not FINGERPRINT_DATEI.exists():
        return {}
    return json.loads(FINGERPRINT_DATEI.read_text())


def _fingerprint_schreiben(fp):
    FINGERPRINT_DATEI.parent.mkdir(parents=True, exist_ok=True)
    FINGERPRINT_DATEI.write_text(json.dumps(fp, indent=2, ensure_ascii=False))


def loesche_nodes(dateiname):
    _collection.delete(where={"file_name": dateiname})


def indexiere(md_pfad):
    """Merged ein Dokument in die bestehende Collection und schreibt den Fingerprint fort."""
    md_pfad = Path(md_pfad)
    loesche_nodes(md_pfad.name)

    dokumente_ = SimpleDirectoryReader(
        input_files=[str(md_pfad)], file_metadata=_quelle_metadata
    ).load_data()
    for dok in dokumente_:
        index.insert(dok)

    fp = _fingerprint_lesen() or dokumente.leerer_fingerprint(config.EMBEDDING_MODELL, METRIK)
    fp["dokumente"][md_pfad.name] = dokumente.datei_hash(md_pfad)
    _fingerprint_schreiben(fp)


def lade_oder_baue_index():
    global index, _collection
    _embed_model()

    md_dateien = _md_dateien()
    neu = dokumente.fingerprint(md_dateien, config.EMBEDDING_MODELL, METRIK)
    alt = {} if NEU_BAUEN else _fingerprint_lesen()
    zu_indexieren, zu_loeschen, voll_rebuild = dokumente.diff(alt, neu)

    client = chromadb.PersistentClient(path=config.CHROMA_VERZEICHNIS)
    if voll_rebuild or NEU_BAUEN:
        FINGERPRINT_DATEI.unlink(missing_ok=True)
        with suppress(Exception):
            client.delete_collection(config.COLLECTION)
        zu_loeschen = []

    _collection = client.get_or_create_collection(
        config.COLLECTION, metadata=DISTANZMETRIK_WIE_IN_MEMORY
    )
    vector_store = ChromaVectorStore(chroma_collection=_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(
        vector_store, storage_context=storage_context
    )

    fp = _fingerprint_lesen() or dokumente.leerer_fingerprint(config.EMBEDDING_MODELL, METRIK)
    for name in zu_loeschen:
        loesche_nodes(name)
        fp["dokumente"].pop(name, None)
    _fingerprint_schreiben(fp)

    for name in zu_indexieren:
        indexiere(Path(config.DOKUMENTE_VERZEICHNIS) / name)

    return index


index = lade_oder_baue_index()
```

Der `if __name__ == "__main__":`-Block darunter bleibt unverändert.

- [ ] **Step 2: Kaltstart gegen ein Wegwerf-Verzeichnis prüfen**

Baut den Index über den inkrementellen Pfad neu auf (dauert einige Minuten, lädt bge-m3).

Run:
```bash
REGRAG_CHROMA_DIR=/tmp/regrag-chroma-test python -c "
import rag
print('nodes:', rag._collection.count())
print(rag._fingerprint_lesen())
"
```
Expected: `nodes:` deutlich > 0, Fingerprint enthält `CELEX_32022R2554_DE_TXT.md`.

- [ ] **Step 3: Zweiter Lauf indexiert nichts nach**

Run:
```bash
REGRAG_CHROMA_DIR=/tmp/regrag-chroma-test python -c "
import time; t=time.time()
import rag
print('nodes:', rag._collection.count(), 'sekunden:', round(time.time()-t))
"
```
Expected: gleiche Node-Zahl, Laufzeit im Bereich des reinen Modell-Ladens (keine erneute Embedding-Phase).

- [ ] **Step 4: Commit**

```bash
git add rag.py
git commit -m "feat: inkrementeller Index-Merge über Per-Datei-Fingerprint (#4)"
```

---

### Task 4: Abstain-Text vom Einzel-Korpus lösen

**Files:**
- Modify: `agent.py:11`

**Interfaces:**
- Consumes: nichts.
- Produces: `ABSTAIN_ANTWORT = "Nicht eindeutig in den indexierten Dokumenten belegt."` — `web/main.py` und `evaluation/run.py` importieren die Konstante, kein weiterer Anpassungsbedarf.

- [ ] **Step 1: Konstante ändern**

```python
ABSTAIN_ANTWORT = "Nicht eindeutig in den indexierten Dokumenten belegt."
```

- [ ] **Step 2: Prüfen, dass niemand den alten Text hart verdrahtet hat**

Run: `grep -rn "in DORA belegt" --include=*.py --include=*.html --include=*.md .`
Expected: keine Treffer außer ggf. in `docs/` (dort dann mit anpassen).

- [ ] **Step 3: Commit**

```bash
git add agent.py
git commit -m "fix: Abstain-Text nennt nicht mehr nur DORA (#4)"
```

---

### Task 5: Upload-, Job- und Dokument-Endpunkte

**Files:**
- Modify: `web/main.py`
- Modify: `requirements.txt` (`python-multipart` ergänzen — FastAPI braucht es für `UploadFile`)

**Interfaces:**
- Consumes: `dokumente.saeubere_dateiname`, `dokumente.pruefe_pdf`, `dokumente.UploadFehler` (Task 1); `convert.pdf_nach_markdown` (Task 2); `rag.indexiere` (Task 3).
- Produces:
  - `POST /upload` (multipart, Feldname `datei`) → `202` `{"job": "<hex>", "status": "pending", "dateiname": "...", "fehler": null}`
  - `GET /jobs/{job_id}` → `{"status": "pending"|"indexing"|"ready"|"failed", "dateiname": str, "fehler": str|null}`, `404` bei unbekannter ID
  - `GET /documents` → `[{"datei": "x.md", "titel": "..."}]`

- [ ] **Step 1: `python-multipart` in `requirements.txt` ergänzen**

```
fastapi
python-multipart
uvicorn[standard]
```

(die übrigen Zeilen bleiben stehen; `python-multipart` direkt hinter `fastapi`)

- [ ] **Step 2: `web/main.py` erweitern**

Kopf der Datei — Imports und Modul-Konstanten:

```python
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config
import dokumente
import rag
from agent import ABSTAIN_ANTWORT, beleglage_zu_schwach, llm, prompt, retriever
from convert import pdf_nach_markdown

app = FastAPI(title="RegRAG")
STATIC = Path(__file__).parent / "static"
DOKUMENTE = Path(config.DOKUMENTE_VERZEICHNIS)

JOBS = {}
POOL = ThreadPoolExecutor(max_workers=1)  # Embedding ist CPU-gebunden: strikt seriell
```

Ans Ende der Datei — Upload, Job-Status, Dokumentliste:

```python
def _entferne_fragmente(md_pfad):
    if md_pfad is None:
        return
    md_pfad.unlink(missing_ok=True)
    md_pfad.with_suffix(".source.json").unlink(missing_ok=True)


def _indexiere(job_id, daten, dateiname):
    job = JOBS[job_id]
    md_pfad = None
    try:
        job["status"] = "indexing"
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / dateiname
            pdf.write_bytes(daten)
            md_pfad = pdf_nach_markdown(pdf, DOKUMENTE)
        rag.indexiere(md_pfad)
        job["status"] = "ready"
    except Exception as e:
        _entferne_fragmente(md_pfad)
        job["status"] = "failed"
        job["fehler"] = f"{type(e).__name__}: {e}"


@app.post("/upload", status_code=202)
async def upload(datei: UploadFile = File(...)):
    try:
        name = dokumente.saeubere_dateiname(datei.filename or "")
        inhalt = await datei.read()
        dokumente.pruefe_pdf(inhalt)
    except dokumente.UploadFehler as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    if (DOKUMENTE / f"{Path(name).stem}.md").exists():
        raise HTTPException(status_code=409, detail="Dokument ist bereits indexiert.")

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "pending", "dateiname": name, "fehler": None}
    POOL.submit(_indexiere, job_id, inhalt, name)
    return {"job": job_id, **JOBS[job_id]}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job unbekannt.")
    return job


@app.get("/documents")
def dokument_liste():
    liste = []
    for md in sorted(DOKUMENTE.glob("*.md")) if DOKUMENTE.is_dir() else []:
        sidecar = md.with_suffix(".source.json")
        daten = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        liste.append({"datei": md.name, "titel": daten.get("titel", md.stem)})
    return liste
```

Die bestehende `@app.get("/")`-Route bleibt am Ende stehen.

- [ ] **Step 3: Endpunkte gegen den laufenden Server prüfen**

Server starten (`uvicorn web.main:app --port 8000`), dann in einer zweiten Shell:

```bash
curl -s -X POST -F 'datei=@/etc/hosts;filename=schaden.exe' localhost:8000/upload | head -c 200; echo
curl -s localhost:8000/documents; echo
```
Expected: erst `{"detail":"Nur PDF-Dateien werden angenommen."}` (HTTP 400), dann eine Liste mit dem DORA-Eintrag.

- [ ] **Step 4: Ruff**

Run: `ruff check .`
Expected: keine Befunde.

- [ ] **Step 5: Commit**

```bash
git add web/main.py requirements.txt
git commit -m "feat: POST /upload, GET /jobs/{id}, GET /documents (#4)"
```

---

### Task 6: UI — Upload-Feld, Statusanzeige, Dokumentliste

**Files:**
- Modify: `web/static/index.html`

**Interfaces:**
- Consumes: `POST /upload`, `GET /jobs/{id}`, `GET /documents` (Task 5).
- Produces: nichts für spätere Tasks.

- [ ] **Step 1: Styles ergänzen (vor `@keyframes rise`, Zeile ~204)**

```css
  .korpus {
    border-top: 1px solid var(--rule);
    padding: 16px 0;
    font-size: 13px;
    color: var(--ink-soft);
  }
  .korpus h4 {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    margin: 0 0 10px;
    font-weight: 500;
  }
  .korpus ul { list-style: none; margin: 0 0 12px; padding: 0; }
  .korpus li { font-family: var(--serif); font-size: 15px; color: var(--ink); padding: 2px 0; }
  .korpus .zeile { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .korpus .status { font-family: var(--mono); font-size: 12px; }
  .korpus .status.laeuft { color: var(--accent); }
  .korpus .status.fertig { color: var(--accent); }
  .korpus .status.fehler { color: var(--ochre); }
  .korpus .hinweis { font-style: italic; margin-top: 10px; font-family: var(--serif); }
  label.knopf {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 8px 14px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    color: var(--ink);
  }
  label.knopf.aus { opacity: 0.45; cursor: default; }
```

- [ ] **Step 2: Markup zwischen `</main>` und `<form id="form">` einfügen**

```html
  <section class="korpus">
    <h4>Indexierte Dokumente</h4>
    <ul id="dokumente"></ul>
    <div class="zeile">
      <label class="knopf" id="uploadKnopf" for="pdf">PDF hinzufügen</label>
      <input type="file" id="pdf" accept="application/pdf" hidden>
      <span class="status" id="uploadStatus"></span>
    </div>
    <div class="hinweis">
      Nur frei verwendbare Regulatorik hochladen (MaRisk, EBA-Guidelines). Keine urheberrechtlich
      geschützten Normtexte wie ISO oder DIN.
    </div>
  </section>
```

- [ ] **Step 3: Skript ergänzen (ans Ende des `<script>`-Blocks, vor `</script>`)**

```javascript
const dokumenteListe = document.getElementById('dokumente');
const pdfFeld = document.getElementById('pdf');
const uploadKnopf = document.getElementById('uploadKnopf');
const uploadStatus = document.getElementById('uploadStatus');

async function ladeDokumente() {
  const res = await fetch('/documents');
  const liste = await res.json();
  dokumenteListe.replaceChildren(...liste.map((d) => el('li', null, d.titel)));
}

function setzeStatus(text, klasse) {
  uploadStatus.textContent = text;
  uploadStatus.className = 'status' + (klasse ? ' ' + klasse : '');
}

async function verfolge(jobId) {
  while (true) {
    await new Promise((r) => setTimeout(r, 2000));
    const res = await fetch('/jobs/' + jobId);
    if (!res.ok) return setzeStatus('Job unbekannt.', 'fehler');
    const job = await res.json();
    if (job.status === 'ready') {
      setzeStatus(job.dateiname + ' ist durchsuchbar.', 'fertig');
      await ladeDokumente();
      return;
    }
    if (job.status === 'failed') return setzeStatus('Fehlgeschlagen: ' + job.fehler, 'fehler');
    setzeStatus('Wird indexiert — das dauert einige Minuten …', 'laeuft');
  }
}

pdfFeld.addEventListener('change', async () => {
  const datei = pdfFeld.files[0];
  if (!datei) return;
  const daten = new FormData();
  daten.append('datei', datei);
  uploadKnopf.classList.add('aus');
  pdfFeld.disabled = true;
  setzeStatus('Wird hochgeladen …', 'laeuft');

  const res = await fetch('/upload', { method: 'POST', body: daten });
  const antwort = await res.json();
  pdfFeld.value = '';
  if (!res.ok) {
    setzeStatus(antwort.detail || 'Upload fehlgeschlagen.', 'fehler');
  } else {
    await verfolge(antwort.job);
  }
  uploadKnopf.classList.remove('aus');
  pdfFeld.disabled = false;
});

ladeDokumente();
```

- [ ] **Step 4: Untertitel entschärfen (Zeile 212)**

```html
    <div class="unterzeile">Auskunft mit Quellenbeleg · DORA und eigene Dokumente</div>
```

- [ ] **Step 5: Im Browser prüfen**

Server läuft; `http://localhost:8000` öffnen. Erwartung: Liste zeigt „Verordnung (EU) 2022/2554 (DORA)", Knopf „PDF hinzufügen" vorhanden, Urheberrechts-Hinweis sichtbar.

- [ ] **Step 6: Commit**

```bash
git add web/static/index.html
git commit -m "feat: Upload-Feld, Job-Status und Dokumentliste in der UI (#4)"
```

---

### Task 7: Persistenz — Volume für `docs_md/`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md` (Abschnitt „Lauf" / Docker-Abschnitt um Upload ergänzen)

**Interfaces:**
- Consumes: nichts.
- Produces: `docs_md`-Volume; zusammen mit `chroma` überleben Korpus und Index den Neustart.

- [ ] **Step 1: `docker-compose.yml` um das Volume erweitern**

```yaml
    volumes:
      - chroma:/app/chroma
      - docs_md:/app/docs_md
      - ./docs/CELEX_32022R2554_DE_TXT.pdf:/app/docs/CELEX_32022R2554_DE_TXT.pdf:ro

volumes:
  chroma:
  docs_md:
```

- [ ] **Step 2: README ergänzen — Upload-Absatz nach dem Docker-Abschnitt**

```markdown
### Eigene Dokumente hochladen

Über die Web-UI lässt sich eine weitere PDF hochladen (z. B. MaRisk, EBA ICT-Guidelines).
Die Indexierung läuft im Hintergrund; die Statuszeile meldet, sobald das Dokument
durchsuchbar ist. Korpus (`docs_md`) und Index (`chroma`) liegen auf Volumes und
überstehen `docker compose restart`.

Keine urheberrechtlich geschützten Normtexte (ISO, DIN) hochladen.
Größenlimit: `REGRAG_UPLOAD_MAX_MB` (Default 25).
```

- [ ] **Step 3: Akzeptanztest gegen den Container**

```bash
docker compose up -d --build
# 1. Upload in der UI (http://localhost:8000), warten bis "ist durchsuchbar"
# 2. Frage stellen, die nur das neue Dokument beantwortet -> Antwort nennt das Dokument als Quelle
docker compose restart
sleep 30
curl -s localhost:8000/documents
# 3. Dieselbe Frage erneut -> weiterhin korrekt beantwortet, kein Neu-Indexieren in den Logs
docker compose logs --tail 30 regrag
```
Expected: `/documents` listet beide Dokumente; die Logs zeigen keine erneute Embedding-Phase; die Antwort nennt weiterhin das hochgeladene Dokument.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml README.md
git commit -m "feat: docs_md als Volume, Upload im README dokumentieren (#4)"
```

---

### Task 8: ADR und PR

**Files:**
- Create: `docs/adr/0006-inkrementeller-index-merge-statt-voll-rebuild.md`

- [ ] **Step 1: ADR schreiben**

```markdown
# 0006 — Inkrementeller Index-Merge statt Voll-Rebuild

## Status
Akzeptiert

## Kontext
Der Fingerprint deckte bisher den gesamten Korpus in einem Hash ab. Jede Änderung — auch ein
einzelnes neues Dokument — löschte die Collection und embeddete alles neu (~5 min pro Dokument,
linear wachsend). Mit dem Upload über die Web-UI (Issue #4) ist das nicht mehr tragbar: der
Nutzer wartet sonst auf die Neu-Indexierung von Dokumenten, die sich nicht geändert haben.

## Entscheidung
Der Fingerprint wird zu einer Map `{dateiname: sha256}`. Beim Start und beim Upload läuft
derselbe Merge-Pfad: geänderte oder entfernte Dokumente werden über
`collection.delete(where={"file_name": ...})` aus Chroma entfernt, neue über `index.insert()`
eingefügt. Ein Voll-Rebuild bleibt nur für den Wechsel von Embedding-Modell oder Distanzmetrik —
dort sind die alten Vektoren tatsächlich wertlos — sowie für `REGRAG_INDEX_NEU_BAUEN=1`.

## Konsequenzen
- Upload kostet nur die Embedding-Zeit des neuen Dokuments.
- Der Löschpfad hängt am Metadatenfeld `file_name`; es muss bei jedem Node gesetzt sein.
- Der Fingerprint ist damit zugleich das Inventar des Index — was drinsteht, ist indexiert.
```

- [ ] **Step 2: Volle Testsuite und Lint**

Run: `python -m pytest -q tests && ruff check . && python -m compileall -q agent.py config.py convert.py dokumente.py rag.py evaluation web`
Expected: alles grün.

- [ ] **Step 3: Commit und PR**

```bash
git add docs/adr/0006-inkrementeller-index-merge-statt-voll-rebuild.md
git commit -m "docs: ADR 0006 zum inkrementellen Index-Merge (#4)"
git push -u origin HEAD
gh pr create --title "Dokumenten-Upload über die Web-UI mit inkrementeller Re-Indexierung (schließt #4)" --body "..."
```

Der PR-Body nennt: Endpunkte, Merge-Verhalten, Volumes, das Ergebnis des Akzeptanztests aus Task 7 (inkl. der gestellten Frage und der genannten Quelle) sowie die Annahme, dass Job-Status bewusst nicht persistiert wird.
