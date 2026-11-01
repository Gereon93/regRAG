import logging
from types import SimpleNamespace

import httpx
import pytest

from gate import AbstainReason, Kaskade, Outcome, RetrievalScoreGate, kalibriere
from laya import LayaGate, SystemOneClient, baue_gate
from manifest import Manifest, schreibe_manifest


def einstellungen(**ueberschreibe):
    werte = {
        "MIN_RETRIEVAL_SCORE": 0.62,
        "LAYA_URL": "",
        "LAYA_API_KEY": "",
        "LAYA_TIMEOUT": 30.0,
        "LAYA_MAX_LEN": 4096,
        "LAYA_LANG": "de",
        "LAYA_MIN_ANSWERABLE": 0.5,
        "LAYA_MODELL": "multilingual",
        "GATE_MANIFEST": "",
    }
    return SimpleNamespace(**{**werte, **ueberschreibe})


def test_ohne_laya_url_bleibt_es_beim_score_guard():
    assert isinstance(baue_gate(einstellungen()), RetrievalScoreGate)


def test_mit_laya_url_laeuft_der_score_guard_vor_laya():
    gate = baue_gate(einstellungen(LAYA_URL="http://localhost:8000"))

    assert isinstance(gate, Kaskade)
    assert [type(g) for g in gate.gates] == [RetrievalScoreGate, LayaGate]


def test_laya_wird_bei_schwacher_relevanz_nicht_aufgerufen():
    aufrufe = []

    def handler(request):
        aufrufe.append(request)
        return httpx.Response(200, json={"answers": {"answerable": {"noul": 0.9}}})

    gate = baue_gate(einstellungen(LAYA_URL="http://localhost:8000"), transport=httpx.MockTransport(handler))
    schwach = SimpleNamespace(score=0.3, text="t", metadata={})

    d = gate.entscheide("f", [schwach])

    assert d.outcome is Outcome.ABSTAIN
    assert aufrufe == []


def test_warnung_bei_nicht_lokalem_laya_server(caplog):
    baue_gate(einstellungen(LAYA_URL="https://jev.example.com"))

    assert "nicht lokal" in caplog.text


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://host.docker.internal:8000"])
def test_keine_warnung_bei_lokalem_laya_server(url, caplog):
    caplog.set_level(logging.WARNING)

    baue_gate(einstellungen(LAYA_URL=url))

    assert "nicht lokal" not in caplog.text


URL = "http://localhost:8000"
SCHWELLEN = {"verordnung": 0.4, "gesetz": 0.6}


def seg_node(segment=None, score=0.9):
    metadata = {} if segment is None else {"segment": segment}
    return SimpleNamespace(score=score, text="t", metadata=metadata)


def laya(wert, schwelle_fuer, **optionen):
    def handler(request):
        return httpx.Response(200, json={"answers": {"answerable": {"noul": wert}}})

    client = SystemOneClient("http://laya.test:8000", transport=httpx.MockTransport(handler))
    return LayaGate(client, schwelle_fuer, **optionen)


def nach_segment(segment):
    return {**SCHWELLEN}.get(segment, 0.9)


@pytest.mark.parametrize(("segment", "schwelle"), [("verordnung", 0.4), ("gesetz", 0.6)])
def test_schwelle_gilt_pro_bekanntem_segment(segment, schwelle):
    knapp_drunter = laya(schwelle - 0.01, nach_segment).entscheide("f", [seg_node(segment)])
    genau = laya(schwelle, nach_segment).entscheide("f", [seg_node(segment)])
    knapp_drueber = laya(schwelle + 0.01, nach_segment).entscheide("f", [seg_node(segment)])

    assert knapp_drunter.outcome is Outcome.ABSTAIN
    assert knapp_drunter.reasons == (AbstainReason.NOT_ANSWERABLE,)
    assert genau.outcome is Outcome.ANSWER
    assert knapp_drueber.outcome is Outcome.ANSWER


@pytest.mark.parametrize(
    "nodes",
    [
        [seg_node("unbekannt")],
        [seg_node("verordnung"), seg_node("gesetz")],
        [seg_node()],
        [seg_node("verordnung"), seg_node()],
        [seg_node("quatsch")],
        [],
    ],
    ids=["unbekannt", "gemischt", "ohne_segment", "teils_ohne", "ungueltig", "leer"],
)
def test_unbekannt_gemischt_und_fehlend_nutzen_den_fallback(nodes):
    abgefragt = []

    def schwelle_fuer(segment):
        abgefragt.append(segment)
        return nach_segment(segment)

    drunter = laya(0.89, schwelle_fuer).entscheide("f", nodes)
    drueber = laya(0.9, schwelle_fuer).entscheide("f", nodes)

    assert abgefragt == ["unbekannt", "unbekannt"]
    assert drunter.outcome is Outcome.ABSTAIN
    assert drueber.outcome is Outcome.ANSWER


def test_kalibrierung_wirkt_vor_dem_vergleich_und_answerable_bleibt_roh():
    d = laya(0.9, lambda segment: 0.5, kalibriere_score=lambda p: p / 2).entscheide("f", [seg_node("gesetz")])
    ok = laya(1.0, lambda segment: 0.5, kalibriere_score=lambda p: p / 2).entscheide("f", [seg_node("gesetz")])

    assert d.outcome is Outcome.ABSTAIN
    assert d.scores.answerable == 0.9
    assert ok.outcome is Outcome.ANSWER
    assert ok.scores.answerable == 1.0


