import json
import logging
from types import SimpleNamespace

import pytest

from manifest import Manifest, dateiname_revision, lade_manifest, schreibe_manifest

EINSTELLUNGEN = SimpleNamespace(LAYA_MODELL="multilingual", MIN_RETRIEVAL_SCORE=0.62)


def _roh(**ueberschreibungen):
    daten = {
        "schema": 1,
        "checkpoint": {"modell": "multilingual", "revision": "abc123"},
        "eval_version": "sha256:0123456789abcdef0123",
        "min_retrieval_score": 0.62,
        "temperatur": 1.0,
        "schwellen": {"verordnung": 0.41, "gesetz": 0.3},
        "fallback_schwelle": 0.41,
        "kosten": {"falschantwort": 10.0, "eskalation": 1.0},
        "erzeugt": "2026-10-31T12:00:00+00:00",
    }
    daten.update(ueberschreibungen)
    return daten


def _schreibe(tmp_path, daten, text=None):
    datei = tmp_path / "m.json"
    datei.write_text(text if text is not None else json.dumps(daten), encoding="utf-8")
    return datei


def _manifest():
    return Manifest(
        checkpoint_modell="multilingual",
        checkpoint_revision="abc123",
        eval_version="sha256:0123456789abcdef0123",
        min_retrieval_score=0.62,
        temperatur=1.0,
        schwellen={"verordnung": 0.41},
        fallback_schwelle=0.41,
        kosten={"falschantwort": 10.0, "eskalation": 1.0},
        erzeugt="2026-10-31T12:00:00+00:00",
    )


def test_round_trip(tmp_path):
    datei = schreibe_manifest(_manifest(), tmp_path)
    assert datei.name == "gate-manifest-0123456789ab-abc123.json"
    assert lade_manifest(datei, EINSTELLUNGEN) == _manifest()


def test_zweite_revision_ueberschreibt_das_erste_manifest_nicht(tmp_path):
    """PR #43: Baseline und Kandidat auf demselben Eval-Set ersetzen sich nicht still."""
    erstes = schreibe_manifest(_manifest(), tmp_path)
    zweites = schreibe_manifest(Manifest(**{**_manifest().__dict__, "checkpoint_revision": "r/2"}), tmp_path)
    assert zweites.name == "gate-manifest-0123456789ab-r_2.json"
    assert erstes != zweites
    assert lade_manifest(erstes, EINSTELLUNGEN).checkpoint_revision == "abc123"
    assert lade_manifest(zweites, EINSTELLUNGEN).checkpoint_revision == "r/2"


def test_kollision_der_bereinigten_revision_bricht_ab_statt_zu_ueberschreiben(tmp_path):
    """PR #43: r/2 und r_2 bereinigen auf denselben Namen; die Baseline bleibt erhalten."""
    erstes = schreibe_manifest(Manifest(**{**_manifest().__dict__, "checkpoint_revision": "r/2"}), tmp_path)
    with pytest.raises(ValueError, match="r_2"):
        schreibe_manifest(Manifest(**{**_manifest().__dict__, "checkpoint_revision": "r_2"}), tmp_path)
    assert lade_manifest(erstes, EINSTELLUNGEN).checkpoint_revision == "r/2"


def test_gleiche_revision_darf_neu_geschrieben_werden(tmp_path):
    schreibe_manifest(_manifest(), tmp_path)
    neu = schreibe_manifest(Manifest(**{**_manifest().__dict__, "temperatur": 2.0}), tmp_path)
    assert lade_manifest(neu, EINSTELLUNGEN).temperatur == 2.0


def test_unlesbare_bestehende_datei_wird_nicht_ueberschrieben(tmp_path):
    (tmp_path / "gate-manifest-0123456789ab-abc123.json").write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ValueError, match="abc123"):
        schreibe_manifest(_manifest(), tmp_path)


def test_report_und_manifest_bereinigen_die_revision_gleich(tmp_path):
    from evaluation.eval_report import schreibe

    revision = "org/modell@" + "a" * 60
    assert dateiname_revision(revision) == "org_modell_" + "a" * 29
    bericht = {"eval_version": "sha256:" + "ab" * 32, "laya": {"checkpoint": {"revision": revision}}}
    assert schreibe(bericht, tmp_path / "r").name == f"bericht-abababababab-laya-{dateiname_revision(revision)}.json"
    manifest = Manifest(**{**_manifest().__dict__, "checkpoint_revision": revision})
    assert schreibe_manifest(manifest, tmp_path / "m").name == f"gate-manifest-0123456789ab-{dateiname_revision(revision)}.json"


