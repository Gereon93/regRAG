import logging
from types import SimpleNamespace

import httpx
import pytest

from gate import Kaskade, Outcome, RetrievalScoreGate
from laya import LayaGate, baue_gate


def einstellungen(**ueberschreibe):
    werte = {
        "MIN_RETRIEVAL_SCORE": 0.62,
        "LAYA_URL": "",
        "LAYA_API_KEY": "",
        "LAYA_TIMEOUT": 30.0,
        "LAYA_MAX_LEN": 4096,
        "LAYA_LANG": "de",
        "LAYA_MIN_ANSWERABLE": 0.5,
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
