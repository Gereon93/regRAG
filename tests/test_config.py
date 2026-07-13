import importlib
import os

import pytest

import config


@pytest.fixture
def geladen(monkeypatch):
    def laden(**env):
        for name in list(os.environ):
            if name.startswith("REGRAG_"):
                monkeypatch.delenv(name, raising=False)
        for name, wert in env.items():
            monkeypatch.setenv(name, wert)
        return importlib.reload(config)

    yield laden
    importlib.reload(config)


def test_judge_erbt_app_endpunkt_ohne_eigene_werte(geladen):
    c = geladen(REGRAG_LLM_BASE_URL="http://lm:1234/v1", REGRAG_API_KEY="lm-studio")

    assert c.JUDGE_BASE_URL == "http://lm:1234/v1"
    assert c.JUDGE_API_KEY == "lm-studio"


def test_leere_judge_variablen_fallen_auf_app_endpunkt_zurueck(geladen):
    c = geladen(
        REGRAG_LLM_BASE_URL="http://lm:1234/v1",
        REGRAG_API_KEY="lm-studio",
        REGRAG_TIMEOUT="42",
        REGRAG_JUDGE_BASE_URL="",
        REGRAG_JUDGE_API_KEY="",
        REGRAG_JUDGE_MODELL="",
        REGRAG_JUDGE_TIMEOUT="",
    )

    assert c.JUDGE_BASE_URL == "http://lm:1234/v1"
    assert c.JUDGE_API_KEY == "lm-studio"
    assert c.JUDGE_MODELL
    assert c.JUDGE_TIMEOUT == 42.0


def test_gesetzte_judge_variablen_gewinnen(geladen):
    c = geladen(
        REGRAG_LLM_BASE_URL="http://lm:1234/v1",
        REGRAG_API_KEY="lm-studio",
        REGRAG_JUDGE_BASE_URL="https://openrouter.ai/api/v1",
        REGRAG_JUDGE_API_KEY="sk-or-test",
        REGRAG_JUDGE_MODELL="openai/gpt-5.4-mini",
        REGRAG_JUDGE_TIMEOUT="120",
    )

    assert c.JUDGE_BASE_URL == "https://openrouter.ai/api/v1"
    assert c.JUDGE_API_KEY == "sk-or-test"
    assert c.JUDGE_MODELL == "openai/gpt-5.4-mini"
    assert c.JUDGE_TIMEOUT == 120.0
