import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent import ABSTAIN_ANTWORT, beleglage_zu_schwach, llm, prompt, retriever

app = FastAPI(title="RegRAG")
STATIC = Path(__file__).parent / "static"


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


@app.get("/")
def startseite():
    return FileResponse(STATIC / "index.html")
