import sys
from types import SimpleNamespace

import pytest

from evaluation import befolgung
from gate import AbstainReason, GateDecision, GateScores, Outcome

ANSWER = GateDecision(Outcome.ANSWER, (), GateScores())
VERWORFEN = GateDecision(Outcome.ABSTAIN, (AbstainReason.UNGROUNDED_CLAIM,), GateScores())


@pytest.mark.parametrize(
    ("ergebnis", "bestanden", "text"),
    [
        ({"entscheidung": ANSWER, "llm_fehler": False}, 1, "bestanden"),
        ({"entscheidung": ANSWER, "llm_fehler": True}, 0, "LLM_FEHLER"),
        ({"entscheidung": ANSWER}, 1, "bestanden"),
        ({"entscheidung": VERWORFEN, "llm_fehler": False}, 0, "UNGROUNDED_CLAIM"),
    ],
)
def test_nur_geprueft_bestandene_antworten_zaehlen(monkeypatch, capsys, ergebnis, bestanden, text):
    app = SimpleNamespace(invoke=lambda eingabe: ergebnis)
    monkeypatch.setitem(sys.modules, "agent", SimpleNamespace(app=app))
    monkeypatch.setattr(befolgung, "ANTWORTBAR", ["Frage?"])
    monkeypatch.setattr(befolgung.config, "ESKALATION_DATEI", "unveraendert")

    befolgung.main([])

    ausgabe = capsys.readouterr().out
    assert text in ausgabe
    assert f"Befolgung: {bestanden} von 1" in ausgabe
