import asyncio
import importlib
import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import grounding
from gate import (
    ABSTAIN_ANTWORT_NICHT_VORGEMERKT,
    ABSTAIN_ANTWORT_VORGEMERKT,
    RetrievalScoreGate,
    entscheide_und_eskaliere,
)
from laya import baue_gate

MIN_SCORE = 0.62


BEANTWORTBAR = "Das Leitungsorgan trägt die Verantwortung [1]. Vorfälle sind binnen 72 Stunden zu melden [2]."
KORPUS = (
    "Artikel 5: Das Leitungsorgan trägt die Verantwortung für das IKT-Risikomanagement.",
    "Artikel 19: Eine Zwischenmeldung erfolgt binnen 72 Stunden.",
    "Artikel 30: Die Vertragsbestimmungen sind schriftlich festzuhalten.",
)


def node(score, text="Art. 5 DORA"):
    return SimpleNamespace(score=score, text=text, metadata={"quelle": "dora.md"})


def korpus():
    return [node(0.8 - i / 100, text) for i, text in enumerate(KORPUS)]


class FakeLLM:
    def __init__(self):
        self.aufrufe = 0
        self.teile = ["DORA regelt das [1]."]
        self.fehler = None

    async def astream(self, nachrichten):
        self.aufrufe += 1
        for teil in self.teile:
            yield SimpleNamespace(content=teil)
        if self.fehler:
            raise self.fehler


