import asyncio
import importlib
import json
import sys
import types
from contextlib import contextmanager
from datetime import date
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


def node(score, text="Art. 5 DORA", **metadata):
    return SimpleNamespace(score=score, text=text, metadata={"quelle": "dora.md", **metadata})


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

    try:
        yield SimpleNamespace(lauf=lauf, llm=llm, eskalationen=eskalationen, main=main, kontexte=kontexte)
    finally:
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
            LAYA_KONFLIKT_SCHWELLE=None,
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
        assert satz["scores"] == {"relevance": 0.8, "answerable": 0.2, "konflikt": None}


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


def test_fehler_in_der_pruefung_liefert_nie_die_antwort(strom, monkeypatch, caplog):
    def kaputt(*a):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(strom.main, "pruefe_grounding", kaputt)
    strom.llm.teile = [BEANTWORTBAR]

    events = strom.lauf("Frage?", korpus())

    assert _typen(events) == ["status", "error", "sources", "done"]
    assert "Leitungsorgan" not in _dump(events)
    assert "RuntimeError: kaputt" in caplog.text


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


STICHTAG = date(2026, 6, 15)
ABGELOEST = {"gueltig_bis": "2026-06-14", "file_name": "alt.md"}
GUELTIG = {"gueltig_ab": "2026-06-15", "file_name": "neu.md"}


def zeigt_quellen(events):
    return next(e["quellen"] for e in events if e["type"] == "sources")


@pytest.fixture
def korpus_strom(tmp_path, monkeypatch):
    @contextmanager
    def bauen(handler=None, schwelle=None):
        einstellungen = SimpleNamespace(
            MIN_RETRIEVAL_SCORE=MIN_SCORE,
            LAYA_URL="http://localhost:8000" if handler else "",
            LAYA_API_KEY="",
            LAYA_TIMEOUT=5.0,
            LAYA_MAX_LEN=4096,
            LAYA_LANG="de",
            LAYA_MIN_ANSWERABLE=0.5,
            LAYA_MODELL="multilingual",
            GATE_MANIFEST="",
            LAYA_KONFLIKT_SCHWELLE=schwelle,
        )
        transport = httpx.MockTransport(handler) if handler else None
        gate = baue_gate(einstellungen, transport=transport)
        gate.gates[1].uhr = lambda: STICHTAG
        with _strom(gate, tmp_path / "eskalationen.jsonl") as s:
            yield s

    return bauen


def _laya(answerable=0.9, konflikt=0.0, fragen=None):
    def handler(request):
        anfrage = json.loads(request.content)
        frage_id = next(iter(anfrage["questions"]))
        if fragen is not None:
            fragen.append((frage_id, anfrage["state"]))
        wert = {"answerable": answerable, "conflict": konflikt}[frage_id]
        return httpx.Response(200, json={"answers": {frage_id: {"noul": wert}}})

    return handler


def _datensatz(strom):
    return json.loads(strom.eskalationen.read_text(encoding="utf-8"))


def test_nur_abgeloeste_fassung_verweigert_ohne_llm(korpus_strom):
    with korpus_strom() as strom:
        events = strom.lauf("Frage?", [node(0.8, **ABGELOEST)])

        assert [e["type"] for e in events] == ["abstain", "sources", "done"]
        assert strom.llm.aufrufe == 0
        satz = _datensatz(strom)
        assert satz["reasons"] == ["OUTDATED_SOURCE"]
        assert satz["evidenz"][0]["gueltigkeit"] == "abgelaufen"


def test_abgeloest_plus_unbekannt_ist_outdated(korpus_strom):
    with korpus_strom() as strom:
        strom.lauf("Frage?", [node(0.8, **ABGELOEST), node(0.7, file_name="ohne.md")])

        assert _datensatz(strom)["reasons"] == ["OUTDATED_SOURCE"]


def test_mischfall_antwortet_nur_aus_der_gueltigen_fassung(korpus_strom):
    alt, neu = node(0.8, "alter Text", **ABGELOEST), node(0.7, "neuer Text", **GUELTIG)

    with korpus_strom() as strom:
        events = strom.lauf("Frage?", [alt, neu])

        assert [e["type"] for e in events] == ["status", "token", "sources", "done"]
        assert strom.kontexte == ["[1] neuer Text"]
        assert len(zeigt_quellen(events)) == 1
        assert not strom.eskalationen.exists()


def test_mischfall_mit_laya_nein_zeigt_nur_gueltige_fundstellen_und_eskaliert_alle(korpus_strom):
    alt, neu = node(0.8, "alter Text", **ABGELOEST), node(0.7, "neuer Text", **GUELTIG)
    fragen = []

    with korpus_strom(_laya(answerable=0.1, fragen=fragen)) as strom:
        events = strom.lauf("Frage?", [alt, neu])

        assert fragen == [("answerable", {"frage": "Frage?", "evidenz": "neuer Text"})]
        assert len(zeigt_quellen(events)) == 1
        satz = _datensatz(strom)
        assert satz["reasons"] == ["NOT_ANSWERABLE"]
        assert [e["gueltigkeit"] for e in satz["evidenz"]] == ["abgelaufen", "gueltig"]


