"""agent.retrieve in der CI: die schweren Abhängigkeiten sind gestubbt."""

import importlib
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config
from gate import GateDecision, GateScores, GueltigkeitsGate, Kaskade, Outcome


@pytest.fixture
def agent(monkeypatch, tmp_path):
    for name in ("openai", "langchain_core", "langchain_core.prompts", "langchain_openai", "langgraph", "langgraph.graph"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setitem(sys.modules, "rag", MagicMock())
    monkeypatch.delitem(sys.modules, "agent", raising=False)
    monkeypatch.setattr(config, "ESKALATION_DATEI", str(tmp_path / "e.jsonl"))
    modul = importlib.import_module("agent")
    yield modul
    sys.modules.pop("agent", None)


def test_retrieve_laesst_nur_die_genutzten_nodes_im_zustand(agent, monkeypatch):
    alt, neu = SimpleNamespace(), SimpleNamespace()
    entscheidung = GateDecision(Outcome.ANSWER, (), GateScores(), (neu,))
    monkeypatch.setattr(agent, "pruefe", lambda frage, nodes, stichtag=None: SimpleNamespace(entscheidung=entscheidung, vorgemerkt=False))
    monkeypatch.setattr(agent, "retriever", SimpleNamespace(retrieve=lambda frage: [alt, neu]))

    zustand = agent.retrieve({"frage": "f"})

    assert zustand["nodes"] == [neu]


def test_ground_eskaliert_mit_allen_abgerufenen_treffern(agent, monkeypatch):
    alt, neu = SimpleNamespace(), SimpleNamespace()
    entscheidung = GateDecision(Outcome.ANSWER, (), GateScores(), (neu,))
    gesehen = []
    monkeypatch.setattr(agent, "pruefe", lambda frage, nodes, stichtag=None: SimpleNamespace(entscheidung=entscheidung, vorgemerkt=False))
    monkeypatch.setattr(agent, "retriever", SimpleNamespace(retrieve=lambda frage: [alt, neu]))
    monkeypatch.setattr(
        agent,
        "pruefe_grounding",
        lambda frage, nodes, antwort, vorherige, stichtag=None: gesehen.append(nodes) or SimpleNamespace(verweigert=False),
    )
    zustand = agent.retrieve({"frage": "f"})
    zustand.update(antwort="A", llm_fehler=False)

    agent.ground(zustand)

    assert gesehen == [[alt, neu]]
    assert zustand["nodes"] == [neu]


def test_lies_argumente_ohne_stichtag_nimmt_die_beispielfrage(agent):
    argumente = agent.lies_argumente([])

    assert argumente.frage == agent.BEISPIEL_FRAGE
    assert argumente.stichtag is None


def test_lies_argumente_mit_stichtag(agent):
    argumente = agent.lies_argumente(["Was galt damals?", "--stichtag", "2024-06-01"])

    assert argumente.frage == "Was galt damals?"
    assert argumente.stichtag == date(2024, 6, 1)


@pytest.mark.parametrize("wert", ["2024-13-01", "20240601", "01.06.2024", ""])
def test_lies_argumente_lehnt_unsinnigen_stichtag_ab(agent, wert, capsys):
    with pytest.raises(SystemExit) as fehler:
        agent.lies_argumente(["--stichtag", wert])

    assert fehler.value.code == 2
    assert "--stichtag" in capsys.readouterr().err


def test_main_reicht_den_stichtag_in_den_graphen_und_nennt_ihn(agent, monkeypatch, capsys):
    zustaende = []
    graph = SimpleNamespace(invoke=lambda s: zustaende.append(s) or {"antwort": "A", "nodes": []})
    monkeypatch.setattr(agent, "app", graph)

    agent.main(["F?", "--stichtag", "2024-06-01"])

    assert zustaende == [{"frage": "F?", "stichtag": date(2024, 6, 1)}]
    assert "Stichtag: 2024-06-01" in capsys.readouterr().out


def _fassung(text, **gueltigkeit):
    return SimpleNamespace(score=0.9, text=text, metadata={"quelle": "dora", "file_name": f"{text}.md", **gueltigkeit})


def test_retrieve_und_ground_pruefen_mit_dem_stichtag_gegen_das_echte_gate(agent, monkeypatch):
    alt, neu = _fassung("alt", gueltig_bis="2025-01-16"), _fassung("neu", gueltig_ab="2025-01-17")
    monkeypatch.setattr(agent, "gate", Kaskade([GueltigkeitsGate(uhr=lambda: date(2026, 1, 1))]))
    monkeypatch.setattr(agent, "retriever", SimpleNamespace(retrieve=lambda frage: [alt, neu]))

    zustand = agent.retrieve({"frage": "f", "stichtag": date(2024, 6, 1)})
    zustand.update(antwort="Ohne Beleg.", llm_fehler=False)
    agent.ground(zustand)

    assert zustand["entscheidung"].evidenz == (alt,)
    satz = json.loads(Path(config.ESKALATION_DATEI).read_text(encoding="utf-8"))
    assert satz["reasons"] == ["UNGROUNDED_CLAIM"]
    assert satz["stichtag"] == "2024-06-01"


def test_retriever_holt_mit_ueberabruf_und_kuerzt_auf_drei(agent):
    import gueltigkeit

    assert isinstance(agent.retriever, gueltigkeit.UeberabrufRetriever)
    assert agent.retriever.top_k == 3
    sys.modules["rag"].index.as_retriever.assert_called_once_with(similarity_top_k=3 * config.UEBERABRUF_FAKTOR)