def test_leeres_schwellen_erlaubt(tmp_path):
    m = lade_manifest(
        _schreibe(tmp_path, _roh(schwellen={}, fallback_schwelle=0.0)), EINSTELLUNGEN
    )
    assert m.schwelle_fuer("gesetz") == 0.0


def test_schwelle_fuer_fallback(tmp_path):
    m = lade_manifest(_schreibe(tmp_path, _roh()), EINSTELLUNGEN)
    assert m.schwelle_fuer("gesetz") == 0.3
    assert m.schwelle_fuer("unbekannt") == 0.41
    assert m.schwelle_fuer("richtlinie") == 0.41
    assert m.schwelle_fuer("irgendwas") == 0.41


def _ohne(feld):
    daten = _roh()
    del daten[feld]
    return daten


@pytest.mark.parametrize(
    ("daten", "feld"),
    [
        (_roh(schema=2), "schema"),
        (_ohne("temperatur"), "temperatur"),
        (_ohne("checkpoint"), "checkpoint"),
        (_roh(schwellen={"unbekannt": 0.4}), "schwellen"),
        (_roh(schwellen={"verordnung": 1.2}, fallback_schwelle=1.0), "schwellen"),
        (_roh(fallback_schwelle=1.2), "fallback_schwelle"),
        (_roh(fallback_schwelle=0.2), "fallback_schwelle"),
        (_roh(fallback_schwelle=True), "fallback_schwelle"),
        (_roh(temperatur=0.01), "temperatur"),
        (_roh(temperatur=21), "temperatur"),
        (_roh(temperatur=True), "temperatur"),
        (
            _roh(checkpoint={"modell": "anderes", "revision": "abc"}),
            "checkpoint.modell",
        ),
        (
            _roh(checkpoint={"modell": "multilingual", "revision": ""}),
            "checkpoint.revision",
        ),
        (_roh(min_retrieval_score=0.5), "min_retrieval_score"),
        (_roh(eval_version="md5:abc"), "eval_version"),
        (_roh(kosten=[1]), "kosten"),
        (_roh(erzeugt=5), "erzeugt"),
    ],
)
def test_verletzungen(tmp_path, daten, feld):
    with pytest.raises(ValueError, match=feld):
        lade_manifest(_schreibe(tmp_path, daten), EINSTELLUNGEN)


@pytest.mark.parametrize("konstante", ["Infinity", "-Infinity", "NaN"])
def test_nicht_endliche_json_konstanten(tmp_path, konstante):
    text = json.dumps(_roh()).replace('"temperatur": 1.0', f'"temperatur": {konstante}')
    with pytest.raises(ValueError, match=konstante):
        lade_manifest(_schreibe(tmp_path, None, text), EINSTELLUNGEN)


def test_fehlende_datei(tmp_path):
    with pytest.raises(ValueError, match="gibt-es-nicht"):
        lade_manifest(tmp_path / "gibt-es-nicht.json", EINSTELLUNGEN)


def test_kaputtes_json(tmp_path):
    with pytest.raises(ValueError, match="JSON"):
        lade_manifest(_schreibe(tmp_path, None, "{nope"), EINSTELLUNGEN)


def test_wurzel_kein_objekt(tmp_path):
    with pytest.raises(ValueError, match="Objekt"):
        lade_manifest(_schreibe(tmp_path, None, "[]"), EINSTELLUNGEN)


def test_schreiben_lehnt_nan_ab(tmp_path):
    m = _manifest()
    kaputt = Manifest(**{**m.__dict__, "temperatur": float("nan")})
    with pytest.raises(ValueError):
        schreibe_manifest(kaputt, tmp_path)


def test_info_log_ohne_pfad(tmp_path, caplog):
    datei = _schreibe(tmp_path, _roh())
    with caplog.at_level(logging.INFO, logger="manifest"):
        lade_manifest(datei, EINSTELLUNGEN)
    text = caplog.text
    assert "sha256:0123456789abcdef0123" in text
    assert "multilingual" in text
    assert "m.json" in text
    assert str(tmp_path) not in text


def _text_mit(ersetzung):
    text = json.dumps(_roh())
    for alt, neu in ersetzung.items():
        assert alt in text
        text = text.replace(alt, neu)
    return text


