import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
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

EMBEDDING_IST_CPU_GEBUNDEN_ALSO_SERIELL = 1
LESE_STUECK_BYTES = 1024 * 1024

JOBS = {}
NAMEN_IN_ARBEIT = set()
POOL = ThreadPoolExecutor(max_workers=EMBEDDING_IST_CPU_GEBUNDEN_ALSO_SERIELL)


class Frage(BaseModel):
    frage: str


def _quellen(nodes):
    return [
        {
            "quelle": n.metadata.get("quelle") or n.metadata.get("file_name"),
            "score": round(n.score, 3),
        }
        for n in nodes
    ]


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _antwort_strom(frage):
    nodes = retriever.retrieve(frage)
    quellen = _quellen(nodes)

    if beleglage_zu_schwach(nodes):
        yield _sse({"type": "abstain", "text": ABSTAIN_ANTWORT})
        yield _sse({"type": "sources", "quellen": quellen})
        yield _sse({"type": "done"})
        return

    kontext = "\n\n".join(n.text for n in nodes)
    try:
        async for chunk in llm.astream(
            prompt.format_messages(kontext=kontext, frage=frage)
        ):
            if chunk.content:
                yield _sse({"type": "token", "text": chunk.content})
    except Exception as e:
        yield _sse({"type": "error", "text": f"LLM-Fehler ({type(e).__name__}) — bitte erneut versuchen."})

    yield _sse({"type": "sources", "quellen": quellen})
    yield _sse({"type": "done"})


@app.post("/chat")
async def chat(f: Frage):
    return StreamingResponse(_antwort_strom(f.frage), media_type="text/event-stream")


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
        with suppress(Exception):
            rag.loesche_nodes(f"{Path(dateiname).stem}.md")
        _entferne_fragmente(md_pfad)
        job["status"] = "failed"
        job["fehler"] = f"{type(e).__name__}: {e}"
    finally:
        NAMEN_IN_ARBEIT.discard(dateiname)


async def _lies_begrenzt(datei):
    stuecke, gelesen = [], 0
    while stueck := await datei.read(LESE_STUECK_BYTES):
        gelesen += len(stueck)
        dokumente.pruefe_groesse(gelesen)
        stuecke.append(stueck)
    return b"".join(stuecke)


@app.post("/upload", status_code=202)
async def upload(datei: UploadFile = File(...)):
    try:
        name = dokumente.saeubere_dateiname(datei.filename or "")
        inhalt = await _lies_begrenzt(datei)
        dokumente.pruefe_pdf(inhalt)
    except dokumente.UploadFehler as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    if name in NAMEN_IN_ARBEIT:
        raise HTTPException(status_code=409, detail="Dokument wird bereits indexiert.")
    if (DOKUMENTE / f"{Path(name).stem}.md").exists():
        raise HTTPException(status_code=409, detail="Dokument ist bereits indexiert.")
    NAMEN_IN_ARBEIT.add(name)

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


def _irgendeine_indexierung_laeuft():
    """Egal welches Dokument: _indexiere() und loesche_dokument() schreiben beide fingerprint.json neu."""
    return bool(NAMEN_IN_ARBEIT)


@app.delete("/documents/{datei}", status_code=204)
def dokument_loeschen(datei: str):
    try:
        name = dokumente.saeubere_md_name(datei)
    except dokumente.UploadFehler as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    if _irgendeine_indexierung_laeuft():
        raise HTTPException(status_code=409, detail="Es läuft gerade eine Indexierung.")
    if not (DOKUMENTE / name).exists():
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")

    rag.loesche_dokument(name)


@app.get("/")
def startseite():
    return FileResponse(STATIC / "index.html")
