import sys
from datetime import date
from types import SimpleNamespace

import config
import gueltigkeit
from evaluation import calibrate
from evaluation.calibrate import diagnose
from evaluation.evalset import Kosten

KOSTEN = Kosten(falschantwort=10.0, eskalation=1.0)


def zeile(erwartet, top, vorpruefung=None):
    return {
        "id": f"{erwartet}-{top}",
        "segment": "verordnung",
        "kategorie": "k",
        "erwartet": erwartet,
        "top": top,
        "vorpruefung": vorpruefung,
    }


def test_diagnose_misst_spannen_und_trennluecke():
    zeilen = [zeile("answer", 0.9), zeile("answer", 0.7), zeile("abstain", 0.5), zeile("abstain", 0.3)]
    d = diagnose(zeilen, KOSTEN)
    assert d["antwortbar"] == (0.7, 0.9)
    assert d["abstain"] == (0.3, 0.5)
    assert d["trennluecke"] == (0.5, 0.7)
    assert 0.5 < d["schwelle"] <= 0.7
    assert (d["korrekt"], d["n"], d["kosten"]) == (4, 4, 0.0)


def test_diagnose_waehlt_nach_kosten_nicht_nach_accuracy():
    """Accuracy nähme 0.825 (3/4); bei Falschantwort = 10 ist eine Eskalation mehr billiger."""
    zeilen = [zeile("answer", 0.9), zeile("answer", 0.8), zeile("abstain", 0.85), zeile("abstain", 0.4)]
    d = diagnose(zeilen, KOSTEN)
    assert d["schwelle"] > 0.85
    assert d["kosten"] == 1.0


def test_diagnose_zaehlt_faelle_ohne_treffer_als_verweigert():
    d = diagnose([zeile("answer", None), zeile("abstain", 0.4)], KOSTEN)
    assert d["ohne_treffer"] == 1
    assert d["antwortbar"] is None and d["trennluecke"] is None
    assert d["schwelle"] > 0.4
    assert d["kosten"] == 1.0


def test_main_nutzt_eval_set_und_kosten_aus_der_config(monkeypatch, capsys):
    faelle = [
        {"id": "a", "frage": "A?", "erwartet": "answer", "segment": "verordnung", "kategorie": "beantwortbar"},
        {"id": "b", "frage": "B?", "erwartet": "abstain", "segment": "verordnung", "kategorie": "grenzfall"},
    ]
    scores = {"A?": 0.6, "B?": 0.65}
    retriever = SimpleNamespace(retrieve=lambda frage: [SimpleNamespace(score=scores[frage], metadata={})])
    index = SimpleNamespace(as_retriever=lambda similarity_top_k: retriever)
    monkeypatch.setitem(sys.modules, "rag", SimpleNamespace(index=index))
    monkeypatch.setattr(calibrate, "lade_faelle", lambda pfad: faelle)
    monkeypatch.setattr(config, "KOSTEN_FALSCHANTWORT", 0.5)
    monkeypatch.setattr(config, "KOSTEN_ESKALATION", 1.0)
    monkeypatch.setattr(gueltigkeit, "heute_berlin", lambda: date(2026, 6, 15))
    calibrate.main()
    out = capsys.readouterr().out
    assert "Stichtag: 2026-06-15" in out
    assert "grenzfall" in out
    assert "Falschantwort=0.5" in out
    # Falschantwort billiger als Eskalation: B mitzubeantworten ist günstiger als A zu eskalieren.
    assert "Kostenminimale Schwelle: 0.600" in out


def test_diagnose_trennt_vorab_verweigerte_von_faellen_ohne_treffer():
    """Abgelöste Fassung oder mehrdeutige Fassung ist kein leeres Retrieval (#54)."""
    zeilen = [
        zeile("answer", None, "LOW_RELEVANCE"),
        zeile("answer", None, "OUTDATED_SOURCE"),
        zeile("abstain", None, "AMBIGUOUS_VERSION"),
        zeile("abstain", 0.4),
    ]
    d = diagnose(zeilen, KOSTEN)
    assert d["ohne_treffer"] == 1
    assert d["vorab_verweigert"] == {"OUTDATED_SOURCE": 1, "AMBIGUOUS_VERSION": 1}