def test_mischfall_mit_verworfener_antwort_eskaliert_alle_treffer(korpus_strom):
    alt, neu = node(0.8, "alter Text", **ABGELOEST), node(0.7, "neuer Text", **GUELTIG)

    with korpus_strom() as strom:
        strom.llm.teile = ["Die Frist beträgt 4 Stunden [1]."]
        events = strom.lauf("Frage?", [alt, neu])

        assert [e["type"] for e in events] == ["status", "abstain", "sources", "done"]
        assert len(zeigt_quellen(events)) == 1
        satz = _datensatz(strom)
        assert satz["reasons"] == ["UNGROUNDED_CLAIM"]
        assert [e["gueltigkeit"] for e in satz["evidenz"]] == ["abgelaufen", "gueltig"]


def test_verdraengung_durch_top_k_verweigert_und_ist_dokumentiert(korpus_strom):
    with korpus_strom() as strom:
        events = strom.lauf("Frage?", [node(0.8, **ABGELOEST) for _ in range(3)])

        assert events[0]["type"] == "abstain"
        assert _datensatz(strom)["reasons"] == ["OUTDATED_SOURCE"]


def test_zwei_fassungen_derselben_quelle_sind_mehrdeutig_auch_ohne_laya(korpus_strom):
    eins = node(0.8, gueltig_ab="2025-01-01", file_name="a.md")
    zwei = node(0.7, gueltig_ab="2026-01-01", file_name="b.md")

    with korpus_strom() as strom:
        strom.lauf("Frage?", [eins, zwei])

        assert _datensatz(strom)["reasons"] == ["AMBIGUOUS_VERSION"]
        assert strom.llm.aufrufe == 0


def _gesetz_und_auslegung():
    return [
        node(0.8, "Die Frist beträgt 72 Stunden.", quelle="Gesetz", file_name="g.md", segment="gesetz"),
        node(0.7, "Die Frist beträgt 24 Stunden.", quelle="Auslegung", file_name="a.md", segment="auslegung"),
    ]


def test_gesetz_gegen_auslegung_mit_fake_judge_ist_normkonflikt(korpus_strom):
    with korpus_strom(_laya(konflikt=0.9), schwelle=0.8) as strom:
        strom.lauf("Frage?", _gesetz_und_auslegung())

        satz = _datensatz(strom)
        assert satz["reasons"] == ["CONFLICTING_PROVISIONS"]
        assert satz["scores"]["konflikt"] == 0.9
        assert strom.llm.aufrufe == 0


def test_nicht_beantwortbar_spart_den_konflikt_aufruf(korpus_strom):
    fragen = []

    with korpus_strom(_laya(answerable=0.1, fragen=fragen), schwelle=0.8) as strom:
        strom.lauf("Frage?", _gesetz_und_auslegung())

        assert _datensatz(strom)["reasons"] == ["NOT_ANSWERABLE"]
        assert [f for f, _ in fragen] == ["answerable"]


def test_ausfall_beim_konflikt_ist_judge_unavailable(korpus_strom):
    def handler(request):
        if "conflict" in json.loads(request.content)["questions"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"answers": {"answerable": {"noul": 0.9}}})

    with korpus_strom(handler, schwelle=0.8) as strom:
        strom.lauf("Frage?", _gesetz_und_auslegung())

        assert _datensatz(strom)["reasons"] == ["JUDGE_UNAVAILABLE"]


def test_metrics_zaehlt_die_neuen_reasons(korpus_strom, monkeypatch):
    import metrics

    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())
    eins = node(0.8, gueltig_ab="2025-01-01", file_name="a.md")
    zwei = node(0.7, gueltig_ab="2026-01-01", file_name="b.md")

    with korpus_strom(_laya(konflikt=0.9), schwelle=0.8) as strom:
        strom.lauf("Frage?", [node(0.8, **ABGELOEST)])
        strom.lauf("Frage?", [eins, zwei])
        strom.lauf("Frage?", _gesetz_und_auslegung())
        daten = TestClient(strom.main.app).get("/metrics").json()

    assert daten["eskalationen_nach_reason"] == {
        "OUTDATED_SOURCE": 1,
        "AMBIGUOUS_VERSION": 1,
        "CONFLICTING_PROVISIONS": 1,
    }


def test_bestandsdokument_ohne_angaben_antwortet_wie_bisher(korpus_strom):
    with korpus_strom() as strom:
        events = strom.lauf("Was regelt DORA?", [node(0.8, file_name="alt.md")])

        assert [e["type"] for e in events] == ["status", "token", "sources", "done"]
        assert not strom.eskalationen.exists()
