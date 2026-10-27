import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from gate import (
    AbstainReason,
    GateDecision,
    GateScores,
    Outcome,
    RetrievalScoreGate,
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
        eskalationsdatensatz("f", [], d, jetzt=datetime(2026, 10, 27, 12, 0))


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

    schreibe_eskalation(blockiert / "eskalationen.jsonl", "f", [], d)

    assert "Eskalationsdatensatz" in caplog.text