@pytest.mark.parametrize(
    ("pfad", "feld"),
    [
        (("schema",), "schema"),
        (("eval_version",), "eval_version"),
        (("min_retrieval_score",), "min_retrieval_score"),
        (("schwellen",), "schwellen"),
        (("fallback_schwelle",), "fallback_schwelle"),
        (("kosten",), "kosten"),
        (("erzeugt",), "erzeugt"),
        (("checkpoint", "modell"), "checkpoint.modell"),
        (("checkpoint", "revision"), "checkpoint.revision"),
    ],
)
def test_fehlendes_feld(tmp_path, pfad, feld):
    daten = _roh()
    ziel = daten
    for schluessel in pfad[:-1]:
        ziel = ziel[schluessel]
    del ziel[pfad[-1]]
    with pytest.raises(ValueError, match=feld):
        lade_manifest(_schreibe(tmp_path, daten), EINSTELLUNGEN)


@pytest.mark.parametrize(
    ("daten", "feld"),
    [
        (_roh(schwellen=[]), "schwellen"),
        (_roh(schwellen={"gesetz": "0.3"}), "schwellen"),
        (_roh(schwellen={"gesetz": -0.1}), "schwellen"),
        (_roh(schwellen={"gesetz": 1.0001}, fallback_schwelle=1.0), "schwellen"),
        (_roh(fallback_schwelle=-0.1), "fallback_schwelle"),
        (_roh(fallback_schwelle=1.0001), "fallback_schwelle"),
        (_roh(schema=True), "schema"),
        (_roh(kosten={"falschantwort": "10"}), "kosten"),
        (_roh(kosten={"falschantwort": True}), "kosten"),
        (_roh(temperatur=0.0499), "temperatur"),
        (_roh(temperatur=20.0001), "temperatur"),
    ],
)
def test_weitere_verletzungen(tmp_path, daten, feld):
    with pytest.raises(ValueError, match=feld):
        lade_manifest(_schreibe(tmp_path, daten), EINSTELLUNGEN)


@pytest.mark.parametrize("wurzel", ['"text"', "42", "null"])
def test_wurzel_nicht_objekt_andere(tmp_path, wurzel):
    with pytest.raises(ValueError, match="Objekt"):
        lade_manifest(_schreibe(tmp_path, None, wurzel), EINSTELLUNGEN)


@pytest.mark.parametrize(
    "ersetzung",
    [
        {'"temperatur": 1.0': '"temperatur": 1' + "0" * 400},
        {'"fallback_schwelle": 0.41': '"fallback_schwelle": 1' + "0" * 400},
        {'"gesetz": 0.3': '"gesetz": 1' + "0" * 400},
        {'"eskalation": 1.0': '"eskalation": 1' + "0" * 400},
        {'"gesetz": 0.3': '"gesetz": 1e999'},
        {'"eskalation": 1.0': '"eskalation": 1e999'},
    ],
)
def test_riesige_zahlen_sind_value_error(tmp_path, ersetzung):
    with pytest.raises(ValueError):
        lade_manifest(_schreibe(tmp_path, None, _text_mit(ersetzung)), EINSTELLUNGEN)


@pytest.mark.parametrize("konstante", ["Infinity", "-Infinity", "NaN"])
@pytest.mark.parametrize(
    "alt",
    ['"fallback_schwelle": 0.41', '"gesetz": 0.3', '"eskalation": 1.0'],
)
def test_nicht_endliche_konstanten_ueberall(tmp_path, alt, konstante):
    neu = alt.split(":")[0] + f": {konstante}"
    with pytest.raises(ValueError, match=konstante):
        lade_manifest(_schreibe(tmp_path, None, _text_mit({alt: neu})), EINSTELLUNGEN)


@pytest.mark.parametrize("temperatur", [0.05, 20, 20.0])
def test_temperatur_grenzen_gueltig(tmp_path, temperatur):
    assert (
        lade_manifest(
            _schreibe(tmp_path, _roh(temperatur=temperatur)), EINSTELLUNGEN
        ).temperatur
        == temperatur
    )


@pytest.mark.parametrize("wert", [0, 1, 0.0, 1.0])
def test_schwellen_grenzen_gueltig(tmp_path, wert):
    daten = _roh(schwellen={"gesetz": wert}, fallback_schwelle=1.0)
    assert lade_manifest(_schreibe(tmp_path, daten), EINSTELLUNGEN).schwellen == {
        "gesetz": wert
    }
    daten = _roh(schwellen={}, fallback_schwelle=wert)
    assert (
        lade_manifest(_schreibe(tmp_path, daten), EINSTELLUNGEN).fallback_schwelle
        == wert
    )
