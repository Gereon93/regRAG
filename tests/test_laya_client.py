import json
import logging

import httpx
import pytest

from gate import AbstainReason, Outcome
from laya import JudgeFehler, LayaGate, SystemOneClient

KEY = "geheim-123"
ANTWORT_FRAGE = "answerable"


def node(text="Art. 5 DORA"):
    return type("N", (), {"score": 0.9, "text": text, "metadata": {}})()


def antwort(wert, **extra):
    return httpx.Response(200, json={"answers": {ANTWORT_FRAGE: {"type": "noul", "noul": wert}}, **extra})


def gate_mit(handler, key="", **optionen):
    client = SystemOneClient("http://laya.test:8000/", api_key=key, transport=httpx.MockTransport(handler))
    return LayaGate(client, schwelle_fuer=lambda segment: 0.5, **optionen)


def test_request_folgt_dem_system_one_schema():
    gesehen = []

    def handler(request):
        gesehen.append(request)
        return antwort(0.9)

    gate_mit(handler, max_len=2048, lang="de").entscheide("Was regelt Art. 5?", [node("Text A"), node("Text B")])

    request = gesehen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert request.url.path == "/v1/systemone"
    assert "authorization" not in request.headers
    assert list(body["state"]) == ["frage", "evidenz"]
    assert body["state"]["frage"] == "Was regelt Art. 5?"
    assert body["state"]["evidenz"] == "Text A\n\nText B"
    assert body["questions"][ANTWORT_FRAGE]["type"] == "noul"
    assert body["model"] == "multilingual"
    assert body["lang"] == "de"
    assert body["max_len"] == 2048


def test_bearer_nur_mit_key():
    gesehen = []

    def handler(request):
        gesehen.append(request)
        return antwort(0.9)

    gate_mit(handler, key=KEY).entscheide("f", [node()])

    assert gesehen[0].headers["authorization"] == f"Bearer {KEY}"


def test_evidenz_wird_auf_die_zeichengrenze_gekuerzt():
    gesehen = []

    def handler(request):
        gesehen.append(json.loads(request.content))
        return antwort(0.9)

    gate_mit(handler, max_evidenz_zeichen=100).entscheide("f", [node("x" * 500)])

    assert len(gesehen[0]["state"]["evidenz"]) == 100


@pytest.mark.parametrize(
    ("wert", "outcome"),
    [(0.499, Outcome.ABSTAIN), (0.5, Outcome.ANSWER), (0.501, Outcome.ANSWER), (0, Outcome.ABSTAIN), (1, Outcome.ANSWER)],
)
def test_schwelle_grenzfaelle(wert, outcome):
    d = gate_mit(lambda r: antwort(wert)).entscheide("f", [node()])

    assert d.outcome is outcome
    assert d.scores.answerable == wert
    if outcome is Outcome.ABSTAIN:
        assert d.reasons == (AbstainReason.NOT_ANSWERABLE,)


@pytest.mark.parametrize("status", [400, 401, 413, 422, 500, 503, 302])
def test_fehlerstatus_ist_judge_unavailable(status):
    d = gate_mit(lambda r: httpx.Response(status)).entscheide("f", [node()])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.JUDGE_UNAVAILABLE,)
    assert d.scores.answerable is None


@pytest.mark.parametrize("status", [400, 401, 413, 422])
def test_konfigurationsfehler_werden_als_error_geloggt(status, caplog):
    gate_mit(lambda r: httpx.Response(status)).entscheide("f", [node()])

    assert [r.levelno for r in caplog.records if "Laya" in r.getMessage()] == [logging.ERROR]


@pytest.mark.parametrize("status", [500, 503])
def test_serverfehler_werden_als_warning_geloggt(status, caplog):
    gate_mit(lambda r: httpx.Response(status)).entscheide("f", [node()])

    assert [r.levelno for r in caplog.records if "Laya" in r.getMessage()] == [logging.WARNING]


def test_timeout_und_verbindungsfehler_sind_judge_unavailable():
    def timeout(request):
        raise httpx.ReadTimeout("zu langsam", request=request)

    def verbindung(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, verbindung):
        d = gate_mit(handler).entscheide("f", [node()])
        assert d.reasons == (AbstainReason.JUDGE_UNAVAILABLE,)


