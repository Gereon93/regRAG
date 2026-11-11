import asyncio
import json
import logging
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
import dokumente
import metrics
import rag
from agent import llm, prompt, pruefe, pruefe_grounding, retriever
from convert import pdf_nach_markdown
from gate import (
    MAX_FRAGE_ZEICHEN,
    abstain_antwort,
    aktiviere_score_log,
    bereinige_eskalationen,
    grenzen_aus,
)
from grounding import baue_kontext

aktiviere_score_log()
log = logging.getLogger(__name__)



@asynccontextmanager
async def _lebenszyklus(_app):
    await asyncio.to_thread(bereinige_eskalationen, config.ESKALATION_DATEI, grenzen_aus(config))
    yield


app = FastAPI(title="RegRAG", lifespan=_lebenszyklus)
STATIC = Path(__file__).parent / "static"
DOKUMENTE = Path(config.DOKUMENTE_VERZEICHNIS)

EMBEDDING_IST_CPU_GEBUNDEN_ALSO_SERIELL = 1
LESE_STUECK_BYTES = 1024 * 1024

JOBS = {}
NAMEN_IN_ARBEIT = set()
POOL = ThreadPoolExecutor(max_workers=EMBEDDING_IST_CPU_GEBUNDEN_ALSO_SERIELL)
KEIN_JOBSTART_WAEHREND_GELOESCHT_WIRD = threading.Lock()


class Frage(BaseModel):
    frage: str = Field(min_length=1, max_length=MAX_FRAGE_ZEICHEN)


def _quellen(nodes):
    return [
        {
            "nr": i,
            "quelle": n.metadata.get("quelle") or n.metadata.get("file_name"),
            "score": round(n.score, 3),
        }
        for i, n in enumerate(nodes, start=1)
    ]


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _antwort_strom(frage):
    treffer = retriever.retrieve(frage)

    pruefung = await asyncio.to_thread(pruefe, frage, treffer)
    nodes = pruefung.entscheidung.genutzte_nodes(treffer)
    quellen = _quellen(nodes)
    if pruefung.verweigert:
        yield _sse({"type": "abstain", "text": abstain_antwort(pruefung.vorgemerkt, pruefung.entscheidung.reasons)})
        yield _sse({"type": "sources", "quellen": quellen})
        yield _sse({"type": "done"})
        return

    yield _sse({"type": "status", "text": "Antwort wird erzeugt und geprüft …"})
    try:
        nachrichten = prompt.format_messages(kontext=baue_kontext(nodes), frage=frage)
        antwort = "".join([c.content async for c in llm.astream(nachrichten) if c.content])
        geprueft = await asyncio.to_thread(pruefe_grounding, frage, treffer, antwort, pruefung.entscheidung)
    except Exception as e:
        log.exception("Antwort konnte nicht erzeugt oder geprüft werden")
        yield _sse({"type": "error", "text": f"Antwort konnte nicht erzeugt werden ({type(e).__name__}) — bitte erneut versuchen."})
    else:
        if geprueft.verweigert:
            yield _sse({"type": "abstain", "text": abstain_antwort(geprueft.vorgemerkt, geprueft.entscheidung.reasons)})
        else:
            yield _sse({"type": "token", "text": antwort})

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


def _indexiere(job_id, daten, dateiname, metadaten):
    job = JOBS[job_id]
    md_pfad = None
    try:
        job["status"] = "indexing"
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / dateiname
            pdf.write_bytes(daten)
            md_pfad = pdf_nach_markdown(pdf, DOKUMENTE, metadaten=metadaten)
        rag.indexiere(md_pfad)
        job["status"] = "ready"
    except Exception as e:  # noqa: BLE001 - Hintergrund-Job: jeder Fehler landet im Job-Status statt im Nichts
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
async def upload(
    datei: Annotated[UploadFile, File()],
    gueltig_ab: Annotated[str | None, Form()] = None,
    gueltig_bis: Annotated[str | None, Form()] = None,
    fassung: Annotated[str | None, Form()] = None,
):
    try:
        name = dokumente.saeubere_dateiname(datei.filename or "")
        metadaten = dokumente.pruefe_gueltigkeit(gueltig_ab, gueltig_bis, fassung)
        inhalt = await _lies_begrenzt(datei)
        dokumente.pruefe_pdf(inhalt)
    except dokumente.UploadFehler as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    with KEIN_JOBSTART_WAEHREND_GELOESCHT_WIRD:
        if name in NAMEN_IN_ARBEIT:
            raise HTTPException(status_code=409, detail="Dokument wird bereits indexiert.")
        if (DOKUMENTE / f"{Path(name).stem}.md").exists():
            raise HTTPException(status_code=409, detail="Dokument ist bereits indexiert.")
        NAMEN_IN_ARBEIT.add(name)

        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "pending", "dateiname": name, "fehler": None}
        POOL.submit(_indexiere, job_id, inhalt, name, metadaten)
    return {"job": job_id, **JOBS[job_id]}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job unbekannt.")
    return job


def _titel(md):
    """Ein abgelehntes Sidecar (rag._quelle_metadata) darf die Liste nicht kippen; es bleibt der Dateiname."""
    sidecar = md.with_suffix(".source.json")
    try:
        daten = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    except ValueError:
        return md.stem
    return daten.get("titel", md.stem) if isinstance(daten, dict) else md.stem


@app.get("/documents")
def dokument_liste():
    liste = []
    for md in sorted(DOKUMENTE.glob("*.md")) if DOKUMENTE.is_dir() else []:
        liste.append({"datei": md.name, "titel": _titel(md)})
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

    with KEIN_JOBSTART_WAEHREND_GELOESCHT_WIRD:
        if _irgendeine_indexierung_laeuft():
            raise HTTPException(status_code=409, detail="Es läuft gerade eine Indexierung.")
        if not (DOKUMENTE / name).exists():
            raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")

        rag.loesche_dokument(name)


@app.get("/metrics")
def metriken():
    return metrics.METRIKEN.schnappschuss()


@app.get("/")
def startseite():
    return FileResponse(STATIC / "index.html")
