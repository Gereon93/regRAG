"""agent.retrieve in der CI: die schweren Abhängigkeiten sind gestubbt."""

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config
from gate import GateDecision, GateScores, Outcome


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
    monkeypatch.setattr(agent, "pruefe", lambda frage, nodes: SimpleNamespace(entscheidung=entscheidung, vorgemerkt=False))
    monkeypatch.setattr(agent, "retriever", SimpleNamespace(retrieve=lambda frage: [alt, neu]))

    zustand = agent.retrieve({"frage": "f"})

    assert zustand["nodes"] == [neu]


def test_ground_eskaliert_mit_allen_abgerufenen_treffern(agent, monkeypatch):
    alt, neu = SimpleNamespace(), SimpleNamespace()
    entscheidung = GateDecision(Outcome.ANSWER, (), GateScores(), (neu,))
    gesehen = []
    monkeypatch.setattr(agent, "pruefe", lambda frage, nodes: SimpleNamespace(entscheidung=entscheidung, vorgemerkt=False))
    monkeypatch.setattr(agent, "retriever", SimpleNamespace(retrieve=lambda frage: [alt, neu]))
    monkeypatch.setattr(
        agent,
        "pruefe_grounding",
        lambda frage, nodes, antwort, vorherige: gesehen.append(nodes) or SimpleNamespace(verweigert=False),
    )
    zustand = agent.retrieve({"frage": "f"})
    zustand.update(antwort="A", llm_fehler=False)

    agent.ground(zustand)

    assert gesehen == [[alt, neu]]
    assert zustand["nodes"] == [neu]