@pytest.mark.parametrize(
    "inhalt",
    [
        b"",
        b"kein json",
        b"[]",
        b'{"answers": {}}',
        b'{"answers": {"answerable": {}}}',
        b'{"answers": {"answerable": {"noul": true}}}',
        b'{"answers": {"answerable": {"noul": "0.9"}}}',
        b'{"answers": {"answerable": {"noul": null}}}',
        b'{"answers": {"answerable": {"noul": NaN}}}',
        b'{"answers": {"answerable": {"noul": Infinity}}}',
        b'{"answers": {"answerable": {"noul": -0.1}}}',
        b'{"answers": {"answerable": {"noul": 1.1}}}',
    ],
)
def test_ungueltige_antworten_sind_judge_unavailable(inhalt):
    d = gate_mit(lambda r: httpx.Response(200, content=inhalt)).entscheide("f", [node()])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.JUDGE_UNAVAILABLE,)


def test_key_taucht_in_keinem_log_auf(caplog):
    caplog.set_level(logging.DEBUG)

    def timeout(request):
        raise httpx.ReadTimeout("zu langsam", request=request)

    handlers = [lambda r: antwort(0.9), lambda r: httpx.Response(401), lambda r: httpx.Response(503), timeout]
    for handler in handlers:
        gate_mit(handler, key=KEY).entscheide("f", [node()])

    assert KEY not in caplog.text


def test_gekuerzter_state_wird_gewarnt(caplog):
    gate_mit(lambda r: antwort(0.9, usage={"truncated": True})).entscheide("f", [node()])

    assert "gekürzt" in caplog.text


def test_fehlendes_usage_ist_unbekannt_und_kein_fehler(caplog):
    d = gate_mit(lambda r: antwort(0.9)).entscheide("f", [node()])

    assert d.outcome is Outcome.ANSWER
    assert "gekürzt" not in caplog.text


def test_abweichendes_routing_wird_gewarnt(caplog):
    gate_mit(lambda r: antwort(0.9, routing={"model": "english"})).entscheide("f", [node()])

    assert "english" in caplog.text


@pytest.mark.parametrize("extra", [{"usage": "unbekannt"}, {"routing": ["multilingual"]}, {"usage": 3, "routing": "x"}])
def test_ungueltige_metadaten_kippen_einen_gueltigen_score_nicht(extra):
    d = gate_mit(lambda r: antwort(0.9, **extra)).entscheide("f", [node()])

    assert d.outcome is Outcome.ANSWER
    assert d.scores.answerable == 0.9


def test_modell_aus_dem_gate_geht_in_den_request():
    gesehen = []

    def handler(request):
        gesehen.append(json.loads(request.content))
        return antwort(0.9, routing={"model": "mein-modell"})

    d = gate_mit(handler, modell="mein-modell", modell_streng=True).entscheide("f", [node()])

    assert gesehen[0]["model"] == "mein-modell"
    assert d.outcome is Outcome.ANSWER


def test_routing_abweichung_ohne_manifest_bleibt_eine_warnung(caplog):
    d = gate_mit(lambda r: antwort(0.9, routing={"model": "english"})).entscheide("f", [node()])

    assert d.outcome is Outcome.ANSWER
    assert "english" in caplog.text


def test_routing_abweichung_mit_modell_streng_ist_judge_unavailable(caplog):
    d = gate_mit(lambda r: antwort(0.99, routing={"model": "english"}), modell_streng=True).entscheide("f", [node()])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.JUDGE_UNAVAILABLE,)
    assert "english" in caplog.text


def test_routing_ohne_modellangabe_ist_auch_streng_kein_fehler():
    d = gate_mit(lambda r: antwort(0.9), modell_streng=True).entscheide("f", [node()])

    assert d.outcome is Outcome.ANSWER


def test_answerable_roh_liefert_den_rohwert_und_wirft_bei_fehlern():
    assert gate_mit(lambda r: antwort(0.37)).answerable_roh("f", [node()]) == 0.37
    with pytest.raises(JudgeFehler):
        gate_mit(lambda r: httpx.Response(503)).answerable_roh("f", [node()])
    with pytest.raises(JudgeFehler):
        gate_mit(lambda r: antwort(0.9, routing={"model": "english"}), modell_streng=True).answerable_roh("f", [node()])