@contextmanager
def _strom(gate, eskalationen):
    llm = FakeLLM()
    treffer = []

    def pruefe(frage, nodes):
        return entscheide_und_eskaliere(gate, eskalationen, frage, nodes)

    def pruefe_grounding(frage, nodes, antwort, vorherige):
        return grounding.pruefe_antwort_und_eskaliere(eskalationen, frage, nodes, antwort, vorherige)

    kontexte = []
    prompt = SimpleNamespace(format_messages=lambda **kw: kontexte.append(kw["kontext"]) or [])
    retriever = SimpleNamespace(retrieve=lambda frage: treffer)
    stubs = {
        "rag": {},
        "convert": {"pdf_nach_markdown": lambda *a, **k: None},
        "agent": {
            "llm": llm,
            "prompt": prompt,
            "pruefe": pruefe,
            "pruefe_grounding": pruefe_grounding,
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

    yield SimpleNamespace(lauf=lauf, llm=llm, eskalationen=eskalationen, main=main, kontexte=kontexte)

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
            LAYA_MODELL="multilingual",
            GATE_MANIFEST="",
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

    assert [e["type"] for e in events] == ["status", "token", "sources", "done"]
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


def test_metrics_zaehlt_chat_entscheidungen_ohne_inhalte(laya_strom, monkeypatch):
    import metrics

    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())

    def antwort(request):
        return httpx.Response(200, json={"answers": {"answerable": {"noul": 0.9}}})

    with laya_strom(antwort) as strom:
        strom.lauf("Geheime Frage ueber Kuchen?", [node(0.8)])
        strom.lauf("Geheime Frage ueber Kuchen?", [node(0.3)])
        main = sys.modules["web.main"]

        async def hole():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.get("/metrics")

        antwort_http = asyncio.run(hole())

    daten = antwort_http.json()
    assert antwort_http.status_code == 200
    assert daten["entscheidungen_gesamt"] == 2
    assert daten["eskalationen_nach_reason"] == {"LOW_RELEVANCE": 1}
    assert daten["histogramme"]["answerable"]["none"] == 1
    assert daten["histogramme"]["answerable"]["0.9-1.0"] == 1
    assert daten["histogramme"]["relevance"]["0.3-0.4"] == 1
    assert daten["nach_segment"]["unbekannt"]["entscheidungen"] == 2
    text = antwort_http.text
    for verboten in ("Geheime", "Kuchen", str(MIN_SCORE), "manifest", "dora.md", "Art. 5"):
        assert verboten not in text


def test_laya_bejaht_antwortet_normal(laya_strom):
    with laya_strom(lambda r: httpx.Response(200, json={"answers": {"answerable": {"noul": 0.9}}})) as strom:
        events = strom.lauf("Was regelt DORA?", [node(0.8)])

        assert [e["type"] for e in events] == ["status", "token", "sources", "done"]
        assert not strom.eskalationen.exists()


@pytest.mark.parametrize("frage", ["x" * 2001, ""])
def test_chat_lehnt_unzulaessige_frage_mit_422_ab(strom, frage):
    antwort = TestClient(strom.main.app).post("/chat", json={"frage": frage})

    assert antwort.status_code == 422
    assert strom.llm.aufrufe == 0
    assert not strom.eskalationen.exists()


@pytest.mark.parametrize("frage", ["x" * 2000, "ä" * 2000])
def test_chat_akzeptiert_frage_an_der_grenze_in_zeichen_nicht_bytes(strom, frage):
    antwort = TestClient(strom.main.app).post("/chat", json={"frage": frage})

    assert antwort.status_code == 200


def test_serverstart_bereinigt_ueberfaelliges_archiv(strom, monkeypatch):
    archiv = strom.eskalationen.with_name(strom.eskalationen.name + ".20200101T000000000000Z.1")
    archiv.write_text('{"zeitpunkt": "2020-01-01T00:00:00+00:00"}\n', encoding="utf-8")
    monkeypatch.setattr(strom.main.config, "ESKALATION_DATEI", str(strom.eskalationen))

    with TestClient(strom.main.app):
        pass

    assert not archiv.exists()


def _typen(events):
    return [e["type"] for e in events]


def _dump(events):
    return json.dumps(events, ensure_ascii=False)


def test_beantwortbar_liefert_geprueften_text_nummerierte_quellen_und_kontext(strom):
    strom.llm.teile = [BEANTWORTBAR]

    events = strom.lauf("Wer trägt die Verantwortung?", korpus())

    assert _typen(events) == ["status", "token", "sources", "done"]
    assert events[1]["text"] == BEANTWORTBAR
    assert [q["nr"] for q in events[2]["quellen"]] == [1, 2, 3]
    assert strom.kontexte[0].startswith("[1] Artikel 5")
    assert "\n\n[3] Artikel 30" in strom.kontexte[0]
    assert not strom.eskalationen.exists()


def test_erfolg_liefert_mehrere_llm_chunks_als_ein_token(strom):
    strom.llm.teile = [BEANTWORTBAR[:20], BEANTWORTBAR[20:]]

    events = strom.lauf("Frage?", korpus())

    assert [e["text"] for e in events if e["type"] == "token"] == [BEANTWORTBAR]


def test_antwort_ohne_marker_wird_verworfen_und_eskaliert(strom):
    strom.llm.teile = ["Das Leitungsorgan ", "haftet immer."]

    events = strom.lauf("Wer haftet?", korpus())

    assert _typen(events) == ["status", "abstain", "sources", "done"]
    assert events[1]["text"] == ABSTAIN_ANTWORT_VORGEMERKT
    assert "haftet immer" not in _dump(events)
    zeilen = strom.eskalationen.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1
    satz = json.loads(zeilen[0])
    assert satz["reasons"] == ["UNGROUNDED_CLAIM"]
    assert satz["antwort"] == "Das Leitungsorgan haftet immer."
    assert satz["aussagen_gesamt"] == 1
    assert satz["aussagen_ohne_beleg"] == [{"text": "Das Leitungsorgan haftet immer.", "grund": "kein_marker"}]


def test_ungueltiger_marker_wird_verworfen(strom):
    strom.llm.teile = ["Das steht in Fundstelle neun [9]."]

    events = strom.lauf("Frage?", korpus())

    assert _typen(events) == ["status", "abstain", "sources", "done"]
    satz = json.loads(strom.eskalationen.read_text(encoding="utf-8"))
    assert satz["aussagen_ohne_beleg"][0]["grund"] == "ungueltiger_marker"


def test_erfundene_frist_wird_verworfen(strom):
    strom.llm.teile = ["Die Meldung erfolgt binnen 4 Stunden [2]."]

    events = strom.lauf("Frage?", korpus())

    assert _typen(events) == ["status", "abstain", "sources", "done"]
    satz = json.loads(strom.eskalationen.read_text(encoding="utf-8"))
    assert satz["aussagen_ohne_beleg"][0]["grund"] == "nicht_gestuetzt"


def test_unbeantwortbar_ruft_llm_nicht_auf_und_sendet_keinen_status(strom):
    events = strom.lauf("Wie backe ich Kuchen?", [node(0.4, text) for text in KORPUS])

    assert _typen(events) == ["abstain", "sources", "done"]
    assert strom.llm.aufrufe == 0


def test_llm_fehler_nach_einem_chunk_zeigt_keinen_teiltext_und_eskaliert_nicht(strom):
    strom.llm.teile = ["Teiltext der Antwort"]
    strom.llm.fehler = ConnectionError("weg")

    events = strom.lauf("Frage?", korpus())

    assert _typen(events) == ["status", "error", "sources", "done"]
    assert "Teiltext" not in _dump(events)
    assert not strom.eskalationen.exists()


def test_fehler_in_der_pruefung_liefert_nie_die_antwort(strom, monkeypatch):
    def kaputt(*a):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(strom.main, "pruefe_grounding", kaputt)
    strom.llm.teile = [BEANTWORTBAR]

    events = strom.lauf("Frage?", korpus())

    assert _typen(events) == ["status", "error", "sources", "done"]
    assert "Leitungsorgan" not in _dump(events)


def test_metrics_zaehlt_verwurf_als_eine_entscheidung(strom, monkeypatch):
    import metrics

    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())
    strom.llm.teile = ["Geheimer Antworttext ohne Beleg."]

    strom.lauf("Frage?", korpus())
    antwort = TestClient(strom.main.app).get("/metrics")

    daten = antwort.json()
    assert daten["entscheidungen_gesamt"] == 1
    assert daten["eskalationen_gesamt"] == 1
    assert daten["eskalationen_nach_reason"] == {"UNGROUNDED_CLAIM": 1}
    assert "Geheimer" not in antwort.text
