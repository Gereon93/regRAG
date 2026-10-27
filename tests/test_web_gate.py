import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest

from gate import RetrievalScoreGate, entscheide_und_eskaliere

MIN_SCORE = 0.62
ABSTAIN_TEXT = "Keine belastbare Grundlage."


def node(score):
    return SimpleNamespace(score=score, text="Art. 5 DORA", metadata={"quelle": "dora.md"})


class FakeLLM:
    def __init__(self):
        self.aufrufe = 0

    async def astream(self, nachrichten):
        self.aufrufe += 1
        yield SimpleNamespace(content="Antwort")


@pytest.fixture
def strom(tmp_path):
    eskalationen = tmp_path / "eskalationen.jsonl"
    gate = RetrievalScoreGate(MIN_SCORE)
    llm = FakeLLM()
    treffer = []

    def pruefe(frage, nodes):
        return entscheide_und_eskaliere(gate, eskalationen, frage, nodes)

    prompt = SimpleNamespace(format_messages=lambda **kw: [])
    retriever = SimpleNamespace(retrieve=lambda frage: treffer)
    stubs = {
        "rag": {},
        "convert": {"pdf_nach_markdown": lambda *a, **k: None},
        "agent": {
            "ABSTAIN_ANTWORT": ABSTAIN_TEXT,
            "llm": llm,
            "prompt": prompt,
            "pruefe": pruefe,
            "retriever": retriever,
        },
    }
    alt = {name: sys.modules.get(name) for name in [*stubs, "web.main"]}
    sys.modules.pop("web.main", None)
    for name, attrs in stubs.items():
        modul = types.ModuleType(name)
        vars(modul).update(attrs)
        sys.modules[name] = modul

    import web.main as main

    def lauf(frage, nodes):
        treffer[:] = nodes

        async def sammle():
            return [e async for e in main._antwort_strom(frage)]

        events = asyncio.run(sammle())
        return [json.loads(e.removeprefix("data: ")) for e in events]

    yield SimpleNamespace(lauf=lauf, llm=llm, eskalationen=eskalationen)

    for name, vorher in alt.items():
        if vorher is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = vorher


def test_schwache_evidenz_verweigert_ohne_llm_und_schreibt_eskalation(strom):
    events = strom.lauf("Wie backe ich Kuchen?", [node(0.5)])

    assert [e["type"] for e in events] == ["abstain", "sources", "done"]
    assert events[0]["text"] == ABSTAIN_TEXT
    assert strom.llm.aufrufe == 0
    satz = json.loads(strom.eskalationen.read_text(encoding="utf-8"))
    assert satz["frage"] == "Wie backe ich Kuchen?"
    assert satz["reasons"] == ["LOW_RELEVANCE"]
    assert satz["evidenz"][0]["score"] == 0.5


def test_starke_evidenz_antwortet_und_schreibt_keine_eskalation(strom):
    events = strom.lauf("Was regelt DORA?", [node(0.7)])

    assert [e["type"] for e in events] == ["token", "sources", "done"]
    assert strom.llm.aufrufe == 1
    assert not strom.eskalationen.exists()
