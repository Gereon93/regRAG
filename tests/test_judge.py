"""JudgeLLM in der CI: deepeval und langchain_openai sind gestubbt, geprüft wird die Weiterleitung."""

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

import config


class FakeChatOpenAI:
    """Spiegelt den Prompt zurück; mit Schema wird das Schema mit dem Prompt gebaut."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def invoke(self, prompt):
        return SimpleNamespace(content=f"sync:{prompt}")

    async def ainvoke(self, prompt):
        return SimpleNamespace(content=f"async:{prompt}")

    def with_structured_output(self, schema):
        async def ainvoke(prompt):
            return schema(f"async:{prompt}")

        return SimpleNamespace(
            invoke=lambda prompt: schema(f"sync:{prompt}"), ainvoke=ainvoke
        )


class Urteil:
    def __init__(self, text):
        self.text = text


def _stub(name, **attribute):
    modul = ModuleType(name)
    modul.__dict__.update(attribute)
    return modul


@pytest.fixture
def judge(monkeypatch):
    monkeypatch.setitem(sys.modules, "deepeval", _stub("deepeval"))
    monkeypatch.setitem(
        sys.modules, "deepeval.models", _stub("deepeval.models", DeepEvalBaseLLM=object)
    )
    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        _stub("langchain_openai", ChatOpenAI=FakeChatOpenAI),
    )
    monkeypatch.delitem(sys.modules, "evaluation.judge", raising=False)
    modul = importlib.import_module("evaluation.judge")
    yield modul
    sys.modules.pop("evaluation.judge", None)


def test_judge_nimmt_konfiguration_und_deterministische_einstellungen(
    judge, monkeypatch
):
    monkeypatch.setattr(config, "JUDGE_MODELL", "judge-modell")
    monkeypatch.setattr(config, "JUDGE_BASE_URL", "http://judge:1234/v1")
    monkeypatch.setattr(config, "JUDGE_API_KEY", "geheim")
    monkeypatch.setattr(config, "JUDGE_TIMEOUT", 42.0)

    llm = judge.JudgeLLM()

    assert llm.load_model().kwargs == {
        "model": "judge-modell",
        "base_url": "http://judge:1234/v1",
        "api_key": "geheim",
        "temperature": 0,
        "timeout": 42.0,
        "max_retries": 1,
    }
    assert llm.get_model_name() == "judge:judge-modell"


def test_judge_explizites_modell_schlaegt_konfiguration(judge):
    llm = judge.JudgeLLM("anderes-modell")

    assert llm.load_model().kwargs["model"] == "anderes-modell"
    assert llm.get_model_name() == "judge:anderes-modell"


def test_generate_liefert_text_ohne_schema_und_objekt_mit_schema(judge):
    llm = judge.JudgeLLM("m")

    assert llm.generate("Prompt") == "sync:Prompt"
    urteil = llm.generate("Prompt", schema=Urteil)
    assert isinstance(urteil, Urteil)
    assert urteil.text == "sync:Prompt"


def test_a_generate_liefert_text_ohne_schema_und_objekt_mit_schema(judge):
    llm = judge.JudgeLLM("m")

    assert asyncio.run(llm.a_generate("Prompt")) == "async:Prompt"
    urteil = asyncio.run(llm.a_generate("Prompt", schema=Urteil))
    assert isinstance(urteil, Urteil)
    assert urteil.text == "async:Prompt"


def test_lokaler_judge_ist_alias(judge):
    assert judge.LokalerJudge is judge.JudgeLLM
