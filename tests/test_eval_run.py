"""evaluation.run in der CI: deepeval und agent sind gestubbt, gemessen wird nur die Auswertungslogik."""

import importlib
import os
import runpy
import sys
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest

import config

FAELLE = [
    {"frage": "Was regelt Art. 28?", "erwartet": "answer"},
    {"frage": "Wie wird das Wetter?", "erwartet": "abstain"},
    {"frage": "Wer meldet Vorfälle?", "erwartet": "answer"},
]


class FakeTestCase:
    def __init__(self, **felder):
        self.felder = felder


class FakeMetric:
    """Bewertet nach Länge der Antwort, damit Scores pro Fall unterscheidbar sind."""

    instanzen: ClassVar[list] = []

    def __init__(self, threshold, model, async_mode):
        self.threshold, self.model, self.async_mode = threshold, model, async_mode
        self.gemessen = []
        self.score = None
        FakeMetric.instanzen.append(self)

    def measure(self, testfall):
        self.gemessen.append(testfall)
        self.score = len(testfall.felder["actual_output"]) / 10


def _stub(name, **attribute):
    modul = ModuleType(name)
    modul.__dict__.update(attribute)
    return modul


def _entscheidung(verweigert):
    return SimpleNamespace(verweigert=verweigert)


@pytest.fixture
def agent_stub():
    """Retriever liefert die Frage als Knoten; das Gate verweigert alles mit 'Wetter'."""
    retriever = SimpleNamespace(
        retrieve=lambda frage: [SimpleNamespace(text=f"Kontext zu {frage}")]
    )
    gate = SimpleNamespace(
        entscheide=lambda frage, nodes: _entscheidung("Wetter" in frage)
    )
    antworten = {
        "Was regelt Art. 28?": "Drittanbieter",
        "Wer meldet Vorfälle?": "Finanzunternehmen",
    }

    def invoke(eingabe):
        frage = eingabe["frage"]
        return {
            "entscheidung": _entscheidung(frage not in antworten),
            "antwort": antworten.get(frage, ""),
            "nodes": [SimpleNamespace(text=f"Kontext zu {frage}")],
        }

    return SimpleNamespace(
        app=SimpleNamespace(invoke=invoke), gate=gate, retriever=retriever
    )


@pytest.fixture
def stubs(monkeypatch, agent_stub):
    FakeMetric.instanzen = []
    module = {
        "deepeval": _stub("deepeval"),
        "deepeval.metrics": _stub("deepeval.metrics", FaithfulnessMetric=FakeMetric),
        "deepeval.test_case": _stub("deepeval.test_case", LLMTestCase=FakeTestCase),
        "agent": agent_stub,
        "evaluation.judge": _stub("evaluation.judge", JudgeLLM=lambda: "judge"),
    }
    for name, modul in module.items():
        monkeypatch.setitem(sys.modules, name, modul)
    monkeypatch.delitem(sys.modules, "evaluation.run", raising=False)
    return module


@pytest.fixture
def run(stubs, monkeypatch):
    modul = importlib.import_module("evaluation.run")
    monkeypatch.setattr(modul, "FAELLE", FAELLE)
    yield modul
    sys.modules.pop("evaluation.run", None)


def test_guard_report_zaehlt_korrekte_entscheidungen(run, capsys):
    assert run.guard_report() == 3

    ausgabe = capsys.readouterr().out
    assert "ok abstain  erwartet=abstain" in ausgabe
    assert "Guard: 3/3 Entscheidungen korrekt" in ausgabe


def test_guard_report_markiert_falsche_entscheidung(run, monkeypatch, capsys):
    monkeypatch.setattr(
        run.gate, "entscheide", lambda frage, nodes: _entscheidung(True)
    )

    assert run.guard_report() == 1

    ausgabe = capsys.readouterr().out
    assert "!! abstain  erwartet=answer" in ausgabe
    assert "Guard: 1/3 Entscheidungen korrekt" in ausgabe


def test_faithfulness_misst_nur_beantwortete_antwortbare_faelle(run, capsys):
    scores = run.faithfulness_report("judge", schwelle=0.5, faelle=FAELLE)

    assert scores == pytest.approx([1.3, 1.7])
    (metric,) = FakeMetric.instanzen
    assert (metric.threshold, metric.model, metric.async_mode) == (0.5, "judge", False)
    erster = metric.gemessen[0].felder
    assert erster == {
        "input": "Was regelt Art. 28?",
        "actual_output": "Drittanbieter",
        "retrieval_context": ["Kontext zu Was regelt Art. 28?"],
    }
    assert "Faithfulness Ø = 1.50 über 2 Antworten" in capsys.readouterr().out


def test_faithfulness_ueberspringt_verweigerte_antworten(run, capsys):
    faelle = [{"frage": "Unbekannt?", "erwartet": "answer"}]

    assert run.faithfulness_report("judge", faelle=faelle) == []

    ausgabe = capsys.readouterr().out
    assert "-- abstained (kein Faithfulness-Test): Unbekannt?" in ausgabe
    assert "Faithfulness Ø" not in ausgabe
    assert FakeMetric.instanzen[0].gemessen == []


def test_hauptprogramm_schreibt_keine_eskalationen_und_laeuft_beide_berichte(
    stubs, monkeypatch, capsys
):
    monkeypatch.setitem(
        sys.modules, "evaluation.dataset", _stub("evaluation.dataset", FAELLE=FAELLE)
    )
    monkeypatch.setattr(config, "ESKALATION_DATEI", "eskalationen.jsonl")

    runpy.run_module("evaluation.run", run_name="__main__")

    assert config.ESKALATION_DATEI == os.devnull
    ausgabe = capsys.readouterr().out
    assert ausgabe.index("=== Guard-Verhalten ===") < ausgabe.index(
        "=== Faithfulness ==="
    )
    assert "Guard: 3/3" in ausgabe
    assert FakeMetric.instanzen[0].model == "judge"
