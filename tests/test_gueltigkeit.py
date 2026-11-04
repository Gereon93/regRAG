from datetime import date, datetime, timedelta, timezone

import pytest

import gueltigkeit
from gueltigkeit import (
    MetadatenFehler,
    heute_berlin,
    normalisiere,
    parse_datum,
    status_von,
)

STICHTAG = date(2026, 6, 15)


def test_parse_datum_nimmt_iso_datum():
    assert parse_datum("2026-02-28", "gueltig_ab") == date(2026, 2, 28)


@pytest.mark.parametrize(
    "wert",
    ["2026-02-30", "2026-1-1", "20260101", "2026-W01-1", "2026-01-01T00:00", "", "  ", 20260101, ["2026-01-01"], True],
)
def test_parse_datum_lehnt_kaputte_werte_mit_feldname_ab(wert):
    with pytest.raises(MetadatenFehler, match="gueltig_bis"):
        parse_datum(wert, "gueltig_bis")


def test_normalisiere_liefert_nur_gesetzte_felder():
    roh = {"titel": "X", "gueltig_ab": "2026-01-01", "gueltig_bis": None, "fassung": "  2. Fassung "}

    assert normalisiere(roh) == {"gueltig_ab": "2026-01-01", "fassung": "2. Fassung"}


def test_normalisiere_ohne_felder_ist_leer():
    assert normalisiere({"titel": "X"}) == {}


def test_normalisiere_erlaubt_gleiche_grenzen():
    roh = {"gueltig_ab": "2026-01-01", "gueltig_bis": "2026-01-01"}

    assert normalisiere(roh) == roh


def test_normalisiere_lehnt_ende_vor_beginn_ab():
    with pytest.raises(MetadatenFehler, match="gueltig_bis"):
        normalisiere({"gueltig_ab": "2026-01-02", "gueltig_bis": "2026-01-01"})


def test_fassung_mit_100_zeichen_ist_gueltig():
    assert normalisiere({"fassung": "x" * 100}) == {"fassung": "x" * 100}


@pytest.mark.parametrize("wert", ["x" * 101, "  ", "", 3, ["a"]])
def test_kaputte_fassung_wird_abgelehnt(wert):
    with pytest.raises(MetadatenFehler, match="fassung"):
        normalisiere({"fassung": wert})


@pytest.mark.parametrize(
    ("metadata", "erwartet"),
    [
        ({"gueltig_ab": "2026-06-15"}, "gueltig"),
        ({"gueltig_ab": "2026-06-16"}, "noch_nicht_gueltig"),
        ({"gueltig_bis": "2026-06-15"}, "gueltig"),
        ({"gueltig_bis": "2026-06-14"}, "abgelaufen"),
        ({"gueltig_ab": "2026-06-15", "gueltig_bis": "2026-06-15"}, "gueltig"),
        ({"fassung": "2. Fassung"}, "unbekannt"),
        ({}, "unbekannt"),
        ({"gueltig_ab": "kaputt"}, "metadaten_ungueltig"),
        ({"gueltig_bis": 7}, "metadaten_ungueltig"),
        ({"gueltig_ab": "2026-06-16", "gueltig_bis": "2026-06-14"}, "metadaten_ungueltig"),
    ],
)
def test_status_von_am_stichtag(metadata, erwartet):
    assert status_von(metadata, STICHTAG) == erwartet


@pytest.mark.parametrize(
    ("jetzt", "erwartet"),
    [
        (datetime(2026, 6, 30, 22, 30, tzinfo=timezone.utc), date(2026, 7, 1)),
        (datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc), date(2027, 1, 1)),
        (datetime(2026, 3, 29, 21, 59, tzinfo=timezone.utc), date(2026, 3, 29)),
        (datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc), date(2026, 3, 30)),
        (datetime(2026, 10, 25, 22, 59, tzinfo=timezone.utc), date(2026, 10, 25)),
        (datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc), date(2026, 10, 26)),
    ],
)
def test_heute_berlin_rechnet_in_berliner_kalendertagen(jetzt, erwartet):
    assert heute_berlin(jetzt) == erwartet


def test_heute_berlin_ist_prozessunabhaengig(monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Auckland")

    assert heute_berlin(datetime(2026, 6, 30, 22, 30, tzinfo=timezone.utc)) == date(2026, 7, 1)


def test_heute_berlin_nimmt_ohne_eingabe_die_aktuelle_uhr():
    grenze = datetime.now(timezone.utc).astimezone(gueltigkeit.BERLIN).date()

    assert heute_berlin() - grenze <= timedelta(days=1)
