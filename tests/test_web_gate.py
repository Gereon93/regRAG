import asyncio
import importlib
import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from gate import (
    ABSTAIN_ANTWORT_NICHT_VORGEMERKT,
    ABSTAIN_ANTWORT_VORGEMERKT,
    RetrievalScoreGate,
    entscheide_und_eskaliere,
)
from laya import baue_gate

MIN_SCORE = 0.62


def node(score):
    return SimpleNamespace(score=score, text="Art. 5 DORA", metadata={"quelle": "dora.md"})


class FakeLLM:
    def __init__(self):
        self.aufrufe = 0

    async def astream(self, nachrichten):
        self.aufrufe += 1
        yield SimpleNamespace(content="Antwort")


@contextmanager
def _strom(gate, eskalationen):
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
            "llm": llm,
            "prompt": prompt,
            "pruefe": pruefe,
            "retriever": retriever,
        },
    }
    alt = {name: sys.modules.get(name) for name in [*stubs, "web.main"]}
    web_paket = sys.modules.get("web")
    altes_attribut = getattr(web_paket, "main", None) if web_paket else None
    sys.modules.pop("web.main", None)
    for name, attrs in stubs.items():
        modul = types.ModuleType(name)
        vars(modul).update(attrs)
        sys.modules[name] = modul

    main = importlib.import_module("web.main")

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
    web_paket = sys.modules.get("web")
    if web_paket is not None:
        if altes_attribut is None:
            vars(web_paket).pop("main", None)
        else:
            web_paket.main = altes_attribut


@pytest.fixture
def strom(tmp_path, request):
    eskalationen = tmp_path / getattr(request, "param", "eskalationen.jsonl")
    with _strom(RetrievalScoreGate(MIN_SCORE), eskalationen) as s:
        yield s


@pytest.fixture
def laya_strom(tmp_path):
    @contextmanager
    def bauen(handler):
        einstellungen = SimpleNamespace(
            MIN_RETRIEVAL_SCORE=MIN_SCORE,
            LAYA_URL="http://localhost:8000",
            LAYA_API_KEY="",
            LAYA_TIMEOUT=5.0,
            LAYA_MAX_LEN=4096,
            LAYA_LANG="de",
            LAYA_MIN_ANSWERABLE=0.5,
        )
        gate = baue_gate(einstellungen, transport=httpx.MockTransport(handler))
        with _strom(gate, tmp_path / "eskalationen.jsonl") as s:
            yield s

    return bauen


def test_schwache_evidenz_verweigert_ohne_llm_und_schreibt_eskalation(strom):
    events = strom.lauf("Wie backe ich Kuchen?", [node(0.5)])

    assert [e["type"] for e in events] == ["abstain", "sources", "done"]
    assert events[0]["text"] == ABSTAIN_ANTWORT_VORGEMERKT
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


@pytest.mark.parametrize("strom", ["datei/eskalationen.jsonl"], indirect=True)
def test_abstain_ohne_schreibbaren_datensatz_verspricht_keine_vormerkung(strom, tmp_path):
    (tmp_path / "datei").write_text("x")

    events = strom.lauf("Wie backe ich Kuchen?", [node(0.5)])

    assert events[0]["type"] == "abstain"
    assert events[0]["text"] == ABSTAIN_ANTWORT_NICHT_VORGEMERKT
    assert strom.llm.aufrufe == 0


def test_laya_nicht_beantwortbar_verweigert_mit_answerable_im_datensatz(laya_strom):
    def nicht_beantwortbar(request):
        return httpx.Response(200, json={"answers": {"answerable": {"noul": 0.2}}})

    with laya_strom(nicht_beantwortbar) as strom:
        events = strom.lauf("Was regelt DORA zur Ausnahme?", [node(0.8)])

        assert [e["type"] for e in events] == ["abstain", "sources", "done"]
        assert strom.llm.aufrufe == 0
        satz = json.loads(strom.eskalationen.read_text(encoding="utf-8"))
        assert satz["reasons"] == ["NOT_ANSWERABLE"]
        assert satz["scores"] == {"relevance": 0.8, "answerable": 0.2}


def test_laya_ausfall_verweigert_mit_judge_unavailable(laya_strom):
    with laya_strom(lambda r: httpx.Response(503)) as strom:
        events = strom.lauf("Was regelt DORA?", [node(0.8)])

        assert events[0]["type"] == "abstain"
        assert "nicht verfügbar" in events[0]["text"]
        assert strom.llm.aufrufe == 0
        satz = json.loads(strom.eskalationen.read_text(encoding="utf-8"))
        assert satz["reasons"] == ["JUDGE_UNAVAILABLE"]


def test_laya_bejaht_antwortet_normal(laya_strom):
    with laya_strom(lambda r: httpx.Response(200, json={"answers": {"answerable": {"noul": 0.9}}})) as strom:
        events = strom.lauf("Was regelt DORA?", [node(0.8)])

        assert [e["type"] for e in events] == ["token", "sources", "done"]
        assert not strom.eskalationen.exists()