def test_manifest_leer_behaelt_das_alte_verhalten():
    gate = baue_gate(einstellungen(LAYA_URL=URL, LAYA_MIN_ANSWERABLE=0.7))
    laya_gate = gate.gates[1]

    assert laya_gate.modell == "multilingual"
    assert laya_gate.modell_streng is False
    assert laya_gate.kalibriere_score(0.3) == 0.3
    assert {laya_gate.schwelle_fuer(s) for s in ("gesetz", "unbekannt")} == {0.7}


def test_manifest_ohne_laya_url_ist_ein_startfehler(tmp_path):
    with pytest.raises(ValueError, match="LAYA_URL"):
        baue_gate(einstellungen(GATE_MANIFEST=str(tmp_path / "gate.json")))


def test_fehlendes_oder_ungueltiges_manifest_faellt_nicht_auf_0_5_zurueck(tmp_path):
    kaputt = tmp_path / "kaputt.json"
    kaputt.write_text("{", encoding="utf-8")

    for pfad in (tmp_path / "fehlt.json", kaputt):
        with pytest.raises(ValueError, match="anifest"):
            baue_gate(einstellungen(LAYA_URL=URL, GATE_MANIFEST=str(pfad)))


def schreibe_fixture(tmp_path, **ueberschreibe):
    werte = {
        "checkpoint_modell": "multilingual",
        "checkpoint_revision": "abc123",
        "eval_version": "sha256:" + "a" * 64,
        "min_retrieval_score": 0.62,
        "temperatur": 2.0,
        "schwellen": {"verordnung": 0.4, "gesetz": 0.6},
        "fallback_schwelle": 0.7,
        "kosten": {"c_fehlantwort": 5.0, "c_eskalation": 1.0},
        "erzeugt": "2026-11-01T00:00:00+00:00",
    }
    return schreibe_manifest(Manifest(**{**werte, **ueberschreibe}), tmp_path)


def kaskade_mit_manifest(tmp_path, wert, routing=None, **manifest):
    pfad = schreibe_fixture(tmp_path, **manifest)
    gesehen = []

    def handler(request):
        gesehen.append(request)
        extra = {} if routing is None else {"routing": {"model": routing}}
        return httpx.Response(200, json={"answers": {"answerable": {"noul": wert}}, **extra})

    gate = baue_gate(einstellungen(LAYA_URL=URL, GATE_MANIFEST=str(pfad)), transport=httpx.MockTransport(handler))
    return gate, gesehen


def test_gueltiges_manifest_baut_die_kaskade_mit_segmentschwellen(tmp_path):
    gate, _ = kaskade_mit_manifest(tmp_path, 0.5)

    assert [type(g) for g in gate.gates] == [RetrievalScoreGate, LayaGate]
    assert gate.gates[1].modell_streng is True
    assert gate.gates[1].schwelle_fuer("verordnung") == 0.4
    assert gate.gates[1].schwelle_fuer("unbekannt") == 0.7


def test_kaskade_mit_manifest_kalibriert_und_waehlt_die_schwelle_pro_segment(tmp_path):
    roh = 0.8
    kalibriert = kalibriere(roh, 2.0)
    assert 0.6 < kalibriert < 0.7
    gate, gesehen = kaskade_mit_manifest(tmp_path, roh)

    verordnung = gate.entscheide("f", [seg_node("verordnung")])
    gesetz = gate.entscheide("f", [seg_node("gesetz")])
    unbekannt = gate.entscheide("f", [seg_node("unbekannt")])
    gemischt = gate.entscheide("f", [seg_node("gesetz"), seg_node("verordnung")])
    ohne = gate.entscheide("f", [seg_node()])

    assert [d.outcome for d in (verordnung, gesetz, unbekannt, gemischt, ohne)] == [
        Outcome.ANSWER,
        Outcome.ANSWER,
        Outcome.ABSTAIN,
        Outcome.ABSTAIN,
        Outcome.ABSTAIN,
    ]
    assert verordnung.scores.answerable == roh
    assert len(gesehen) == 5


def test_kaskade_mit_manifest_stoppt_bei_schwacher_relevanz_ohne_laya_aufruf(tmp_path):
    gate, gesehen = kaskade_mit_manifest(tmp_path, 0.99)

    d = gate.entscheide("f", [seg_node("gesetz", score=0.3)])

    assert d.outcome is Outcome.ABSTAIN
    assert gesehen == []


def test_kaskade_mit_manifest_aber_fremdem_checkpoint_enthaelt_sich(tmp_path):
    gate, _ = kaskade_mit_manifest(tmp_path, 0.99, routing="english")

    d = gate.entscheide("f", [seg_node("gesetz")])

    assert d.outcome is Outcome.ABSTAIN
    assert AbstainReason.JUDGE_UNAVAILABLE in d.reasons


def test_kaskade_mit_manifest_und_judge_ausfall_enthaelt_sich(tmp_path):
    pfad = schreibe_fixture(tmp_path)
    gate = baue_gate(
        einstellungen(LAYA_URL=URL, GATE_MANIFEST=str(pfad)),
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )

    d = gate.entscheide("f", [seg_node("verordnung")])

    assert d.outcome is Outcome.ABSTAIN
    assert AbstainReason.JUDGE_UNAVAILABLE in d.reasons
