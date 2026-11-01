import json
import logging
from types import SimpleNamespace

import pytest

from manifest import Manifest, lade_manifest, schreibe_manifest

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
    assert datei.name == "gate-manifest-0123456789ab.json"
    assert lade_manifest(datei, EINSTELLUNGEN) == _manifest()


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
