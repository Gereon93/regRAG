import json
import stat
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from gate import (
    ABSTAIN_ANTWORT_NICHT_VORGEMERKT,
    ABSTAIN_ANTWORT_VORGEMERKT,
    MAX_EVIDENZ_ZEICHEN,
    MAX_FRAGE_ZEICHEN,
    AbstainReason,
    GateDecision,
    GateScores,
    Outcome,
    RetrievalScoreGate,
    abstain_antwort,
    entscheide_und_eskaliere,
    eskalationsdatensatz,
    schreibe_eskalation,
)


def node(score, text="Art. 5 DORA", quelle="dora.md"):
    return SimpleNamespace(score=score, text=text, metadata={"quelle": quelle})


def test_abstain_ohne_reason_ist_unzulaessig():
    with pytest.raises(ValueError):
        GateDecision(Outcome.ABSTAIN, (), GateScores(relevance=0.1))


def test_answer_mit_reason_ist_unzulaessig():
    with pytest.raises(ValueError):
        GateDecision(Outcome.ANSWER, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.9))


def test_reasons_decken_alle_fachlichen_gruende_ab():
    assert {r.name for r in AbstainReason} == {
        "LOW_RELEVANCE",
        "NOT_ANSWERABLE",
        "OUTDATED_SOURCE",
        "CONFLICTING_PROVISIONS",
        "UNGROUNDED_CLAIM",
    }


def test_score_knapp_drunter_ist_abstain():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.619)])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert d.scores.relevance == 0.619


def test_score_genau_auf_schwelle_ist_answer():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.62)])

    assert d.outcome is Outcome.ANSWER
    assert d.reasons == ()


def test_score_knapp_drueber_ist_answer():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.621)])

    assert d.outcome is Outcome.ANSWER


def test_keine_treffer_ist_abstain_mit_reason():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert d.scores.relevance is None


def test_bester_treffer_zaehlt_nicht_die_reihenfolge():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.5), node(0.7)])

    assert d.outcome is Outcome.ANSWER
    assert d.scores.relevance == 0.7


def test_eskalationsdatensatz_enthaelt_frage_evidenz_scores_reasons_utc():
    jetzt = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.5))

    satz = eskalationsdatensatz("Frage?", [node(0.5, text="Text", quelle="q.md")], d, jetzt=jetzt)

    assert satz == {
        "zeitpunkt": "2026-10-27T12:00:00+00:00",
        "frage": "Frage?",
        "evidenz": [{"quelle": "q.md", "score": 0.5, "text": "Text"}],
        "scores": {"relevance": 0.5, "answerable": None},
        "reasons": ["LOW_RELEVANCE"],
    }


def test_eskalationsdatensatz_verweigert_naive_zeit():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores())

    with pytest.raises(ValueError):
        eskalationsdatensatz("f", [], d, jetzt=datetime.fromisoformat("2026-10-27T12:00:00"))


def test_eskalationsdatensatz_nur_bei_abstain():
    d = GateDecision(Outcome.ANSWER, (), GateScores(relevance=0.9))

    with pytest.raises(ValueError):
        eskalationsdatensatz("f", [node(0.9)], d)


def test_schreibe_eskalation_haengt_jsonl_zeilen_an(tmp_path):
    ziel = tmp_path / "unterordner" / "eskalationen.jsonl"
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    schreibe_eskalation(ziel, "erste?", [node(0.4)], d)
    schreibe_eskalation(ziel, "zweite?", [], d)

    zeilen = [json.loads(z) for z in ziel.read_text(encoding="utf-8").splitlines()]
    assert [z["frage"] for z in zeilen] == ["erste?", "zweite?"]
    assert zeilen[1]["evidenz"] == []


def test_schreibfehler_wird_geloggt_und_nicht_geworfen(tmp_path, caplog):
    blockiert = tmp_path / "datei"
    blockiert.write_text("x")
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    geschrieben = schreibe_eskalation(blockiert / "eskalationen.jsonl", "f", [], d)

    assert geschrieben is False
    assert "Eskalationsdatensatz" in caplog.text


def test_schreibe_eskalation_meldet_erfolg(tmp_path):
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    assert schreibe_eskalation(tmp_path / "e.jsonl", "f", [node(0.4)], d) is True


def test_nicht_serialisierbare_metadaten_kippen_die_verweigerung_nicht(tmp_path):
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    kaputt = SimpleNamespace(score=0.4, text="t", metadata={"quelle": object()})

    assert schreibe_eskalation(tmp_path / "e.jsonl", "f", [kaputt], d) is False


def test_eskalationsdatei_ist_nur_fuer_den_besitzer_lesbar(tmp_path):
    ziel = tmp_path / "e.jsonl"
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    schreibe_eskalation(ziel, "f", [], d)

    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600


def test_parallele_schreiber_verschachteln_keine_zeilen(tmp_path):
    ziel = tmp_path / "e.jsonl"
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    lange_evidenz = [node(0.4, text="x" * MAX_EVIDENZ_ZEICHEN) for _ in range(3)]

    def schreiber():
        for _ in range(25):
            schreibe_eskalation(ziel, "f", lange_evidenz, d)

    threads = [threading.Thread(target=schreiber) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    zeilen = ziel.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 200
    assert all(json.loads(z)["frage"] == "f" for z in zeilen)


def test_frage_und_evidenz_werden_im_datensatz_begrenzt():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    langer_text = node(0.4, text="y" * (MAX_EVIDENZ_ZEICHEN * 3))

    satz = eskalationsdatensatz("q" * (MAX_FRAGE_ZEICHEN * 3), [langer_text], d)

    assert len(satz["frage"]) == MAX_FRAGE_ZEICHEN
    assert len(satz["evidenz"][0]["text"]) == MAX_EVIDENZ_ZEICHEN


def test_treffer_ohne_quelle_liefert_none():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    ohne = SimpleNamespace(score=0.4, text="t", metadata={})

    assert eskalationsdatensatz("f", [ohne], d)["evidenz"][0]["quelle"] is None


def test_answer_schreibt_keinen_datensatz(tmp_path):
    ziel = tmp_path / "e.jsonl"

    pruefung = entscheide_und_eskaliere(RetrievalScoreGate(0.62), ziel, "f", [node(0.7)])

    assert not pruefung.verweigert
    assert pruefung.vorgemerkt is False
    assert not ziel.exists()


def test_abstain_wird_vorgemerkt_wenn_schreiben_gelingt(tmp_path):
    ziel = tmp_path / "e.jsonl"

    pruefung = entscheide_und_eskaliere(RetrievalScoreGate(0.62), ziel, "f", [node(0.5)])

    assert pruefung.verweigert
    assert pruefung.vorgemerkt is True
    assert ziel.exists()


def test_abstain_wird_nicht_als_vorgemerkt_gemeldet_wenn_schreiben_scheitert(tmp_path):
    blockiert = tmp_path / "datei"
    blockiert.write_text("x")

    pruefung = entscheide_und_eskaliere(RetrievalScoreGate(0.62), blockiert / "e.jsonl", "f", [node(0.5)])

    assert pruefung.verweigert
    assert pruefung.vorgemerkt is False


def test_abstain_antwort_verspricht_vormerkung_nur_wenn_sie_stattfand():
    assert abstain_antwort(True) == ABSTAIN_ANTWORT_VORGEMERKT
    assert abstain_antwort(False) == ABSTAIN_ANTWORT_NICHT_VORGEMERKT
    assert "vorgemerkt" not in ABSTAIN_ANTWORT_NICHT_VORGEMERKT
