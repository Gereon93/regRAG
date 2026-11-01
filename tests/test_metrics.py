import sys
import threading

import pytest

import gate
import metrics
from gate import AbstainReason, GateDecision, GateScores, Outcome, RetrievalScoreGate
from metrics import Metriken
from types import SimpleNamespace


def antwort(**scores):
    return GateDecision(Outcome.ANSWER, (), GateScores(**scores))


def abstain(*reasons, **scores):
    return GateDecision(Outcome.ABSTAIN, tuple(reasons), GateScores(**scores))


def test_leer_hat_keine_rate_und_utc_start():
    s = Metriken().schnappschuss()

    assert s["entscheidungen_gesamt"] == 0
    assert s["eskalationen_gesamt"] == 0
    assert s["eskalationsrate"] is None
    assert s["eskalationen_nach_reason"] == {}
    assert s["nach_segment"] == {}
    assert s["seit"].endswith("+00:00")


def test_zaehler_pro_reason_und_segment():
    m = Metriken()
    m.zaehle(antwort(relevance=0.9), "gesetz")
    m.zaehle(abstain(AbstainReason.LOW_RELEVANCE, AbstainReason.OUTDATED_SOURCE), "gesetz")
    m.zaehle(abstain(AbstainReason.LOW_RELEVANCE), "unbekannt")

    s = m.schnappschuss()

    assert s["entscheidungen_gesamt"] == 3
    assert s["eskalationen_gesamt"] == 2
    assert s["eskalationsrate"] == pytest.approx(2 / 3)
    assert s["eskalationen_nach_reason"] == {"LOW_RELEVANCE": 2, "OUTDATED_SOURCE": 1}
    assert s["nach_segment"]["gesetz"] == {"entscheidungen": 2, "eskalationen": 1, "eskalationsrate": 0.5}
    assert s["nach_segment"]["unbekannt"] == {"entscheidungen": 1, "eskalationen": 1, "eskalationsrate": 1.0}


@pytest.mark.parametrize("feld", ["answerable", "relevance"])
@pytest.mark.parametrize(
    ("wert", "bucket"),
    [
        (0.0, "0.0-0.1"),
        (0.1, "0.1-0.2"),
        (0.95, "0.9-1.0"),
        (1.0, "0.9-1.0"),
        (-0.2, "unter_0"),
        (1.2, "ueber_1"),
        (None, "none"),
        (float("nan"), "none"),
        (float("inf"), "ueber_1"),
        (float("-inf"), "unter_0"),
    ],
)
def test_histogramm_grenzen(feld, wert, bucket):
    m = Metriken()
    m.zaehle(antwort(**{feld: wert}), "gesetz")

    h = m.schnappschuss()["histogramme"][feld]

    assert h[bucket] == 1
    assert sum(h.values()) == 1
    assert list(h)[:10] == [f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10)]


def test_histogramm_hat_alle_schluessel_auch_leer():
    h = Metriken().schnappschuss()["histogramme"]["answerable"]

    assert set(h) == {f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10)} | {"unter_0", "ueber_1", "none"}


@pytest.fixture
def enges_threadfenster():
    alt = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(alt)


def test_threads_zaehlen_exakt(enges_threadfenster):
    m = Metriken()

    def arbeit():
        for _ in range(2000):
            m.zaehle(abstain(AbstainReason.LOW_RELEVANCE, relevance=0.3), "gesetz")

    threads = [threading.Thread(target=arbeit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = m.schnappschuss()
    assert s["entscheidungen_gesamt"] == 16000
    assert s["eskalationen_nach_reason"] == {"LOW_RELEVANCE": 16000}
    assert s["nach_segment"]["gesetz"]["entscheidungen"] == 16000
    assert sum(s["histogramme"]["relevance"].values()) == 16000


def test_zaehle_und_schnappschuss_nehmen_den_lock():
    class SpyLock:
        def __init__(self):
            self.betreten = 0

        def __enter__(self):
            self.betreten += 1

        def __exit__(self, *a):
            return False

    m = Metriken()
    m._lock = spy = SpyLock()

    m.zaehle(antwort(), "gesetz")
    assert spy.betreten == 1
    m.schnappschuss()
    assert spy.betreten == 2


def test_nan_score_wird_trotzdem_gezaehlt(monkeypatch, tmp_path):
    m = Metriken()
    monkeypatch.setattr(metrics, "METRIKEN", m)
    n = SimpleNamespace(score=float("nan"), text="t", metadata={"segment": "gesetz"})

    gate.entscheide_und_eskaliere(RetrievalScoreGate(0.62), tmp_path / "e.jsonl", "f", [n])

    s = m.schnappschuss()
    assert s["entscheidungen_gesamt"] == 1
    assert s["nach_segment"]["gesetz"]["entscheidungen"] == 1
    assert s["histogramme"]["relevance"]["none"] == 1


def test_gate_zaehlt_einmal_mit_segment(monkeypatch, tmp_path):
    m = Metriken()
    monkeypatch.setattr(metrics, "METRIKEN", m)
    n = SimpleNamespace(score=0.5, text="t", metadata={"segment": "verordnung"})

    gate.entscheide_und_eskaliere(RetrievalScoreGate(0.62), tmp_path / "e.jsonl", "f", [n])

    s = m.schnappschuss()
    assert s["nach_segment"]["verordnung"]["eskalationen"] == 1
    assert s["histogramme"]["answerable"]["none"] == 1


def test_fehler_beim_zaehlen_aendert_entscheidung_nicht(monkeypatch, tmp_path, caplog):
    class Kaputt:
        def zaehle(self, *a):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(metrics, "METRIKEN", Kaputt())
    n = SimpleNamespace(score=0.5, text="t", metadata={})

    pruefung = gate.entscheide_und_eskaliere(RetrievalScoreGate(0.62), tmp_path / "e.jsonl", "f", [n])

    assert pruefung.verweigert
    assert pruefung.vorgemerkt
    assert "kaputt" in caplog.text
