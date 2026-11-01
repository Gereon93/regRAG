import importlib
import os

import pytest

import config


@pytest.fixture(name="geladen")
def _geladen(monkeypatch):
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


def test_eskalationsdatei_liegt_standardmaessig_im_chroma_verzeichnis(geladen):
    c = geladen(REGRAG_CHROMA_DIR="/daten/chroma")

    assert c.ESKALATION_DATEI == "/daten/chroma/eskalationen.jsonl"


def test_eskalationsdatei_ist_per_env_ueberschreibbar(geladen):
    c = geladen(REGRAG_ESKALATION_DATEI="/tmp/e.jsonl")

    assert c.ESKALATION_DATEI == "/tmp/e.jsonl"


def test_laya_ist_standardmaessig_aus_mit_dokumentierten_defaults(geladen):
    c = geladen()

    assert c.LAYA_URL == ""
    assert c.LAYA_TIMEOUT == 30.0
    assert c.LAYA_MAX_LEN == 4096
    assert c.LAYA_LANG == "de"
    assert c.LAYA_MIN_ANSWERABLE == 0.5


def test_laya_url_aus_leerraum_gilt_als_aus(geladen):
    assert geladen(REGRAG_LAYA_URL="   ").LAYA_URL == ""


@pytest.mark.parametrize("wert", ["-0.1", "1.1"])
def test_laya_schwelle_ausserhalb_von_null_bis_eins_ist_ein_fehler(geladen, monkeypatch, wert):
    with pytest.raises(ValueError, match="MIN_ANSWERABLE"):
        geladen(REGRAG_LAYA_MIN_ANSWERABLE=wert)
    monkeypatch.delenv("REGRAG_LAYA_MIN_ANSWERABLE")


def test_laya_url_mit_zugangsdaten_wird_abgelehnt(geladen, monkeypatch):
    with pytest.raises(ValueError, match="Zugangsdaten"):
        geladen(REGRAG_LAYA_URL="http://user:geheim@laya:8000")
    monkeypatch.delenv("REGRAG_LAYA_URL")


def test_kosten_defaults_gewichten_falschantwort_hoeher(geladen):
    c = geladen()

    assert c.KOSTEN_FALSCHANTWORT > c.KOSTEN_ESKALATION > 0


def test_kosten_sind_ueber_umgebung_konfigurierbar(geladen):
    c = geladen(REGRAG_KOSTEN_FALSCHANTWORT="25", REGRAG_KOSTEN_ESKALATION="2")

    assert (c.KOSTEN_FALSCHANTWORT, c.KOSTEN_ESKALATION) == (25.0, 2.0)


@pytest.mark.parametrize(
    "falschantwort, eskalation",
    [("-1", "1"), ("1", "-1"), ("nan", "1"), ("inf", "1"), ("0", "0")],
)
def test_ungueltige_kosten_brechen_beim_import_ab(geladen, monkeypatch, falschantwort, eskalation):
    with pytest.raises(ValueError, match="REGRAG_KOSTEN"):
        geladen(REGRAG_KOSTEN_FALSCHANTWORT=falschantwort, REGRAG_KOSTEN_ESKALATION=eskalation)
    monkeypatch.delenv("REGRAG_KOSTEN_FALSCHANTWORT")
    monkeypatch.delenv("REGRAG_KOSTEN_ESKALATION")


def test_gate_manifest_und_laya_modell_defaults(geladen):
    c = geladen()
    assert c.GATE_MANIFEST == ""
    assert c.LAYA_MODELL == "multilingual"


def test_gate_manifest_und_laya_modell_werden_getrimmt(geladen):
    c = geladen(REGRAG_GATE_MANIFEST="  gate.json ", REGRAG_LAYA_MODELL=" mein-modell ")
    assert c.GATE_MANIFEST == "gate.json"
    assert c.LAYA_MODELL == "mein-modell"


@pytest.mark.parametrize("wert", ["", "   "])
def test_leeres_laya_modell_ist_ein_fehler(geladen, monkeypatch, wert):
    with pytest.raises(ValueError, match="LAYA_MODELL"):
        geladen(REGRAG_LAYA_MODELL=wert)
    monkeypatch.delenv("REGRAG_LAYA_MODELL")


def test_eskalationsgrenzen_haben_dokumentierte_defaults(geladen):
    c = geladen()

    assert c.ESKALATION_MAX_BYTES == 5 * 1024 * 1024
    assert c.ESKALATION_MAX_ARCHIVE == 5
    assert c.ESKALATION_AUFBEWAHRUNG_TAGE == 90


def test_eskalationsgrenzen_sind_per_env_ueberschreibbar(geladen):
    c = geladen(
        REGRAG_ESKALATION_MAX_BYTES="1000",
        REGRAG_ESKALATION_MAX_ARCHIVE="2",
        REGRAG_ESKALATION_AUFBEWAHRUNG_TAGE="7",
    )

    assert (c.ESKALATION_MAX_BYTES, c.ESKALATION_MAX_ARCHIVE, c.ESKALATION_AUFBEWAHRUNG_TAGE) == (1000, 2, 7)


def test_config_defaults_decken_sich_mit_den_gate_defaults(geladen):
    from gate import EskalationsGrenzen, grenzen_aus

    standard = EskalationsGrenzen()
    aus_config = grenzen_aus(geladen())

    assert vars(aus_config) == vars(standard)
